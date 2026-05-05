from __future__ import annotations

import re
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from src.agents.chat import respond_to_normal_chat
from src.agents.citation_builder import build_citations
from src.agents.clarifier import check_needs_clarification
from src.agents.classifier import classify_question
from src.agents.domain_guard import check_domain
from src.agents.empty_result_handler import handle_empty_result
from src.agents.kpi_router import route_to_kpi_procedure
from src.agents.query_resolver import FailureType, ResolutionStrategy, resolve_query
from src.agents.sql_explainer import explain_sql
from src.agents.sql_writer import write_sql_query
from src.agents.summarizer import summarize_sql_result, stream_summarize_sql_result
from src.agents.verifier import verify_answer
from src.business_context import BusinessContextStore, format_context_for_prompt
from src.config import get_settings
from src.db.execute import DatabaseExecutionError, execute_sql_query, execute_stored_procedure
from src.db.schema_cache import SchemaCache
from src.entity_search import (
    CategoryEntityResolver,
    CityEntityResolver,
    EntityMatch,
    StateEntityResolver,
    StoreNameEntityResolver,
    SubclassEntityResolver,
)
from src.llm import run_embedding
from src.memory import ConversationMemory, ThreadSummary
from src.mongo_memory import MongoConversationMemory
from src.models import BotReply, SqlExecutionResult
from src.sql_cache import SqlQueryCache, fingerprint_text, normalize_question_for_cache
from src.sql_guard import SqlGuardError, ensure_safe_readonly_sql
from src.tracing import current_trace_id
from src.visualization import VisualOutput, build_visual_output


class ChatOrchestrator:
    def __init__(
        self,
        schema_cache: SchemaCache | None = None,
        user_id: str | None = None,
    ) -> None:
        self.settings = get_settings()
        self.schema_cache = schema_cache or SchemaCache()
        self.context_store = BusinessContextStore(path=self.settings.business_context_path)
        # user_id comes from the authenticated request (email); falls back to
        # the MONGO_USER_ID env var (default "anonymous") when auth is off.
        effective_user_id = (user_id or self.settings.mongo_user_id).strip() or "anonymous"
        self._user_id = effective_user_id
        # ── Memory backend: MongoDB if MONGO_URI is set, else local JSON ──
        if self.settings.mongo_uri:
            self.memory = MongoConversationMemory(
                uri=self.settings.mongo_uri,
                db_name=self.settings.mongo_db_name,
                collection=self.settings.mongo_collection,
                max_turns=self.settings.memory_max_turns,
                default_thread_id=self.settings.memory_default_thread,
                auto_create_thread=self.settings.memory_auto_create_thread,
                user_id=effective_user_id,
            )
        else:
            self.memory = ConversationMemory(
                max_turns=self.settings.memory_max_turns,
                store_path=self.settings.memory_store_path,
                default_thread_id=self.settings.memory_default_thread,
            )
            if self.settings.memory_auto_create_thread:
                self.memory.create_thread(self._new_thread_id(), switch=True)

        self.sql_cache = (
            SqlQueryCache(path=self.settings.sql_cache_path)
            if self.settings.sql_cache_enabled
            else None
        )
        self.state_resolver = (
            StateEntityResolver(
                cache_path=self.settings.entity_state_cache_path,
                cache_ttl_seconds=self.settings.entity_state_cache_ttl_seconds,
                similarity_threshold=self.settings.entity_state_similarity_threshold,
                embedding_model=self.settings.embedding_model,
            )
            if self.settings.entity_search_enabled
            else None
        )
        self.city_resolver = (
            CityEntityResolver(
                cache_path=self.settings.entity_state_cache_path,
                cache_ttl_seconds=self.settings.entity_state_cache_ttl_seconds,
                similarity_threshold=self.settings.entity_state_similarity_threshold,
                embedding_model=self.settings.embedding_model,
            )
            if self.settings.entity_search_enabled
            else None
        )
        self.store_name_resolver = (
            StoreNameEntityResolver(
                cache_path=self.settings.entity_state_cache_path,
                cache_ttl_seconds=self.settings.entity_state_cache_ttl_seconds,
                similarity_threshold=self.settings.entity_state_similarity_threshold,
                embedding_model=self.settings.embedding_model,
            )
            if self.settings.entity_search_enabled
            else None
        )
        self.category_resolver = (
            CategoryEntityResolver(
                cache_path=self.settings.entity_state_cache_path,
                cache_ttl_seconds=self.settings.entity_state_cache_ttl_seconds,
                similarity_threshold=self.settings.entity_state_similarity_threshold,
                embedding_model=self.settings.embedding_model,
            )
            if self.settings.entity_search_enabled
            else None
        )
        self.subclass_resolver = (
            SubclassEntityResolver(
                cache_path=self.settings.entity_state_cache_path,
                cache_ttl_seconds=self.settings.entity_state_cache_ttl_seconds,
                similarity_threshold=self.settings.entity_state_similarity_threshold,
                embedding_model=self.settings.embedding_model,
            )
            if self.settings.entity_search_enabled
            else None
        )

        self.last_sql: str | None = None
        self.last_sql_cache_status: str | None = None
        self.last_entity_match: str | None = None
        self.last_resolver_explanation: str | None = None

    def refresh_schema(self) -> str:
        return self.schema_cache.refresh()

    def clear_memory(self) -> None:
        self.memory.clear()

    def show_memory(self) -> str:
        return self.memory.format_for_display()

    def active_thread(self) -> str:
        return self.memory.active_thread_id()

    def list_threads(self) -> list[ThreadSummary]:
        return self.memory.list_threads()

    @staticmethod
    def _reconstruct_chart_data(
        row_preview: list[dict[str, Any]],
        chart_type: str,
        title: str = "Chart",
    ) -> dict[str, Any] | None:
        """
        Build a ChartData-compatible dict from raw row_preview rows when the
        original chart_data was not persisted (legacy turns).
        Detects label column (first non-numeric key) and metric columns
        (all numeric keys).
        """
        if not row_preview:
            return None

        sample = row_preview[0]
        label_col: str | None = None
        metric_cols: list[str] = []

        for key, val in sample.items():
            if isinstance(val, Decimal):
                metric_cols.append(key)
            elif isinstance(val, (int, float)) and not isinstance(val, bool):
                metric_cols.append(key)
            elif label_col is None:
                label_col = key

        if label_col is None or not metric_cols:
            return None

        labels = [str(row.get(label_col, "")) for row in row_preview]

        AF_PALETTE = [
            "#dc2626", "#2563eb", "#10b981", "#f59e0b",
            "#8b5cf6", "#ec4899", "#14b8a6", "#f97316",
        ]

        def _to_num(v: Any) -> float:
            if isinstance(v, Decimal):
                return float(v)
            if isinstance(v, (int, float)):
                return v
            return 0.0

        datasets: list[dict[str, Any]] = []
        for i, col in enumerate(metric_cols):
            values = [_to_num(row.get(col, 0)) for row in row_preview]
            colour = AF_PALETTE[i % len(AF_PALETTE)]
            ds: dict[str, Any] = {
                "label": col,
                "data": values,
            }
            if chart_type == "pie":
                ds["backgroundColor"] = AF_PALETTE[: len(values)]
                ds["borderColor"] = AF_PALETTE[: len(values)]
            else:
                ds["backgroundColor"] = colour
                ds["borderColor"] = colour
            datasets.append(ds)

        # Build column / rows for table chart_type
        columns = list(sample.keys())
        rows = [[row.get(c) for c in columns] for row in row_preview]

        return {
            "chart_type": chart_type,
            "title": title,
            "labels": labels,
            "datasets": datasets,
            "columns": columns,
            "rows": rows,
        }

    def get_thread_messages(self, thread_id: str) -> list[dict[str, Any]]:
        """
        Return all turns for thread_id as a list of message dicts the frontend
        can render — each turn produces a user dict followed by a bot dict.
        """
        try:
            turns = self.memory.get_thread_turns(thread_id)
        except ValueError:
            return []

        messages: list[dict[str, Any]] = []
        for turn in turns:
            # User bubble
            messages.append({"role": "user", "text": turn.user})

            # Recover chart_data for legacy turns that stored only row_preview
            chart_data = turn.chart_data
            if chart_data is None and turn.row_preview and turn.chart_type:
                chart_data = self._reconstruct_chart_data(
                    turn.row_preview,
                    turn.chart_type,
                    title=f"{turn.chart_type.title()} Chart",
                )

            # Bot bubble — include all rich grounding data
            messages.append({
                "role": "bot",
                "text": turn.assistant,
                "route": turn.route,
                "sql_used": turn.sql_used,
                "sql_explanation": turn.sql_explanation,
                "citations": turn.citations or [],
                "verification": turn.verification,
                "chart_data": chart_data,
                "chart_type": turn.chart_type,
                "row_preview": turn.row_preview,
                "last_resolver_explanation": None,
            })
        return messages

    def create_thread(self, thread_id: str) -> str:
        return self.memory.create_thread(thread_id, switch=True)

    def switch_thread(self, thread_id: str) -> str:
        return self.memory.switch_thread(thread_id)

    def list_sql_cache_entries(self, limit: int = 20) -> list[dict[str, Any]]:
        if self.sql_cache is None:
            return []
        entries = self.sql_cache.list_recent_entries(limit=limit)
        result: list[dict[str, Any]] = []
        for entry in entries:
            result.append(
                {
                    "id": entry.entry_id,
                    "original_question": entry.original_question,
                    "normalized_question": entry.normalized_question,
                    "hit_count": entry.hit_count,
                    "created_at": entry.created_at,
                    "last_success_at": entry.last_success_at,
                    "embedding": entry.embedding,
                }
            )
        return result

    def clear_sql_cache(self) -> int:
        if self.sql_cache is None:
            return 0
        existing = self.sql_cache.list_recent_entries(limit=1_000_000)
        self.sql_cache.clear_all()
        return len(existing)

    def handle_user_message(self, message: str) -> BotReply:
        cleaned = message.strip()
        if not cleaned:
            return BotReply(route="normal_chat", answer_text="Please enter a question.")

        classifier_input = self._build_contextual_input(cleaned)
        try:
            classification = classify_question(classifier_input)
        except Exception as exc:
            reply = BotReply(
                route="normal_chat",
                answer_text=f"I could not reach the LLM service: {exc}",
            )
            self.memory.add_turn(user=cleaned, assistant=reply.answer_text, route=reply.route)
            return reply

        if classification.label == "normal_chat":
            self.last_sql_cache_status = None
            answer = respond_to_normal_chat(self._build_contextual_input(cleaned))
            reply = BotReply(route="normal_chat", answer_text=answer)
            self.memory.add_turn(user=cleaned, assistant=reply.answer_text, route=reply.route)
            return reply

        # ── Domain Guard — block questions unrelated to Arvind Fashions ──
        domain = check_domain(self._build_contextual_input(cleaned))
        if not domain.in_scope:
            reply = BotReply(
                route="normal_chat",
                answer_text=domain.rejection_message,
            )
            self.memory.add_turn(user=cleaned, assistant=reply.answer_text, route="normal_chat")
            return reply

        return self._handle_business_question(cleaned)

    # ── Streaming entry point ─────────────────────────────────────────────────
    def stream_handle_user_message(
        self, message: str
    ) -> Generator[dict[str, Any], None, None]:
        """
        Streaming variant of handle_user_message.

        Yields SSE-style dicts:
          {"type": "start"}                          – pipeline running, SQL ready
          {"type": "token",    "content": "..."}     – answer text chunk
          {"type": "metadata", ...full reply data}   – citations, SQL, chart etc.
          {"type": "error",    "content": "..."}     – something went wrong

        Non-business routes (normal_chat / domain block / clarification) yield a
        single {"type": "complete", "content": "..."} and finish.
        """
        cleaned = message.strip()
        if not cleaned:
            yield {"type": "complete", "content": "Please enter a question."}
            return

        # ── Langfuse trace — one trace per user request ──────────────────────
        from src.tracing import current_trace_id, get_langfuse, set_trace_context
        _lf = get_langfuse()
        if _lf:
            try:
                _trace = _lf.trace(
                    name="chat_request",
                    user_id=self._user_id,
                    input=cleaned,
                    tags=["chat"],
                )
                set_trace_context(_trace.id, self._user_id)
            except Exception:
                pass  # tracing is non-fatal

        # ── Classify ────────────────────────────────────────────────────────
        classifier_input = self._build_contextual_input(cleaned)
        try:
            classification = classify_question(classifier_input)
        except Exception as exc:
            msg = f"I could not reach the LLM service: {exc}"
            self.memory.add_turn(user=cleaned, assistant=msg, route="normal_chat")
            yield {"type": "error", "content": msg}
            return

        # ── Normal chat ─────────────────────────────────────────────────────
        if classification.label == "normal_chat":
            answer = respond_to_normal_chat(self._build_contextual_input(cleaned))
            self.memory.add_turn(user=cleaned, assistant=answer, route="normal_chat")
            yield {"type": "complete", "content": answer, "trace_id": current_trace_id()}
            return

        # ── Domain guard ────────────────────────────────────────────────────
        domain = check_domain(self._build_contextual_input(cleaned))
        if not domain.in_scope:
            self.memory.add_turn(
                user=cleaned, assistant=domain.rejection_message, route="normal_chat"
            )
            yield {
                "type": "complete",
                "content": domain.rejection_message,
                "trace_id": current_trace_id(),
            }
            return

        # ── Business question ────────────────────────────────────────────────
        yield from self._stream_business_question(cleaned)

    def _stream_business_question(
        self, question: str
    ) -> Generator[dict[str, Any], None, None]:
        self.last_sql_cache_status = None
        self.last_entity_match = None
        self.last_resolver_explanation = None

        try:
            schema_context = self.schema_cache.get_schema_context()
            context = self.context_store.get_context()
            context_prompt = format_context_for_prompt(context)

            # ── Chart retype short-circuit ───────────────────────────────────
            # e.g. "give pie chart" / "show as bar" — no new SQL needed
            chart_retype = self._is_chart_retype_request(question)
            if chart_retype is not None:
                events = list(self._stream_chart_retype(question, chart_retype))
                if events:          # only short-circuit if we had prior data
                    yield from events
                    return
            # ────────────────────────────────────────────────────────────────

            # ── KPI stored-procedure router ──────────────────────────────────
            # Detect registered KPI questions (ABV, etc.) and execute via SP
            if context.kpi_procedures:
                question_with_memory_kpi = self._build_contextual_input(question)
                kpi_result = route_to_kpi_procedure(
                    question_with_memory_kpi, context.kpi_procedures
                )
                if kpi_result is not None:
                    yield from self._stream_kpi_procedure(question, kpi_result, context_prompt)
                    return
            # ────────────────────────────────────────────────────────────────

            # Clarification check
            question_with_memory = self._build_contextual_input(question)
            clarification = check_needs_clarification(question_with_memory, context_prompt)
            if clarification.needs_clarification and clarification.clarifying_question.strip():
                msg = clarification.clarifying_question.strip()
                self.memory.add_turn(user=question, assistant=msg, route="normal_chat")
                yield {"type": "complete", "content": msg, "trace_id": current_trace_id()}
                return

            # SQL resolution
            schema_fingerprint = fingerprint_text(schema_context)
            business_fingerprint = fingerprint_text(context_prompt)
            normalized_question = normalize_question_for_cache(question, context)
            entity_hint = self._resolve_location_hints(question)
            question_with_memory = self._build_contextual_input(question, entity_hint=entity_hint)

            sql, question_embedding = self._resolve_sql(
                question_with_memory=question_with_memory,
                schema_context=schema_context,
                context_prompt=context_prompt,
                normalized_question=normalized_question,
                schema_fingerprint=schema_fingerprint,
                business_fingerprint=business_fingerprint,
            )
            sql, execution_result = self._execute_with_recovery(
                user_question=question_with_memory,
                sql=sql,
                schema_context=schema_context,
                context_prompt=context_prompt,
            )
            self.last_sql = sql

            # Empty result — no streaming needed
            if execution_result.row_count == 0:
                follow_up = handle_empty_result(
                    question, sql, context_prompt, entity_match=self.last_entity_match
                )
                self.memory.add_turn(
                    user=question, assistant=follow_up,
                    route="business_question", sql_used=sql,
                )
                yield {
                    "type": "complete",
                    "content": follow_up,
                    "sql_used": sql,
                    "trace_id": current_trace_id(),
                }
                return

            # Signal to frontend: SQL done, answer streaming begins
            yield {"type": "start", "sql_used": sql}

            # Start background tasks: visualise + explain SQL
            executor = ThreadPoolExecutor(max_workers=2)
            visuals_future: Future = executor.submit(
                self._build_visual_output, question, execution_result
            )
            explain_future: Future = executor.submit(explain_sql, sql)

            # Stream the summariser — collect chunks + forward to client
            answer_parts: list[str] = []
            try:
                for chunk in stream_summarize_sql_result(
                    question_with_memory, sql, execution_result, context_prompt
                ):
                    answer_parts.append(chunk)
                    yield {"type": "token", "content": chunk}
            except Exception as exc:
                executor.shutdown(wait=False)
                err = f"I could not complete the summary: {exc}"
                self.memory.add_turn(user=question, assistant=err, route="business_question")
                yield {"type": "error", "content": err}
                return

            answer = "".join(answer_parts)

            # Collect background tasks
            try:
                visuals = visuals_future.result()
            except Exception:
                visuals = VisualOutput(
                    row_preview=self._build_preview(execution_result),
                    reason="visualization fallback to table preview",
                )
            try:
                sql_explanation = explain_future.result() or None
            except Exception:
                sql_explanation = None
            executor.shutdown(wait=False)

            # Phase 2: verify + cite (need the full answer text)
            with ThreadPoolExecutor(max_workers=2) as ex2:
                verify_future = ex2.submit(verify_answer, answer, execution_result)
                cite_future   = ex2.submit(build_citations, answer, execution_result)
                try:
                    verification = verify_future.result()
                except Exception:
                    verification = None
                try:
                    citations = cite_future.result()
                except Exception:
                    citations = []

            # SQL cache upsert
            if self.sql_cache is not None:
                if question_embedding is None and self.settings.sql_cache_semantic_enabled:
                    question_embedding = self._compute_embedding(normalized_question)
                self.sql_cache.upsert(
                    normalized_question=normalized_question,
                    original_question=question,
                    sql_text=sql,
                    schema_fingerprint=schema_fingerprint,
                    business_fingerprint=business_fingerprint,
                    question_embedding=question_embedding,
                    used_columns=execution_result.columns,
                )

            # Serialise chart_data once — used by both memory and metadata event
            chart_data_dict = None
            if visuals.chart_data:
                from dataclasses import asdict as _asdict
                chart_data_dict = _asdict(visuals.chart_data)

            # Save to memory
            self.memory.add_turn(
                user=question,
                assistant=answer,
                route="business_question",
                sql_used=sql,
                sql_explanation=sql_explanation,
                citations=[c.model_dump() for c in citations],
                verification=verification.model_dump() if verification else None,
                chart_data=chart_data_dict,
                chart_type=visuals.chart_type,
                row_preview=visuals.row_preview,
            )

            from src.tracing import current_trace_id, get_langfuse
            _tid = current_trace_id()
            # Update Langfuse trace with the final answer
            _lf2 = get_langfuse()
            if _lf2 and _tid:
                try:
                    _lf2.trace(id=_tid, output=answer)
                    _lf2.flush()
                except Exception:
                    pass

            yield {
                "type": "metadata",
                "sql_used": sql,
                "sql_explanation": sql_explanation,
                "chart_data": chart_data_dict,
                "chart_type": visuals.chart_type,
                "row_preview": visuals.row_preview,
                "citations": [c.model_dump() for c in citations],
                "verification": verification.model_dump() if verification else None,
                "last_resolver_explanation": self.last_resolver_explanation,
                "cache_status": self.last_sql_cache_status,
                "trace_id": _tid,
            }

        except SqlGuardError as exc:
            msg = f"I cannot run that query safely: {exc}"
            self.memory.add_turn(user=question, assistant=msg, route="business_question")
            yield {"type": "error", "content": msg}
        except DatabaseExecutionError as exc:
            msg = f"I hit a database error while running the query: {exc}"
            self.memory.add_turn(user=question, assistant=msg, route="business_question")
            yield {"type": "error", "content": msg}
        except Exception as exc:
            msg = f"I could not complete the SQL workflow: {exc}"
            self.memory.add_turn(user=question, assistant=msg, route="business_question")
            yield {"type": "error", "content": msg}

    def _handle_business_question(self, question: str) -> BotReply:
        self.last_sql_cache_status = None
        self.last_entity_match = None
        try:
            schema_context = self.schema_cache.get_schema_context()
            context = self.context_store.get_context()
            context_prompt = format_context_for_prompt(context)

            question_with_memory = self._build_contextual_input(question)
            clarification = check_needs_clarification(question_with_memory, context_prompt)
            if clarification.needs_clarification and clarification.clarifying_question.strip():
                reply = BotReply(
                    route="normal_chat",
                    answer_text=clarification.clarifying_question.strip(),
                )
                self.memory.add_turn(user=question, assistant=reply.answer_text, route="normal_chat")
                return reply

            schema_fingerprint = fingerprint_text(schema_context)
            business_fingerprint = fingerprint_text(context_prompt)
            normalized_question = normalize_question_for_cache(question, context)
            entity_hint = self._resolve_location_hints(question)
            question_with_memory = self._build_contextual_input(
                question,
                entity_hint=entity_hint,
            )

            sql, question_embedding = self._resolve_sql(
                question_with_memory=question_with_memory,
                schema_context=schema_context,
                context_prompt=context_prompt,
                normalized_question=normalized_question,
                schema_fingerprint=schema_fingerprint,
                business_fingerprint=business_fingerprint,
            )
            sql, execution_result = self._execute_with_recovery(
                user_question=question_with_memory,
                sql=sql,
                schema_context=schema_context,
                context_prompt=context_prompt,
            )
            self.last_sql = sql

            if execution_result.row_count == 0:
                follow_up = handle_empty_result(
                    question, sql, context_prompt, entity_match=self.last_entity_match
                )
                reply = BotReply(
                    route="business_question",
                    answer_text=follow_up,
                    sql_used=sql,
                )
                self.memory.add_turn(
                    user=question,
                    assistant=reply.answer_text,
                    route="business_question",
                    sql_used=sql,
                )
                return reply

            # ── Phase 1: summarise + visualise + explain SQL in parallel ────
            with ThreadPoolExecutor(max_workers=3) as executor:
                summary_future = executor.submit(
                    summarize_sql_result,
                    question_with_memory,
                    sql,
                    execution_result,
                    context_prompt,
                )
                visuals_future = executor.submit(
                    self._build_visual_output,
                    question,
                    execution_result,
                )
                explain_future = executor.submit(explain_sql, sql)

                answer = summary_future.result()
                try:
                    visuals = visuals_future.result()
                except Exception:
                    visuals = VisualOutput(
                        row_preview=self._build_preview(execution_result),
                        reason="visualization fallback to table preview",
                    )
                try:
                    sql_explanation = explain_future.result() or None
                except Exception:
                    sql_explanation = None

            # ── Phase 2: verify + cite in parallel (needs answer from phase 1) ──
            with ThreadPoolExecutor(max_workers=2) as executor:
                verify_future = executor.submit(verify_answer, answer, execution_result)
                cite_future   = executor.submit(build_citations, answer, execution_result)
                try:
                    verification = verify_future.result()
                except Exception:
                    verification = None
                try:
                    citations = cite_future.result()
                except Exception:
                    citations = []

            if self.sql_cache is not None:
                if question_embedding is None and self.settings.sql_cache_semantic_enabled:
                    question_embedding = self._compute_embedding(normalized_question)
                self.sql_cache.upsert(
                    normalized_question=normalized_question,
                    original_question=question,
                    sql_text=sql,
                    schema_fingerprint=schema_fingerprint,
                    business_fingerprint=business_fingerprint,
                    question_embedding=question_embedding,
                    used_columns=execution_result.columns,
                )

            # Convert ChartData dataclass → Pydantic model for BotReply
            chart_data_model = None
            if visuals.chart_data:
                from src.models import ChartDataModel
                from dataclasses import asdict
                chart_data_model = ChartDataModel(**asdict(visuals.chart_data))

            reply = BotReply(
                route="business_question",
                answer_text=answer,
                sql_used=sql,
                sql_explanation=sql_explanation,
                row_preview=visuals.row_preview,
                chart_data=chart_data_model,
                chart_type=visuals.chart_type,
                visualization_reason=visuals.reason,
                citations=citations,
                verification=verification,
            )
            chart_data_dict = None
            if visuals.chart_data:
                from dataclasses import asdict as _asdict
                chart_data_dict = _asdict(visuals.chart_data)

            self.memory.add_turn(
                user=question,
                assistant=reply.answer_text,
                route=reply.route,
                sql_used=sql,
                sql_explanation=sql_explanation,
                citations=[c.model_dump() for c in citations],
                verification=verification.model_dump() if verification else None,
                chart_data=chart_data_dict,
                chart_type=visuals.chart_type,
                row_preview=visuals.row_preview,
            )
            return reply
        except SqlGuardError as exc:
            reply = BotReply(
                route="business_question",
                answer_text=f"I cannot run that query safely: {exc}",
            )
            self.memory.add_turn(user=question, assistant=reply.answer_text, route=reply.route)
            return reply
        except DatabaseExecutionError as exc:
            reply = BotReply(
                route="business_question",
                answer_text=f"I hit a database error while running the query: {exc}",
            )
            self.memory.add_turn(user=question, assistant=reply.answer_text, route=reply.route)
            return reply
        except Exception as exc:  # pragma: no cover - runtime safeguard
            reply = BotReply(
                route="business_question",
                answer_text=f"I could not complete the SQL workflow: {exc}",
            )
            self.memory.add_turn(user=question, assistant=reply.answer_text, route=reply.route)
            return reply

    # ── Chart-retype keywords ─────────────────────────────────────────────────
    _CHART_RETYPE_RE = re.compile(
        r"\b(pie|bar|line|table|donut|doughnut|column|area|horizontal)\b",
        re.IGNORECASE,
    )
    # Words that signal a genuinely new data question — skip retype shortcut
    _DATA_KEYWORDS_RE = re.compile(
        r"\b(top|bottom|last|this|previous|month|week|year|quarter|day|"
        r"yesterday|today|sales|revenue|units|growth|brand|store|region|"
        r"category|compare|vs|versus|between|from|to|in|by|per|average|"
        r"total|sum|count|rank|highest|lowest|trend)\b",
        re.IGNORECASE,
    )

    @staticmethod
    def _is_chart_retype_request(question: str) -> str | None:
        """
        Return the requested chart type if the question is *only* a chart format
        change (e.g. "give pie chart", "show as bar", "as a table please").
        Returns None if the question seems to ask for new/different data.
        """
        q = question.strip()
        # Must be short (≤10 words) to avoid catching real queries that mention chart types
        if len(q.split()) > 10:
            return None
        chart_match = ChatOrchestrator._CHART_RETYPE_RE.search(q)
        if not chart_match:
            return None
        # Reject if any data keyword is also present — that means it's a new query
        if ChatOrchestrator._DATA_KEYWORDS_RE.search(q):
            return None
        chart_type = chart_match.group(1).lower()
        # Normalise aliases
        if chart_type in ("doughnut", "donut"):
            chart_type = "pie"
        if chart_type == "column":
            chart_type = "bar"
        return chart_type

    def _stream_kpi_procedure(
        self,
        question: str,
        kpi_result: Any,          # KpiRouteResult from kpi_router
        context_prompt: str,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Execute a registered KPI stored procedure and stream the response.

        Mirrors the tail of ``_stream_business_question`` but:
        - Calls ``execute_stored_procedure`` instead of running ad-hoc SQL.
        - Uses ``kpi_result.exec_sql`` as the ``sql_used`` display string.
        - Never writes to the SQL cache (the SP is already the canonical query).
        """
        exec_sql = kpi_result.exec_sql  # human-readable EXEC … string

        try:
            execution_result = execute_stored_procedure(
                procedure_name=kpi_result.procedure,
                params=kpi_result.parameters,
            )
        except DatabaseExecutionError as exc:
            msg = f"I hit a database error running the {kpi_result.kpi} procedure: {exc}"
            self.memory.add_turn(user=question, assistant=msg, route="business_question", sql_used=exec_sql)
            yield {"type": "error", "content": msg}
            return

        if execution_result.row_count == 0:
            follow_up = handle_empty_result(question, exec_sql, context_prompt)
            self.memory.add_turn(
                user=question, assistant=follow_up,
                route="business_question", sql_used=exec_sql,
            )
            yield {"type": "complete", "content": follow_up, "sql_used": exec_sql}
            return

        yield {"type": "start", "sql_used": exec_sql}

        # Background: visualise + explain SQL (the EXEC string as proxy)
        executor = ThreadPoolExecutor(max_workers=2)
        visuals_future: Future = executor.submit(
            self._build_visual_output, question, execution_result
        )
        explain_future: Future = executor.submit(explain_sql, exec_sql)

        # Stream summariser
        answer_parts: list[str] = []
        try:
            for chunk in stream_summarize_sql_result(
                self._build_contextual_input(question),
                exec_sql,
                execution_result,
                context_prompt,
            ):
                answer_parts.append(chunk)
                yield {"type": "token", "content": chunk}
        except Exception as exc:
            executor.shutdown(wait=False)
            err = f"I could not complete the {kpi_result.kpi} summary: {exc}"
            self.memory.add_turn(user=question, assistant=err, route="business_question")
            yield {"type": "error", "content": err}
            return

        answer = "".join(answer_parts)

        try:
            visuals = visuals_future.result()
        except Exception:
            visuals = VisualOutput(
                row_preview=self._build_preview(execution_result),
                reason="visualization fallback to table preview",
            )
        try:
            sql_explanation = explain_future.result() or None
        except Exception:
            sql_explanation = None
        executor.shutdown(wait=False)

        # Phase 2: verify + cite
        with ThreadPoolExecutor(max_workers=2) as ex2:
            verify_future = ex2.submit(verify_answer, answer, execution_result)
            cite_future   = ex2.submit(build_citations, answer, execution_result)
            try:
                verification = verify_future.result()
            except Exception:
                verification = None
            try:
                citations = cite_future.result()
            except Exception:
                citations = []

        chart_data_dict = None
        if visuals.chart_data:
            from dataclasses import asdict as _asdict
            chart_data_dict = _asdict(visuals.chart_data)

        self.memory.add_turn(
            user=question,
            assistant=answer,
            route="business_question",
            sql_used=exec_sql,
            sql_explanation=sql_explanation,
            citations=[c.model_dump() for c in citations],
            verification=verification.model_dump() if verification else None,
            chart_data=chart_data_dict,
            chart_type=visuals.chart_type,
            row_preview=visuals.row_preview,
        )

        yield {
            "type": "metadata",
            "sql_used": exec_sql,
            "sql_explanation": sql_explanation,
            "chart_data": chart_data_dict,
            "chart_type": visuals.chart_type,
            "row_preview": visuals.row_preview,
            "citations": [c.model_dump() for c in citations],
            "verification": verification.model_dump() if verification else None,
            "last_resolver_explanation": None,
            "cache_status": f"kpi:{kpi_result.kpi}",
            "trace_id": current_trace_id(),
        }

    def _stream_chart_retype(
        self,
        question: str,
        chart_type: str,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Fast path: reuse the last business turn's row_preview with a new chart type.
        No SQL is run, no cache entry is written.
        """
        last_turn = self.memory.get_last_business_turn()
        if last_turn is None or not last_turn.row_preview:
            # No previous data to re-render — fall through to normal flow
            return

        chart_data_dict = self._reconstruct_chart_data(
            last_turn.row_preview,
            chart_type,
            title=f"{chart_type.title()} Chart",
        )

        answer = last_turn.assistant  # reuse the previous answer text

        self.memory.add_turn(
            user=question,
            assistant=answer,
            route="business_question",
            sql_used=last_turn.sql_used,
            sql_explanation=last_turn.sql_explanation,
            citations=last_turn.citations,
            verification=last_turn.verification,
            chart_data=chart_data_dict,
            chart_type=chart_type,
            row_preview=last_turn.row_preview,
        )

        yield {
            "type": "metadata",
            "answer": answer,
            "sql_used": last_turn.sql_used,
            "sql_explanation": last_turn.sql_explanation,
            "citations": last_turn.citations or [],
            "verification": last_turn.verification,
            "chart_data": chart_data_dict,
            "chart_type": chart_type,
            "row_preview": last_turn.row_preview,
            "cache_status": "chart_retype",
            "trace_id": current_trace_id(),
        }

    def _resolve_sql(
        self,
        *,
        question_with_memory: str,
        schema_context: str,
        context_prompt: str,
        normalized_question: str,
        schema_fingerprint: str,
        business_fingerprint: str,
    ) -> tuple[str, list[float] | None]:
        embedding: list[float] | None = None

        if self.sql_cache is not None:
            exact_hit = self.sql_cache.find_exact(
                normalized_question=normalized_question,
                schema_fingerprint=schema_fingerprint,
                business_fingerprint=business_fingerprint,
            )
            if exact_hit is not None:
                try:
                    ensure_safe_readonly_sql(exact_hit.sql_text)
                    self.last_sql_cache_status = "exact"
                    return exact_hit.sql_text, embedding
                except SqlGuardError:
                    pass

            if self.settings.sql_cache_semantic_enabled:
                embedding = self._compute_embedding(normalized_question)
                if embedding:
                    semantic_hit = self.sql_cache.find_semantic(
                        embedding_vector=embedding,
                        schema_fingerprint=schema_fingerprint,
                        business_fingerprint=business_fingerprint,
                        min_similarity=self.settings.sql_cache_similarity_threshold,
                    )
                    if semantic_hit is not None:
                        try:
                            ensure_safe_readonly_sql(semantic_hit.sql_text)
                            score = semantic_hit.similarity or 0.0
                            self.last_sql_cache_status = f"semantic:{score:.3f}"
                            return semantic_hit.sql_text, embedding
                        except SqlGuardError:
                            pass

        self.last_sql_cache_status = None
        sql = write_sql_query(question_with_memory, schema_context, context_prompt)
        return sql, embedding

    def _compute_embedding(self, normalized_question: str) -> list[float] | None:
        if not self.settings.embedding_model:
            return None
        if not normalized_question.strip():
            return None
        try:
            return run_embedding(
                text=normalized_question,
                model=self.settings.embedding_model,
            )
        except Exception:
            return None

    def _execute_with_recovery(
        self,
        *,
        user_question: str,
        sql: str,
        schema_context: str,
        context_prompt: str,
    ) -> tuple[str, SqlExecutionResult]:
        """
        Execute SQL with intelligent query resolution on failure.

        On each failure the QueryResolver diagnoses the root cause, picks the
        best strategy (syntax fix, column fix, broaden, simplify, safe rewrite,
        or full rewrite) and returns corrected SQL to retry.  Retries up to
        sql_debug_max_retries times before raising.
        """
        retries = max(0, int(self.settings.sql_debug_max_retries))
        current_sql = sql
        self.last_resolver_explanation: str | None = None

        for attempt in range(retries + 1):
            try:
                result = execute_sql_query(
                    current_sql,
                    max_rows=self.settings.max_result_rows,
                )
                return current_sql, result

            except SqlGuardError as exc:
                if attempt >= retries:
                    raise
                failure_type = FailureType.GUARD_ERROR
                error_message = str(exc)

            except DatabaseExecutionError as exc:
                if attempt >= retries:
                    raise
                failure_type = FailureType.DB_ERROR
                error_message = str(exc)

            # ── Resolver ──────────────────────────────────────────────────
            resolution = resolve_query(
                user_question=user_question,
                failing_sql=current_sql,
                failure_type=failure_type,
                error_message=error_message,
                schema_context=schema_context,
                business_context=context_prompt,
                attempt=attempt + 1,
            )

            self.last_resolver_explanation = (
                f"[{resolution.strategy.value}] {resolution.explanation}"
            )

            if resolution.strategy == ResolutionStrategy.GIVE_UP or not resolution.sql.strip():
                # Resolver gave up — surface explanation as a DatabaseExecutionError
                raise DatabaseExecutionError(
                    resolution.explanation or "Query could not be resolved automatically."
                )

            current_sql = resolution.sql.strip()

        raise DatabaseExecutionError("Failed to execute SQL after resolution attempts.")

    def _build_preview(self, result: SqlExecutionResult) -> list[dict[str, Any]]:
        max_preview = self.settings.preview_rows
        preview_rows: list[dict[str, Any]] = []

        for row in result.rows[:max_preview]:
            preview_rows.append(dict(zip(result.columns, row, strict=False)))

        return preview_rows

    def _build_visual_output(self, question: str, result: SqlExecutionResult) -> VisualOutput:
        return build_visual_output(
            question=question,
            result=result,
            preview_rows=self.settings.preview_rows,
            output_dir=self.settings.chart_output_dir,
            theme_path=self.settings.chart_theme_path,
            chart_enabled=self.settings.visualization_enabled,
            chart_max_points=self.settings.chart_max_points,
        )

    def _build_contextual_input(
        self,
        current_user_message: str,
        *,
        entity_hint: str | None = None,
    ) -> str:
        history = self.memory.format_for_prompt()
        hint_block = ""
        if entity_hint:
            hint_block = f"\nResolved entities:\n{entity_hint}\n"
        return (
            f"Conversation thread: {self.memory.active_thread_id()}\n"
            "Conversation history:\n"
            f"{history}\n\n"
            f"{hint_block}"
            "Current user message:\n"
            f"{current_user_message}"
        )

    def _resolve_location_hints(self, question: str) -> str | None:
        resolvers = [
            resolver
            for resolver in (
                self.state_resolver,
                self.city_resolver,
                self.store_name_resolver,
                self.category_resolver,
                self.subclass_resolver,
            )
            if resolver
        ]
        if not resolvers:
            return None

        matches: list[EntityMatch] = []
        for resolver in resolvers:
            match = resolver.resolve(question)
            if match is not None:
                matches.append(match)

        if not matches:
            self.last_entity_match = None
            return None

        self.last_entity_match = "; ".join(
            f"{match.column}={match.value} ({match.score:.3f}, source={match.source})"
            for match in matches
        )

        hint_lines = ["Candidate entity matches:"]
        for match in matches:
            hint_lines.append(f"- column: {match.column}")
            hint_lines.append(f"- value: {match.value}")
            hint_lines.append(f"- confidence: {match.score:.3f}")
            hint_lines.append(f"- source: {match.source}")

        return (
            "\n".join(hint_lines)
            + "\nIf user intent implies an entity filter, prefer these CITY/STATE/STORE_NAME values."
        )

    def _new_thread_id(self) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        suffix = uuid4().hex[:8]
        return f"conv-{ts}-{suffix}"

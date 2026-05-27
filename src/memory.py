from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

_VALID_THREAD_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_ROUTES = {"business_question", "normal_chat"}


def _json_default(obj: Any) -> Any:
    """Serialise types that standard json cannot handle."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return str(obj)


@dataclass
class MemoryTurn:
    user: str
    assistant: str
    route: Literal["business_question", "normal_chat"]
    # ── Rich grounding fields (business_question only, all optional) ──
    sql_used: str | None = None
    sql_explanation: str | None = None
    citations: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] | None = None   # {verified, issues:[]}
    chart_data: dict[str, Any] | None = None     # ChartData serialised as dict
    chart_type: str | None = None
    row_preview: list[dict[str, Any]] | None = None
    trace_id: str | None = None
    # legacy — kept for backward compat when reading old JSON files
    chart_path: str | None = None


@dataclass
class ThreadSummary:
    thread_id: str
    turn_count: int
    is_active: bool


class ConversationMemory:
    def __init__(
        self,
        max_turns: int = 12,
        *,
        store_path: Path | None = None,
        default_thread_id: str = "default",
    ) -> None:
        self._max_turns = max(1, int(max_turns))
        self._store_path = store_path
        self._threads: dict[str, deque[MemoryTurn]] = {}
        self._active_thread_id = self._normalize_thread_id(default_thread_id)

        self._load_from_disk()
        self._ensure_thread(self._active_thread_id)
        self._persist()

    def add_turn(
        self,
        *,
        user: str,
        assistant: str,
        route: Literal["business_question", "normal_chat"],
        sql_used: str | None = None,
        sql_explanation: str | None = None,
        citations: list[dict[str, Any]] | None = None,
        verification: dict[str, Any] | None = None,
        chart_data: dict[str, Any] | None = None,
        chart_type: str | None = None,
        row_preview: list[dict[str, Any]] | None = None,
        trace_id: str | None = None,
    ) -> None:
        self._current_turns().append(
            MemoryTurn(
                user=user.strip(),
                assistant=assistant.strip(),
                route=route,
                sql_used=sql_used,
                sql_explanation=sql_explanation,
                citations=citations or [],
                verification=verification,
                chart_data=chart_data,
                chart_type=chart_type,
                row_preview=row_preview,
                trace_id=trace_id,
            )
        )
        self._persist()

    def clear(self) -> None:
        self._current_turns().clear()
        self._persist()

    def turn_count(self) -> int:
        return len(self._current_turns())

    def is_empty(self) -> bool:
        return len(self._current_turns()) == 0

    def get_thread_turns(self, thread_id: str) -> list[MemoryTurn]:
        """Return all turns for a given thread (does not switch active thread)."""
        normalized = self._normalize_thread_id(thread_id)
        if normalized not in self._threads:
            raise ValueError(f"Thread '{normalized}' does not exist.")
        return list(self._threads[normalized])

    def get_last_business_turn(self) -> MemoryTurn | None:
        """Return the most recent business_question turn that has row_preview data."""
        for turn in reversed(list(self._current_turns())):
            if turn.route == "business_question" and turn.row_preview:
                return turn
        return None

    def format_for_prompt(self) -> str:
        turns = self._current_turns()
        if not turns:
            return f"(no prior conversation in thread '{self._active_thread_id}')"

        lines: list[str] = []
        for idx, turn in enumerate(turns, start=1):
            lines.append(f"Turn {idx} | route={turn.route}")
            lines.append(f"User: {turn.user}")
            lines.append(f"Assistant: {turn.assistant}")
            if turn.sql_used:
                lines.append(f"SQL used: {turn.sql_used}")
            if turn.sql_explanation:
                lines.append(f"SQL meaning: {turn.sql_explanation}")
        return "\n".join(lines)

    def format_for_display(self) -> str:
        turns = self._current_turns()
        if not turns:
            return f"(memory empty in thread '{self._active_thread_id}')"

        lines: list[str] = [f"Active thread: {self._active_thread_id}"]
        for idx, turn in enumerate(turns, start=1):
            lines.append(f"{idx}. [{turn.route}]")
            lines.append(f"   user: {turn.user}")
            lines.append(f"   bot: {turn.assistant}")
        return "\n".join(lines)

    def active_thread_id(self) -> str:
        return self._active_thread_id

    def list_threads(self) -> list[ThreadSummary]:
        if not self._threads:
            return []

        ordered_ids = [self._active_thread_id] + sorted(
            thread_id
            for thread_id in self._threads
            if thread_id != self._active_thread_id
        )
        return [
            ThreadSummary(
                thread_id=thread_id,
                turn_count=len(self._threads[thread_id]),
                is_active=(thread_id == self._active_thread_id),
            )
            for thread_id in ordered_ids
        ]

    def create_thread(self, thread_id: str, *, switch: bool = True) -> str:
        normalized = self._normalize_thread_id(thread_id)
        self._ensure_thread(normalized)
        if switch:
            self._active_thread_id = normalized
        self._persist()
        return normalized

    def switch_thread(self, thread_id: str, *, create_if_missing: bool = False) -> str:
        normalized = self._normalize_thread_id(thread_id)
        if normalized not in self._threads:
            if not create_if_missing:
                raise ValueError(f"Thread '{normalized}' does not exist.")
            self._ensure_thread(normalized)
        self._active_thread_id = normalized
        self._persist()
        return normalized

    def _current_turns(self) -> deque[MemoryTurn]:
        return self._threads[self._active_thread_id]

    def _ensure_thread(self, thread_id: str) -> None:
        if thread_id not in self._threads:
            self._threads[thread_id] = deque(maxlen=self._max_turns)

    def _normalize_thread_id(self, thread_id: str) -> str:
        normalized = thread_id.strip()
        if not _VALID_THREAD_ID.fullmatch(normalized):
            raise ValueError(
                "Invalid thread id. Use 1-64 chars: letters, numbers, '.', '_' or '-'."
            )
        return normalized

    def _load_from_disk(self) -> None:
        if self._store_path is None or not self._store_path.exists():
            return

        try:
            payload = json.loads(self._store_path.read_text(encoding="utf-8"))
        except Exception:
            return

        if not isinstance(payload, dict):
            return

        raw_active = payload.get("active_thread_id")
        if isinstance(raw_active, str) and _VALID_THREAD_ID.fullmatch(raw_active.strip()):
            self._active_thread_id = raw_active.strip()

        raw_threads = payload.get("threads")
        if not isinstance(raw_threads, dict):
            return

        loaded_threads: dict[str, deque[MemoryTurn]] = {}
        for raw_thread_id, raw_turns in raw_threads.items():
            if not isinstance(raw_thread_id, str):
                continue
            normalized_id = raw_thread_id.strip()
            if not _VALID_THREAD_ID.fullmatch(normalized_id):
                continue
            if not isinstance(raw_turns, list):
                continue

            thread_turns: deque[MemoryTurn] = deque(maxlen=self._max_turns)
            for raw_turn in raw_turns:
                if not isinstance(raw_turn, dict):
                    continue
                user = raw_turn.get("user")
                assistant = raw_turn.get("assistant")
                route = raw_turn.get("route")
                if not isinstance(user, str) or not isinstance(assistant, str):
                    continue
                if not isinstance(route, str) or route not in _ROUTES:
                    continue
                thread_turns.append(
                    MemoryTurn(
                        user=user,
                        assistant=assistant,
                        route=route,  # type: ignore[arg-type]
                        sql_used=raw_turn.get("sql_used"),
                        sql_explanation=raw_turn.get("sql_explanation"),
                        citations=raw_turn.get("citations") or [],
                        verification=raw_turn.get("verification"),
                        chart_data=raw_turn.get("chart_data"),
                        chart_type=raw_turn.get("chart_type"),
                        row_preview=raw_turn.get("row_preview"),
                        trace_id=raw_turn.get("trace_id"),
                        chart_path=raw_turn.get("chart_path"),  # legacy
                    )
                )
            loaded_threads[normalized_id] = thread_turns

        if loaded_threads:
            self._threads = loaded_threads

    def _persist(self) -> None:
        if self._store_path is None:
            return

        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "active_thread_id": self._active_thread_id,
            "max_turns": self._max_turns,
            "threads": {
                thread_id: [asdict(turn) for turn in turns]
                for thread_id, turns in self._threads.items()
            },
        }
        temp_path = self._store_path.with_suffix(f"{self._store_path.suffix}.tmp")
        temp_path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")
        temp_path.replace(self._store_path)

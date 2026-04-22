from __future__ import annotations

from src.business_context import format_context_for_prompt
from src.orchestrator import ChatOrchestrator


def main() -> None:
    orchestrator = ChatOrchestrator()

    print("Text-to-SQL chatbot started.")
    print(
        "Commands: /refresh-schema, /show-sql, /show-sql-cache, /show-sql-cache-entries [limit], "
        "/clear-sql-cache, /show-entity-match, /show-resolver, /show-context, /show-memory, "
        "/clear-memory, /thread, /thread new <id>, /thread switch <id>, /exit"
    )
    print(f"Active thread: {orchestrator.active_thread()}")

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not user_input:
            continue

        if user_input == "/exit":
            print("Exiting.")
            break

        if user_input == "/refresh-schema":
            try:
                schema = orchestrator.refresh_schema()
                print("bot> Schema refreshed.")
                print(f"bot> Cached schema length: {len(schema)} chars")
            except Exception as exc:  # pragma: no cover - runtime safeguard
                print(f"bot> Failed to refresh schema: {exc}")
            continue

        if user_input == "/show-sql":
            if orchestrator.last_sql:
                print("bot> Last SQL:")
                print(orchestrator.last_sql)
            else:
                print("bot> No SQL generated yet.")
            continue

        if user_input == "/show-sql-cache":
            if orchestrator.last_sql_cache_status:
                print(f"bot> Last SQL cache hit: {orchestrator.last_sql_cache_status}")
            else:
                print("bot> Last SQL came from fresh generation.")
            continue

        if user_input.startswith("/show-sql-cache-entries"):
            parts = user_input.split()
            limit = 10
            if len(parts) >= 2:
                try:
                    limit = max(1, int(parts[1]))
                except ValueError:
                    print("bot> Usage: /show-sql-cache-entries [limit]")
                    continue

            entries = orchestrator.list_sql_cache_entries(limit=limit)
            if not entries:
                print("bot> SQL cache is empty (or disabled).")
                continue

            print(f"bot> SQL cache entries (latest {len(entries)}):")
            for entry in entries:
                embedding = entry.get("embedding")
                if isinstance(embedding, list) and embedding:
                    dims = len(embedding)
                    preview = ", ".join(f"{float(v):.6f}" for v in embedding[:12])
                    if dims > 12:
                        preview = f"{preview}, ..."
                else:
                    dims = 0
                    preview = "(none)"

                print(
                    f"  - id={entry['id']} | hits={entry['hit_count']} | "
                    f"q='{entry['original_question']}' | normalized='{entry['normalized_question']}'"
                )
                print(
                    f"    created={entry['created_at']} | last_success={entry['last_success_at']}"
                )
                print(f"    embedding_dim={dims} | embedding_preview=[{preview}]")
            continue

        if user_input == "/clear-sql-cache":
            cleared = orchestrator.clear_sql_cache()
            print(f"bot> Cleared {cleared} SQL cache entries.")
            continue

        if user_input == "/show-entity-match":
            if orchestrator.last_entity_match:
                print(f"bot> Last resolved entity: {orchestrator.last_entity_match}")
            else:
                print("bot> No entity match detected in the latest business query.")
            continue

        if user_input == "/show-resolver":
            if orchestrator.last_resolver_explanation:
                print(f"bot> Last resolver action: {orchestrator.last_resolver_explanation}")
            else:
                print("bot> No query resolution was needed for the latest query.")
            continue

        if user_input == "/show-context":
            context = orchestrator.context_store.get_context()
            print("bot> Business context:")
            print(format_context_for_prompt(context))
            continue

        if user_input == "/show-memory":
            print("bot> Conversation memory:")
            print(orchestrator.show_memory())
            continue

        if user_input == "/clear-memory":
            orchestrator.clear_memory()
            print("bot> Conversation memory cleared.")
            continue

        if user_input == "/thread" or user_input == "/thread list":
            print("bot> Threads:")
            for thread in orchestrator.list_threads():
                marker = "*" if thread.is_active else " "
                print(f"{marker} {thread.thread_id} ({thread.turn_count} turns)")
            continue

        if user_input.startswith("/thread new "):
            thread_id = user_input[len("/thread new ") :].strip()
            if not thread_id:
                print("bot> Usage: /thread new <id>")
                continue
            try:
                active = orchestrator.create_thread(thread_id)
                print(f"bot> Switched to thread '{active}'.")
            except ValueError as exc:
                print(f"bot> {exc}")
            continue

        if user_input.startswith("/thread switch "):
            thread_id = user_input[len("/thread switch ") :].strip()
            if not thread_id:
                print("bot> Usage: /thread switch <id>")
                continue
            try:
                active = orchestrator.switch_thread(thread_id)
                print(f"bot> Switched to thread '{active}'.")
            except ValueError as exc:
                print(f"bot> {exc}")
            continue

        reply = orchestrator.handle_user_message(user_input)
        print(f"bot> {reply.answer_text}")

        # ── SQL grounding (plain-English explanation of the query) ─────────
        if reply.sql_explanation:
            print(f"bot> Query: {reply.sql_explanation}")

        # ── Citations ──────────────────────────────────────────────────────
        if reply.citations:
            print("bot> Sources:")
            for i, c in enumerate(reply.citations, start=1):
                print(f"  [{i}] {c.source_column}={c.source_value}"
                      f" → {c.metric_column}: {c.metric_value}"
                      f"  (row {c.row_index + 1})")
                print(f"       Claim: {c.claim}")

        # ── Verification ───────────────────────────────────────────────────
        if reply.verification is not None:
            if reply.verification.verified:
                print("bot> ✓ All numbers verified against result data.")
            else:
                print(f"bot> ⚠ Verification: {len(reply.verification.issues)} issue(s) found:")
                for issue in reply.verification.issues:
                    print(f"  · {issue.issue}")

        # ── Data preview ───────────────────────────────────────────────────
        if reply.row_preview:
            print("bot> Preview rows:")
            for row in reply.row_preview:
                print(f"  - {row}")

        if reply.chart_path:
            chart_type = reply.chart_type or "chart"
            label = "Table" if chart_type == "table" else chart_type.capitalize()
            print(f"bot> {label} saved at: {reply.chart_path}")


if __name__ == "__main__":
    main()

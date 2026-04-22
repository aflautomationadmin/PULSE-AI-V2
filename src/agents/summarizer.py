from __future__ import annotations

import json
from collections.abc import Generator

from src.llm import run_text_agent, stream_text_agent
from src.models import SqlExecutionResult

_SUMMARIZER_INSTRUCTIONS = """
You summarize SQL query results for business users.
Write a concise answer in plain English:
- State the direct answer first
- Mention notable metrics or trends
- Align metric interpretation to provided KPI definitions
- If result is empty, say no matching records were found
Do not mention implementation details unless asked.
""".strip()


def summarize_sql_result(
    question: str,
    sql: str,
    result: SqlExecutionResult,
    business_context: str,
) -> str:
    payload = {
        "question": question,
        "sql": sql,
        "business_context": business_context,
        "columns": result.columns,
        "row_count": result.row_count,
        "elapsed_ms": result.elapsed_ms,
        "rows": result.rows,
    }

    return run_text_agent(
        agent_name="result-summarizer",
        instructions=_SUMMARIZER_INSTRUCTIONS,
        user_input=json.dumps(payload, default=str),
    )


def stream_summarize_sql_result(
    question: str,
    sql: str,
    result: SqlExecutionResult,
    business_context: str,
) -> Generator[str, None, None]:
    """Streaming version — yields text chunks as the LLM produces them."""
    payload = {
        "question": question,
        "sql": sql,
        "business_context": business_context,
        "columns": result.columns,
        "row_count": result.row_count,
        "elapsed_ms": result.elapsed_ms,
        "rows": result.rows,
    }
    yield from stream_text_agent(
        agent_name="result-summarizer",
        instructions=_SUMMARIZER_INSTRUCTIONS,
        user_input=json.dumps(payload, default=str),
    )

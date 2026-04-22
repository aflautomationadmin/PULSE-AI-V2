from __future__ import annotations

import json

from pydantic import BaseModel

from src.llm import run_json_agent
from src.models import Citation, SqlExecutionResult


class _CitationOutput(BaseModel):
    citations: list[Citation] = []


_CITATION_INSTRUCTIONS = """
You are given a natural-language answer and the actual SQL result (columns + rows).

For every specific factual claim, number, name, or metric mentioned in the answer,
find the exact row and column in the SQL result that supports it.

Return JSON:
{
  "citations": [
    {
      "claim": "short description of the claim (max 10 words)",
      "source_column": "column that identifies the row (e.g. BRAND, STATE, STORE_NAME)",
      "source_value": "value in that column (e.g. ARROW)",
      "metric_column": "column containing the cited number (e.g. NETAMT, QTY)",
      "metric_value": "the raw value from the result row as a string",
      "row_index": 0
    }
  ]
}

Rules:
- Only cite claims directly backed by a single specific row in the result.
- Skip summary claims that span multiple rows (e.g. "overall sales increased").
- Skip percentage/ratio claims derived from multiple rows.
- row_index is 0-based (first row = 0).
- Keep claim text short and specific.
- If no specific per-row claims exist, return {"citations": []}.
""".strip()


def build_citations(
    answer: str,
    result: SqlExecutionResult,
) -> list[Citation]:
    """Map factual claims in the answer back to specific rows in the SQL result."""
    if not answer.strip() or result.row_count == 0:
        return []

    # Serialise result for the LLM
    rows_preview = []
    for idx, row in enumerate(result.rows[:50]):   # cap at 50 rows for prompt size
        rows_preview.append({"row_index": idx, **dict(zip(result.columns, row, strict=False))})

    prompt = (
        f"Natural-language answer:\n{answer}\n\n"
        f"SQL result columns: {result.columns}\n\n"
        f"SQL result rows (JSON):\n{json.dumps(rows_preview, default=str, indent=2)}"
    )

    try:
        output = run_json_agent(
            agent_name="citation-builder",
            instructions=_CITATION_INSTRUCTIONS,
            user_input=prompt,
            response_model=_CitationOutput,
        )
        return output.citations
    except Exception:
        return []

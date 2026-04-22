from __future__ import annotations

from src.llm import run_text_agent

_SQL_EXPLAINER_INSTRUCTIONS = """
You convert a T-SQL query into a single clear plain-English sentence that a
non-technical business user can read and immediately verify.

Rules:
- Write exactly ONE sentence (max 40 words).
- Mention: what metric is being measured, any filters applied (brand, store, date range,
  state, category etc.), and how the result is grouped or ordered.
- Use business-friendly language — no SQL keywords, no table names, no column names.
- Use Indian number/date conventions where relevant (e.g. "yesterday", "last 30 days").
- Do not explain HOW the SQL works — only WHAT it returns.

Examples:
  SQL: SELECT BRAND, SUM(NETAMT) FROM prd.FACT_SALES_AI
       WHERE INV_DATE = CAST(GETDATE()-1 AS DATE) GROUP BY BRAND ORDER BY 2 DESC
  Plain English: Total net sales by brand for yesterday, ranked highest to lowest.

  SQL: SELECT STATE, COUNT(DISTINCT INV_CNT) AS Bills FROM prd.FACT_SALES_AI
       WHERE INV_DATE BETWEEN DATEADD(DAY,-7,GETDATE()) AND GETDATE()
       GROUP BY STATE
  Plain English: Number of unique sales bills per state for the last 7 days.
""".strip()


def explain_sql(sql: str) -> str:
    """
    Convert a T-SQL query into a single plain-English sentence for user grounding.
    Returns a fallback string on any error so the pipeline is never blocked.
    """
    if not sql.strip():
        return ""
    try:
        return run_text_agent(
            agent_name="sql-explainer",
            instructions=_SQL_EXPLAINER_INSTRUCTIONS,
            user_input=f"Convert this SQL to plain English:\n\n{sql}",
        )
    except Exception:
        return ""

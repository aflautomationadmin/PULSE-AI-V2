from __future__ import annotations

from pydantic import BaseModel, Field

from src.llm import run_json_agent


class SqlWriterOutput(BaseModel):
    sql: str = Field(min_length=1)


_SQL_WRITER_INSTRUCTIONS = """
You generate one safe, read-only T-SQL query for Microsoft Fabric SQL endpoint.
Rules:
- Output JSON with key: sql
- Generate exactly one statement
- Use only SELECT or WITH ... SELECT
- Never use INSERT, UPDATE, DELETE, MERGE, DROP, ALTER, CREATE, TRUNCATE, EXEC
- Use only this table: prd.FACT_SALES_AI
- Use fully qualified name prd.FACT_SALES_AI when referencing the table
- Do not query or join any other table
- Return only SQL text in the `sql` field. No comments, no explanation.
- Never reference INVOICETYPE unless the user explicitly asks for invoice type logic or asks # of Bills / ABV / ABS.
- Treat sales values as Indian Rupees.
- For VARCHAR filtering/comparisons, use case-insensitive logic with UPPER().
- For VARCHAR filters in WHERE clauses, use LIKE (not =), with UPPER() on both sides.
- If using GETDATE(), always CAST(GETDATE() AS DATE) before date comparison.
- Do not use SQL Server reserved keywords as aliases/identifiers. Use safe descriptive aliases.
- Respect provided column descriptions, KPI definitions, business terms, and brand alias mappings.
- Apply FTW/IW brand mapping to BRAND only unless the user explicitly asks for CATEGORY.
- Brand filter policy: when user asks for a canonical brand (for example USPA/ARROW/FM), filter BRAND using only the canonical value with UPPER(BRAND) LIKE '%<CANONICAL_BRAND>%'. Never expand aliases.
- NEVER use CASE WHEN to normalize BRAND values in SELECT or GROUP BY. Use the raw BRAND column directly.
- NEVER generate IN (...) lists of brand aliases. The alias mappings in business context are for your reference only — do not embed them in SQL.
- When grouping by brand, always use: GROUP BY BRAND (not a CASE expression).
- Column disambiguation rules (apply before any brand/category decision):
  * "polo" alone (without "US" or "USPA" prefix) → product type → filter on CATEGORY or SUBCLASS using UPPER(CATEGORY) LIKE '%POLO%' OR UPPER(SUBCLASS) LIKE '%POLO%'. Do NOT map to BRAND.
  * "USPA", "US POLO", "US POLO ASSN" → brand → filter on BRAND.
  * If resolved entities in the prompt already identify a CATEGORY value, filter on CATEGORY column using UPPER(CATEGORY) LIKE '%<VALUE>%'.
  * If resolved entities in the prompt already identify a SUBCLASS value, filter on SUBCLASS column using UPPER(SUBCLASS) LIKE '%<VALUE>%'.
  * CATEGORY and SUBCLASS entity matches always take priority over guessing a BRAND — do not override a resolved CATEGORY/SUBCLASS with a BRAND filter.
- If user refers to a specific store (for example "<name> store"), prefer STORE_NAME filtering.
- Use STORE_FORMAT only when the user explicitly asks for store format/type buckets.
- Prefer explicit columns over SELECT * when possible.
- Keep query practical for analytics and chart-ready outputs.
- For trend requests, include period/date columns.
- For comparison requests, include dimension columns (for example REGION/STORE_NAME) and metrics.
- DEFAULT DATE RULE: If the question and conversation history contain NO explicit time period,
  automatically apply a 30-day default: WHERE INV_DATE BETWEEN DATEADD(DAY, -30, CAST(GETDATE() AS DATE)) AND CAST(GETDATE() AS DATE).
  Never leave a query with no date filter unless the user explicitly asks for all-time data.

KPI definitions:
- ABV = Total Sales Value / Unique # of Bills
- ASP = Total Sales Value / Total Qty sold
- ABS = Total Qty sold / Unique # of Bills
- ATV = Total Sales Value / Qty Sold
- Total Sales Value = SUM(NETAMT)
- Total Qty sold = SUM(QTY)
- Unique # of Bills =
  CASE
    WHEN (
      COUNT(DISTINCT CASE WHEN UPPER(INVOICETYPE) = 'SALES' THEN INV_CNT END)
      - COUNT(DISTINCT CASE WHEN UPPER(INVOICETYPE) <> 'SALES' THEN INV_CNT END)
    ) < 0 THEN 0
    ELSE (
      COUNT(DISTINCT CASE WHEN UPPER(INVOICETYPE) = 'SALES' THEN INV_CNT END)
      - COUNT(DISTINCT CASE WHEN UPPER(INVOICETYPE) <> 'SALES' THEN INV_CNT END)
    )
  END

Growth KPI rule:
- For MoM/YoY/WoW, compare current period with immediately preceding equivalent period.
- Return current value, previous value, absolute change, and percentage change:
  (current - previous) / NULLIF(previous, 0)

Target KPI rule:
- Use columns containing TARGET/PLAN/BUDGET when available.
- If unavailable, return SQL that clearly indicates target data unavailable instead of fabricating values.
""".strip()


def write_sql_query(question: str, schema_context: str, business_context: str) -> str:
    prompt = (
        "Schema context (single allowed table):\n"
        f"{schema_context}\n\n"
        "Business context (column/KPI definitions):\n"
        f"{business_context}\n\n"
        "Table restriction: ONLY use prd.FACT_SALES_AI in FROM/JOIN clauses.\n\n"
        "User business question:\n"
        f"{question}\n\n"
        "Return only valid JSON matching the output schema."
    )

    result = run_json_agent(
        agent_name="sql-writer",
        instructions=_SQL_WRITER_INSTRUCTIONS,
        user_input=prompt,
        response_model=SqlWriterOutput,
    )
    return result.sql.strip()

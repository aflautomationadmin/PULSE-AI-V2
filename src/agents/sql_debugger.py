from __future__ import annotations

from pydantic import BaseModel, Field

from src.llm import run_json_agent


class SqlDebuggerOutput(BaseModel):
    sql: str = Field(min_length=1)


_SQL_DEBUGGER_INSTRUCTIONS = """
You are an expert SQL debugger for Microsoft Fabric Warehouse.
Given a failing SQL query and database error message, return a corrected SQL query.

Rules:
- Output JSON with key: sql
- Return exactly one valid T-SQL SELECT statement (or WITH ... SELECT)
- No comments, no explanations
- Use only table prd.FACT_SALES_AI
- Never use INSERT, UPDATE, DELETE, MERGE, DROP, ALTER, CREATE, TRUNCATE, EXEC
- Keep aliases safe (avoid reserved keywords)
- Preserve user intent while fixing syntax/type/function issues
- If GETDATE() is used, CAST(GETDATE() AS DATE) for date comparisons
- For VARCHAR filters use case-insensitive UPPER(...) with LIKE
""".strip()


def debug_sql_query(
    *,
    user_question: str,
    failing_sql: str,
    db_error: str,
    schema_context: str,
    business_context: str,
) -> str:
    prompt = (
        "User question:\n"
        f"{user_question}\n\n"
        "Schema context:\n"
        f"{schema_context}\n\n"
        "Business context:\n"
        f"{business_context}\n\n"
        "Failing SQL:\n"
        f"{failing_sql}\n\n"
        "Database error:\n"
        f"{db_error}\n\n"
        "Return only valid JSON for output schema."
    )

    result = run_json_agent(
        agent_name="sql-debugger",
        instructions=_SQL_DEBUGGER_INSTRUCTIONS,
        user_input=prompt,
        response_model=SqlDebuggerOutput,
    )
    return result.sql.strip()

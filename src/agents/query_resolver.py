from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from src.llm import run_json_agent


class FailureType(str, Enum):
    DB_ERROR      = "db_error"       # SQL ran but the DB rejected it (syntax/type/column)
    GUARD_ERROR   = "guard_error"    # SQL guard blocked it (non-SELECT statement)
    EMPTY_RESULT  = "empty_result"   # SQL ran fine but returned 0 rows
    TIMEOUT       = "timeout"        # Query took too long


class ResolutionStrategy(str, Enum):
    SYNTAX_FIX     = "syntax_fix"    # Fix SQL syntax / type / function errors
    COLUMN_FIX     = "column_fix"    # Replace invalid column names with correct ones
    BROADEN        = "broaden"       # Widen filters (date range, remove narrow WHERE)
    SIMPLIFY       = "simplify"      # Remove heavy operations that cause timeouts
    SAFE_REWRITE   = "safe_rewrite"  # Rewrite unsafe SQL as a safe SELECT
    FULL_REWRITE   = "full_rewrite"  # Complete rewrite using a different approach
    GIVE_UP        = "give_up"       # Cannot resolve — return explanation only


class ResolverOutput(BaseModel):
    strategy:    ResolutionStrategy
    sql:         str = ""            # empty when strategy = GIVE_UP
    explanation: str                 # what was wrong and what was changed
    confidence:  float = Field(ge=0.0, le=1.0, default=0.8)


_RESOLVER_INSTRUCTIONS = """
You are an expert Query Resolver for a retail analytics chatbot built on Microsoft Fabric SQL.
A query has failed. Your job is to diagnose WHY it failed and return a corrected SQL query
along with a brief explanation a business user can understand.

You will receive:
- failure_type: one of db_error | guard_error | empty_result | timeout
- error_message: the exact error from the database or system
- user_question: what the user originally asked
- failing_sql: the SQL that failed
- schema_context: the table schema
- business_context: column definitions and KPI rules
- attempt: which resolution attempt this is (1 = first try, 2 = second try)

Resolution strategies — pick the best one:

1. syntax_fix   → Fix syntax/type errors. Wrong function names, missing casts, invalid aliases,
                  wrong date functions. Keep user intent intact.
                  Use when: db_error with syntax/type/function messages.

2. column_fix   → Replace column names that don't exist with the correct ones from schema.
                  Use when: db_error says "invalid column name" or "column not found".

3. broaden      → Widen the filters. Expand date range (e.g. -1 day → -30 days), remove
                  overly specific WHERE clauses, try LIKE instead of exact match.
                  Use when: empty_result — the query ran but found nothing.

4. simplify     → Remove heavy subqueries, window functions, or complex CTEs. Replace with
                  simpler aggregations. Reduce the result set size.
                  Use when: timeout — query took too long.

5. safe_rewrite → Rewrite entirely as a safe SELECT-only query on prd.FACT_SALES_AI.
                  Use when: guard_error — non-SELECT statement was generated.

6. full_rewrite → Complete rewrite using a completely different SQL approach to answer
                  the same business question.
                  Use when: attempt >= 2 and other strategies failed, or error is too complex.

7. give_up      → Cannot be resolved. Return sql="" and a clear explanation.
                  Use when: the question cannot be answered from prd.FACT_SALES_AI at all,
                  or it is the 3rd+ attempt and nothing has worked.

Rules for the resolved SQL:
- Use only prd.FACT_SALES_AI
- SELECT or WITH...SELECT only — never INSERT/UPDATE/DELETE/MERGE/DROP/ALTER/CREATE/EXEC
- Always CAST(GETDATE() AS DATE) for date comparisons
- For VARCHAR filters: UPPER(column) LIKE UPPER('%value%')
- No comments or markdown in the sql field
- If no time period in original question: default to last 30 days

Return JSON:
{
  "strategy":    "syntax_fix | column_fix | broaden | simplify | safe_rewrite | full_rewrite | give_up",
  "sql":         "corrected T-SQL or empty string if give_up",
  "explanation": "1-2 sentences: what was wrong + what was changed (business-friendly language)",
  "confidence":  0.0 to 1.0
}
""".strip()


def resolve_query(
    *,
    user_question: str,
    failing_sql: str,
    failure_type: FailureType,
    error_message: str,
    schema_context: str,
    business_context: str,
    attempt: int = 1,
) -> ResolverOutput:
    """
    Diagnose a query failure and return a resolved SQL with explanation.

    Returns a ResolverOutput with strategy=GIVE_UP and sql="" if the resolver
    determines the query cannot be fixed.
    Falls back to GIVE_UP on any exception so the pipeline is never blocked.
    """
    prompt = (
        f"failure_type:    {failure_type.value}\n"
        f"attempt:         {attempt}\n"
        f"error_message:   {error_message}\n\n"
        f"user_question:\n{user_question}\n\n"
        f"failing_sql:\n{failing_sql}\n\n"
        f"schema_context:\n{schema_context}\n\n"
        f"business_context:\n{business_context}"
    )
    try:
        return run_json_agent(
            agent_name="query-resolver",
            instructions=_RESOLVER_INSTRUCTIONS,
            user_input=prompt,
            response_model=ResolverOutput,
        )
    except Exception:
        return ResolverOutput(
            strategy=ResolutionStrategy.GIVE_UP,
            sql="",
            explanation="I was unable to resolve this query automatically.",
            confidence=0.0,
        )

from __future__ import annotations

from src.llm import run_text_agent

_EMPTY_RESULT_INSTRUCTIONS = """
You are a retail sales analytics assistant. A SQL query ran successfully but returned zero rows.
Your job is to help the user understand why and guide them toward a working query.

Analyse the question, the SQL that was executed, and any resolved entity attributes provided, then respond with:
1. A one-line acknowledgement that no data was found for those filters.
2. The most likely reasons (bad spelling, wrong period, filter too narrow, data not available for that date, etc.).
   - If resolved entity attributes are provided, mention the specific attribute and value that was used (e.g. "I searched using SUBCLASS = 'JOGGERS'") and suggest the user verify that value exists.
3. ONE clear follow-up question that asks the user to correct or broaden the most probable issue.

Rules:
- Be concise — 3-4 sentences max.
- Do not show the SQL to the user.
- Do not make up data or assume what the correct answer is.
- Focus on the most probable cause first (usually: wrong spelling, date with no data, or overly specific filter).
- If resolved entity attributes are listed, always reference them in your explanation so the user understands what the system matched.
- If a SUBCLASS or CATEGORY filter was applied, suggest checking the exact product name spelling.
- If a date filter was applied, suggest trying a broader range (last week, last month).
- If a brand/store filter was applied, suggest confirming the exact name.
""".strip()


def handle_empty_result(
    question: str,
    sql: str,
    business_context: str,
    *,
    entity_match: str | None = None,
) -> str:
    entity_block = ""
    if entity_match:
        entity_block = f"\nResolved entity attributes used in the query:\n{entity_match}\n"
    prompt = (
        f"User question: {question}\n\n"
        f"SQL that returned no rows:\n{sql}\n"
        f"{entity_block}\n"
        f"Business context (column definitions):\n{business_context}"
    )
    try:
        return run_text_agent(
            agent_name="empty-result-handler",
            instructions=_EMPTY_RESULT_INSTRUCTIONS,
            user_input=prompt,
        )
    except Exception:
        return (
            "I didn't find any records matching your query. "
            "Could you double-check the spelling, filters, or try a broader time period?"
        )

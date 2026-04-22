from __future__ import annotations

from pydantic import BaseModel

from src.llm import run_json_agent


class ClarifierOutput(BaseModel):
    needs_clarification: bool
    clarifying_question: str = ""


_CLARIFIER_INSTRUCTIONS = """
You review retail sales analytics questions to decide if the question is so vague that no
meaningful SQL can be written even with a default time window.

IMPORTANT: The SQL engine will automatically apply a default date range (last 30 days) when
no time period is mentioned. Therefore you must NEVER ask for a time period — assume the
default will be used.

Ask for clarification ONLY when ALL of the following are true:
  - The question has NO metric at all (no mention of sales, revenue, quantity, units, count, amount, KPI)
  - AND NO dimension at all (no grouping, no region, no brand, no product, no store)
  - AND the question is completely ambiguous even with a 30-day default applied
  Examples that REQUIRE clarification: "give me the data", "show me everything", "what are the numbers"

Do NOT ask for clarification when:
  - The question mentions ANY metric (sales, revenue, qty, amount, ABV, ASP, ABS, ATV, bills, target)
  - OR the question mentions ANY dimension (brand, store, state, city, category, region, product)
  - OR the question mentions ANY time period (yesterday, last week, this month, Q1, YTD, today, last N days)
  - OR the conversation history provides enough context to infer intent
  - OR this is a follow-up to a previous business question (look at conversation history)

Rules:
- Be short and conversational — max 1 sentence.
- If in doubt, set needs_clarification to false and let the SQL engine try.

Return JSON:
{
  "needs_clarification": true or false,
  "clarifying_question": "Your follow-up question here, or empty string if not needed"
}
""".strip()


def check_needs_clarification(question_with_history: str, business_context: str) -> ClarifierOutput:
    prompt = (
        f"Business context (available columns/KPIs):\n{business_context}\n\n"
        f"Conversation context and current question:\n{question_with_history}"
    )
    try:
        return run_json_agent(
            agent_name="clarifier",
            instructions=_CLARIFIER_INSTRUCTIONS,
            user_input=prompt,
            response_model=ClarifierOutput,
        )
    except Exception:
        return ClarifierOutput(needs_clarification=False)

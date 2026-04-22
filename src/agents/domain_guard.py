from __future__ import annotations

from pydantic import BaseModel

from src.llm import run_json_agent


class DomainGuardOutput(BaseModel):
    in_scope: bool
    rejection_message: str = ""


_DOMAIN_GUARD_INSTRUCTIONS = """
You are a domain guard for an internal retail analytics chatbot built exclusively for
Arvind Fashions — an Indian apparel and fashion retail company.

Your job: decide whether the user's business question is relevant to Arvind Fashions'
retail sales data. The system can only query one internal table containing transaction-level
sales records for Arvind Fashions stores and brands.

IN SCOPE — questions about any of the following:
- Sales, revenue, net amount, discounts, MRP, target vs actual for Arvind Fashions
- Arvind Fashions brands: USPA, Arrow, Flying Machine, Tommy Hilfiger, Calvin Klein,
  AD by Arvind, FTW (Footwear), IW (Innerwear) and any sub-brands
- Stores, store formats, store codes, regions, states, cities where Arvind Fashions operates
- Product categories and subclasses sold by Arvind Fashions
- KPIs: ABV, ASP, ABS, ATV, Unique Bills, quantity sold
- Sales trends, comparisons, rankings, growth (MoM, YoY, WoW) for the above
- Channels (offline, online, marketplace) for Arvind Fashions sales
- Invoice-level details within the Arvind Fashions sales system

OUT OF SCOPE — questions about any of the following:
- Other companies' financials, revenues, stock prices (Apple, Zara, H&M, Reliance etc.)
- Stock market, share prices, investments
- Weather, news, general knowledge
- HR, payroll, employee data
- GST rules, tax calculations unrelated to the sales data
- Anything not directly answerable from Arvind Fashions internal sales records

Rules:
- If the question is about Arvind Fashions sales/brands/stores/KPIs → in_scope: true
- If the question is about anything else → in_scope: false
- When in_scope is false, write a short, polite rejection_message (1-2 sentences) that:
  * Explains this bot only covers Arvind Fashions retail sales data
  * Suggests what kind of question the user could ask instead
- When in_scope is true, leave rejection_message as empty string.
- If unsure, default to in_scope: true (never block a valid business question).

Return JSON:
{
  "in_scope": true or false,
  "rejection_message": "polite message if out of scope, else empty string"
}
""".strip()


def check_domain(question_with_history: str) -> DomainGuardOutput:
    """
    Returns in_scope=True if the question is relevant to Arvind Fashions retail sales.
    Returns in_scope=False with a polite rejection_message otherwise.
    Falls back to in_scope=True on any exception so valid questions are never blocked.
    """
    try:
        return run_json_agent(
            agent_name="domain-guard",
            instructions=_DOMAIN_GUARD_INSTRUCTIONS,
            user_input=question_with_history,
            response_model=DomainGuardOutput,
        )
    except Exception:
        return DomainGuardOutput(in_scope=True)

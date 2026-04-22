"""
kpi_router.py
─────────────
Detects whether a user question maps to a registered KPI stored procedure
and extracts all call parameters from the question + conversation context.

If matched  → returns KpiRouteResult (procedure name + parameter dict)
If no match → returns None  (caller falls through to SQL Writer)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from src.llm import run_text_agent, _extract_json_object

logger = logging.getLogger(__name__)

# ── Data models ────────────────────────────────────────────────────────────────

@dataclass
class KpiRouteResult:
    """Holds everything needed to EXEC a stored procedure."""
    kpi:            str             # human label, e.g. "ABV"
    procedure:      str             # DB object name, e.g. "GetABVai"
    parameters:     dict[str, Any]  # only non-None params included
    exec_sql:       str             # ready-to-log EXEC statement


# ── Prompt ─────────────────────────────────────────────────────────────────────

_SYSTEM_TEMPLATE = """
You are a KPI routing agent for a retail analytics assistant (Arvind Fashions).

TODAY'S DATE: {today}  (use this to compute explicit dates when needed)

You receive:
1. A user question (with conversation history for context)
2. A list of registered KPI stored procedures with their parameter contracts

Your job:
- Decide if the question is asking for one of the registered KPIs.
- If yes: extract ALL relevant parameters from the question and return them.
- If no:  return {{"matched": false}}.

═══ MATCHING RULES — READ CAREFULLY ════════════════════════════════════════

STRICT NAME MATCH REQUIRED:
  Only return matched=true when the question explicitly names the KPI or uses
  one of its listed aliases.  Do NOT match based on general business intent.

  Examples for ABV / GetABVai:
    ✓ MATCH  — "what is the ABV this month"
    ✓ MATCH  — "average basket value by brand"
    ✓ MATCH  — "show basket value trend for last 30 days"
    ✓ MATCH  — "bill value YTD by channel"
    ✗ NO MATCH — "YTD growth of USPA"          ← asks for growth, not ABV
    ✗ NO MATCH — "net sales by brand MTD"       ← asks for net sales
    ✗ NO MATCH — "top 5 stores last month"      ← no KPI name mentioned
    ✗ NO MATCH — "how is USPA performing YTD"   ← vague, no KPI name

  When in doubt, return {{"matched": false}} and let the SQL writer handle it.

═══ PARAMETER EXTRACTION RULES ══════════════════════════════════════════════

date_from / date_to
  • Set for BOTH of these cases:
      a) User gives an explicit calendar range ("from April 1 to April 15",
         "between Oct 2025 and Mar 2026") — format: "YYYY-MM-DD".
      b) User asks for a non-standard relative span that has NO matching preset
         (e.g., "last 10 days", "last 3 weeks", "last 45 days").
         Compute from TODAY: date_from = TODAY minus N days, date_to = TODAY.
  • Leave null when user says a period that maps to a named preset below.

date_preset
  • Use ONLY when date_from is null AND the period matches exactly:
      this month / MTD            → "MTD"
      this quarter / QTD          → "QTD"
      this year / YTD             → "YTD"
      last 7 days / last week     → "LAST_7"
      last 14 days / last 2 weeks → "LAST_14"
      last 30 days                → "LAST_30"
      previous month / last month → "LAST_MONTH"
  • For ANY other number of days (e.g., last 10 days, last 5 days, last 20 days)
    — do NOT guess the nearest preset. Use date_from / date_to instead.
  • Default to "MTD" when no date context at all is given.

time_grain
  • null      — user wants a single aggregate or a breakdown (no date axis).
                Use when question has NO trend / over-time intent.
                Example: "MTD ABV by brand"
  • "AUTO"    — user wants a trend but didn't specify granularity, OR user
                provides any date range without saying daily/weekly/monthly.
                Example: "ABV trend last 10 days", "ABV over the last 6 months"
  • "DAY"     — user explicitly says daily / day by day.
  • "WEEK"    — user explicitly says weekly.
  • "MONTH"   — user explicitly says monthly.
  • "QUARTER" — user explicitly says quarterly.

group_by
  • Comma-separated column names from the allowed list.
  • Include a dimension when the user says "by brand", "across states",
    "channel-wise", "store-wise", etc.
  • null when no grouping is requested.

Dimension filters (brand, state, city, channel, …)
  • Set to the value the user mentions. Null if not mentioned.
  • Use the exact phrasing from the question — entity resolution happens
    downstream; do not normalise brand names here.

═══ OUTPUT FORMAT ════════════════════════════════════════════════════════════

When matched:
{{
  "matched": true,
  "kpi": "<kpi name>",
  "procedure": "<procedure name>",
  "parameters": {{
    "date_from":    <"YYYY-MM-DD" or null>,
    "date_to":      <"YYYY-MM-DD" or null>,
    "date_preset":  <"MTD"|"QTD"|"YTD"|"LAST_7"|"LAST_14"|"LAST_30"|"LAST_MONTH" or null>,
    "time_grain":   <null|"AUTO"|"DAY"|"WEEK"|"MONTH"|"QUARTER">,
    "brand":        <string or null>,
    "subbrand":     <string or null>,
    "store_name":   <string or null>,
    "store_format": <string or null>,
    "storecode":    <string or null>,
    "channel":      <string or null>,
    "region":       <string or null>,
    "state":        <string or null>,
    "city":         <string or null>,
    "category":     <string or null>,
    "subclass":     <string or null>,
    "group_by":     <string or null>
  }}
}}

When not matched:
{{ "matched": false }}

Return ONLY valid JSON. No markdown, no explanation.
""".strip()


# ── Alias pre-filter ───────────────────────────────────────────────────────────

def _question_mentions_any_kpi(question: str, kpi_procedures: list[dict]) -> bool:
    """
    Return True only if the question contains the KPI name or at least one of
    its listed aliases (case-insensitive whole-word match).

    This guards against the LLM over-matching generic questions like
    "YTD growth of USPA" to the ABV procedure just because it looks like a
    KPI question.
    """
    # Normalise: lowercase, collapse whitespace
    q_lower = " " + question.lower() + " "

    for proc in kpi_procedures:
        # Check the KPI name itself
        kpi_name = proc.get("kpi", "")
        if kpi_name and _whole_word_match(kpi_name.lower(), q_lower):
            return True

        # Check every alias
        for alias in proc.get("aliases", []):
            if alias and _whole_word_match(alias.lower(), q_lower):
                return True

    return False


def _whole_word_match(term: str, text: str) -> bool:
    """True if ``term`` appears in ``text`` as a whole token (not a substring)."""
    import re as _re
    pattern = r"(?<![a-z0-9])" + _re.escape(term) + r"(?![a-z0-9])"
    return bool(_re.search(pattern, text))


# ── Public API ─────────────────────────────────────────────────────────────────

def route_to_kpi_procedure(
    question_with_context: str,
    kpi_procedures: list[dict[str, Any]],
) -> KpiRouteResult | None:
    """
    Attempt to match the question to a registered KPI stored procedure.

    Parameters
    ----------
    question_with_context : str
        The user question, optionally prefixed with conversation history
        (same format as what is sent to the SQL writer).
    kpi_procedures : list[dict]
        The ``kpi_procedures`` list from business_context.json.

    Returns
    -------
    KpiRouteResult  if a KPI procedure matched and parameters were extracted.
    None            if no match — caller should fall through to SQL Writer.
    """
    if not kpi_procedures:
        return None

    # ── Fast alias pre-filter ────────────────────────────────────────────────
    # Before calling the LLM, check whether the question contains at least one
    # KPI name or alias.  This prevents the LLM from over-matching generic
    # business questions like "YTD growth of USPA" to ABV.
    if not _question_mentions_any_kpi(question_with_context, kpi_procedures):
        logger.debug("kpi_router pre-filter: no KPI alias found — skipping LLM call")
        return None
    # ────────────────────────────────────────────────────────────────────────

    today_str = date.today().isoformat()          # e.g. "2026-04-17"
    procedures_json = json.dumps(kpi_procedures, indent=2)
    instructions = (
        _SYSTEM_TEMPLATE.format(today=today_str)
        + "\n\n═══ REGISTERED KPI PROCEDURES ══════════════════════════════════\n"
        + procedures_json
    )

    try:
        raw_text = run_text_agent(
            agent_name="kpi-router",
            instructions=instructions,
            user_input=question_with_context,
        )
    except Exception as exc:
        logger.warning("kpi_router LLM call failed: %s — falling through to SQL Writer", exc)
        return None

    # Parse the raw text into a dict
    try:
        result = _extract_json_object(raw_text)
    except Exception:
        try:
            result = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.warning("kpi_router returned non-JSON output — falling through to SQL Writer")
            return None

    if not result.get("matched"):
        return None

    procedure = result.get("procedure", "").strip()
    kpi_label = result.get("kpi", "").strip()
    params_raw: dict[str, Any] = result.get("parameters", {}) or {}

    if not procedure:
        logger.warning("kpi_router matched but returned no procedure name — skipping")
        return None

    # Strip out None values — only pass params the proc needs to override defaults
    params_clean = {k: v for k, v in params_raw.items() if v is not None}

    # Build a human-readable EXEC statement for logging / sql_used field
    exec_sql = _build_exec_sql(procedure, params_clean)

    return KpiRouteResult(
        kpi=kpi_label,
        procedure=procedure,
        parameters=params_clean,
        exec_sql=exec_sql,
    )


def _build_exec_sql(procedure: str, params: dict[str, Any]) -> str:
    """Build a readable EXEC string for display / logging purposes."""
    if not params:
        return f"EXEC {procedure}"
    parts = []
    for k, v in params.items():
        if isinstance(v, str):
            parts.append(f"@{k}='{v}'")
        else:
            parts.append(f"@{k}={v}")
    return "EXEC " + procedure + " " + ", ".join(parts)

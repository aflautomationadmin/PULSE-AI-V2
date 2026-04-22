from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from src.models import SqlExecutionResult

_DEFAULT_THEME = {
    "background": "#ffffff",
    "text": "#1f2937",
    "grid": "#e5e7eb",
    "palette": ["#dc2626", "#2563eb", "#10b981", "#f59e0b", "#8b5cf6", "#06b6d4", "#f97316", "#14b8a6"],
    "barColor": "#dc2626",
    "lineColor": "#dc2626",
}

_TABLE_ONLY_KEYWORDS = {"table", "tabular", "rows", "list", "raw"}
_PIE_KEYWORDS = {"share", "mix", "contribution", "split", "distribution"}
_TREND_KEYWORDS = {"trend", "daily", "weekly", "monthly", "yoy", "mom", "wow", "over time"}
_CHART_KEYWORDS = {"chart", "graph", "plot", "visualize", "visualisation", "visualization"}
_MIN_ROWS_FOR_AUTO_CHART = 2


@dataclass
class ChartData:
    """Chart configuration sent as JSON to the frontend for client-side rendering."""
    chart_type: str                          # bar | line | pie | table
    title: str
    labels: list[str] = field(default_factory=list)
    datasets: list[dict[str, Any]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)   # for table type
    rows: list[list[Any]] = field(default_factory=list) # for table type


@dataclass
class VisualOutput:
    row_preview: list[dict[str, Any]]
    chart_data: ChartData | None = None
    chart_type: str | None = None
    reason: str | None = None
    # Keep chart_path as None always (legacy compat — orchestrator still references it)
    chart_path: str | None = None


@dataclass
class ChartDecision:
    needs_chart: bool
    chart_type: str | None
    label_index: int | None
    metric_index: int | None
    reason: str


def build_visual_output(
    *,
    question: str,
    result: SqlExecutionResult,
    preview_rows: int,
    output_dir: Any = None,   # kept for API compat, no longer used
    theme_path: Any = None,   # kept for API compat, no longer used
    chart_enabled: bool,
    chart_max_points: int,
) -> VisualOutput:
    table_rows = build_table_preview(result, preview_rows=preview_rows)

    if not chart_enabled:
        return VisualOutput(row_preview=table_rows, reason="chart generation disabled")

    decision = decide_chart(question=question, result=result)

    if not decision.needs_chart:
        if len(result.rows) >= _MIN_ROWS_FOR_AUTO_CHART:
            chart_data = _build_table_data(result, question, max_points=chart_max_points)
            return VisualOutput(
                row_preview=table_rows,
                chart_data=chart_data,
                chart_type="table",
                reason=decision.reason,
            )
        return VisualOutput(row_preview=table_rows, reason=decision.reason)

    chart_data = _build_chart_data(decision, result, question, max_points=chart_max_points)
    if chart_data is None:
        return VisualOutput(row_preview=table_rows, reason="no numeric data for chart")

    return VisualOutput(
        row_preview=table_rows,
        chart_data=chart_data,
        chart_type=decision.chart_type,
        reason=decision.reason,
    )


def build_table_preview(result: SqlExecutionResult, *, preview_rows: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in result.rows[: max(1, int(preview_rows))]:
        rows.append({col: _sanitize_value(val) for col, val in zip(result.columns, row, strict=False)})
    return rows


def decide_chart(*, question: str, result: SqlExecutionResult) -> ChartDecision:
    question_norm = _normalize_text(question)
    if not result.rows or len(result.columns) < 2:
        return ChartDecision(False, None, None, None, "insufficient result shape for chart")

    tokens = set(question_norm.split())
    contains_phrase_over_time = "over time" in question_norm

    if tokens & _TABLE_ONLY_KEYWORDS:
        return ChartDecision(False, None, None, None, "question asks for table output")

    column_roles = _detect_column_roles(result, question=question)
    label_index = column_roles["label_index"]
    metric_index = column_roles["metric_index"]
    date_index = column_roles["date_index"]

    if label_index is None or metric_index is None:
        return ChartDecision(False, None, None, None, "no chart-friendly label/metric columns")

    explicit_pie = "pie" in tokens
    explicit_bar = "bar" in tokens
    explicit_line = "line" in tokens
    explicit_chart = bool(tokens & _CHART_KEYWORDS)
    asks_pie = explicit_pie or bool(tokens & _PIE_KEYWORDS)
    asks_trend = contains_phrase_over_time or bool(tokens & _TREND_KEYWORDS)
    row_count = len(result.rows)

    if explicit_pie:
        return ChartDecision(True, "pie", label_index, metric_index, "pie chart explicitly requested")
    if explicit_line:
        x_index = date_index if date_index is not None else label_index
        return ChartDecision(True, "line", x_index, metric_index, "line chart explicitly requested")
    if explicit_bar:
        return ChartDecision(True, "bar", label_index, metric_index, "bar chart explicitly requested")
    if explicit_chart:
        return ChartDecision(True, "bar", label_index, metric_index, "chart explicitly requested")
    if asks_pie and _MIN_ROWS_FOR_AUTO_CHART <= row_count <= 12:
        return ChartDecision(True, "pie", label_index, metric_index, "distribution-style question")
    if asks_trend and row_count >= _MIN_ROWS_FOR_AUTO_CHART:
        # Prefer the date column as X axis; fall back to first label column
        x_index = date_index if date_index is not None else label_index
        return ChartDecision(True, "line", x_index, metric_index, "trend/time-series question")
    if row_count >= _MIN_ROWS_FOR_AUTO_CHART:
        return ChartDecision(True, "bar", label_index, metric_index, "data distributed across multiple attributes")

    return ChartDecision(False, None, None, None, "single-value result — no chart needed")


def _build_chart_data(
    decision: ChartDecision,
    result: SqlExecutionResult,
    question: str,
    max_points: int,
) -> ChartData | None:
    if decision.label_index is None or decision.metric_index is None or decision.chart_type is None:
        return None

    theme = dict(_DEFAULT_THEME)
    rows = result.rows[: max(1, int(max_points))]

    # Determine date format once from the full set of label values so every
    # tick gets a consistent, span-aware format (e.g. "07 Apr" for daily data).
    raw_label_values = [row[decision.label_index] for row in rows if len(row) > decision.label_index]
    date_fmt = _smart_date_format(raw_label_values)   # None → str() fallback

    labels: list[str] = []
    values: list[float] = []

    for row in rows:
        v = _to_float(row[decision.metric_index])
        if v is None:
            continue
        labels.append(_label_str(row[decision.label_index], date_fmt=date_fmt))
        values.append(v)

    if not labels:
        return None

    is_pie = decision.chart_type == "pie"
    dataset: dict[str, Any] = {
        "label": result.columns[decision.metric_index],
        "data": values,
        "backgroundColor": theme["palette"] if is_pie else theme["barColor"],
        "borderColor": theme["lineColor"],
        "borderWidth": 2,
        "fill": False,
        "tension": 0.3,
        "pointRadius": 4,
        "pointHoverRadius": 6,
    }

    return ChartData(
        chart_type=decision.chart_type,
        title=_generate_chart_title(question),
        labels=labels,
        datasets=[dataset],
    )


def _sanitize_value(v: Any) -> Any:
    """Convert types that are not JSON-serialisable to safe equivalents."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def _build_table_data(
    result: SqlExecutionResult,
    question: str,
    max_points: int,
) -> ChartData:
    rows = result.rows[: max(1, int(max_points))]
    return ChartData(
        chart_type="table",
        title=_safe_title(question),
        columns=result.columns,
        rows=[[_sanitize_value(c) for c in r] for r in rows],
    )


def load_theme(theme_path: Any) -> dict[str, Any]:
    """Legacy helper — still used by tests. Returns default theme."""
    return dict(_DEFAULT_THEME)


# Column name tokens that signal a growth/rate metric — prefer these for charting
_GROWTH_COL_TOKENS = {"percent", "pct", "growth", "rate", "mom", "yoy", "wow", "change_pct", "changepct", "growthrate"}
# Column name tokens for raw absolute change — secondary preference
_CHANGE_COL_TOKENS = {"change", "delta", "diff", "difference", "growth_abs", "absolutechange"}
# Column name tokens to deprioritise (previous period values add noise to MoM charts)
_PREV_COL_TOKENS = {"prev", "previous", "last", "prior", "lastmonth", "prevmonth"}


def _col_metric_priority(col_name: str) -> int:
    """
    Lower number = higher priority for metric selection.
      0 = percent/growth rate  (most meaningful for MoM/YoY charts)
      1 = absolute change
      2 = normal numeric column
      3 = previous-period column (deprioritised)
    """
    name = col_name.lower().replace("_", "").replace(" ", "")
    if any(t in name for t in _GROWTH_COL_TOKENS):
        return 0
    if any(t in name for t in _CHANGE_COL_TOKENS):
        return 1
    if any(t in name for t in _PREV_COL_TOKENS):
        return 3
    return 2


_DATE_TOKENS = ("DATE", "DAY", "WEEK", "MONTH", "YEAR", "QUARTER", "PERIOD", "QTR")

# Common English stop-words to strip before question-to-column matching
_STOP_WORDS = {
    "a", "an", "the", "for", "of", "in", "on", "at", "to", "by", "and",
    "or", "is", "are", "was", "were", "give", "show", "get", "what",
    "tell", "me", "us", "my", "last", "this", "past", "over", "with",
    "from", "how", "does", "do", "did", "its", "it", "their", "all",
    "chart", "graph", "plot", "trend", "daily", "weekly", "monthly",
    "quarterly", "day", "week", "month", "year", "days", "weeks", "months",
}


def _question_keywords(question: str) -> set[str]:
    """Lower-case tokens from the question, stop-words removed."""
    return {
        w.strip(".,?!:;\"'()").lower()
        for w in question.split()
    } - _STOP_WORDS


def _metric_sort_key(col: str, question_kws: set[str]) -> tuple[int, int]:
    """
    Two-level sort key for choosing the best metric column.

    Level 0 (primary)  — does the column name appear in the question?
        0 = yes (strong signal — user asked for this KPI by name)
        1 = no

    Level 1 (secondary) — structural priority (% / growth first, prev last)
    """
    col_tokens = {
        t.lower()
        for t in re.split(r"[_\s]+", col)
        if t
    } | {col.lower()}
    question_match = 0 if (col_tokens & question_kws) else 1
    return (question_match, _col_metric_priority(col))


def _label_sort_key(col: str, question_kws: set[str], is_date: bool) -> tuple[int, int, int]:
    """
    Three-level sort key for choosing the best X-axis / label column.

    Level 0 — is it a date/time column?
        When the question has trend intent, time columns always win.
        0 = time column, 1 = non-time
    Level 1 — does the column name appear in the question?
        0 = yes, 1 = no
    Level 2 — column position (leftmost first as tiebreaker)
    """
    time_rank = 0 if is_date else 1
    col_tokens = {
        t.lower()
        for t in re.split(r"[_\s]+", col)
        if t
    } | {col.lower()}
    question_match = 0 if (col_tokens & question_kws) else 1
    return (time_rank, question_match, 0)  # position tiebreaker added by caller


def _detect_column_roles(result: SqlExecutionResult, question: str = "") -> dict[str, int | None]:
    """
    Intelligently assign label / metric / date roles to result columns.

    - Metric  : prefers the column whose name appears in the question (e.g. "ABV" in
                "ABV trend by brand" → picks the ABV column, not NetBills).
    - Label   : for trend questions prefers the date/time column as the X-axis;
                for grouping questions prefers the dimension that matches the question.
    - Date    : first column whose name contains a time token (WEEK, MONTH, …).
    """
    numeric_indices: list[int] = []
    non_numeric_indices: list[int] = []

    for idx, col in enumerate(result.columns):
        values = [row[idx] for row in result.rows if len(row) > idx]
        if _is_mostly_numeric(values):
            numeric_indices.append(idx)
        else:
            non_numeric_indices.append(idx)

    # ── Date column detection ──────────────────────────────────────────────
    date_indices: set[int] = set()
    for idx in non_numeric_indices:
        name = result.columns[idx].upper()
        if any(token in name for token in _DATE_TOKENS):
            date_indices.add(idx)

    date_index: int | None = next(iter(date_indices), None)

    question_kws = _question_keywords(question)

    # ── Metric column selection ────────────────────────────────────────────
    metric_index: int | None = None
    if numeric_indices:
        metric_index = min(
            numeric_indices,
            key=lambda i: _metric_sort_key(result.columns[i], question_kws),
        )

    # ── Label / X-axis column selection ───────────────────────────────────
    label_index: int | None = None
    if non_numeric_indices:
        asks_trend = bool(
            {"trend", "over time", "daily", "weekly", "monthly", "quarterly"}
            & {w.lower() for w in question.split()}
        ) or date_index is not None

        def _lkey(idx: int) -> tuple[int, int, int]:
            col = result.columns[idx]
            is_date = idx in date_indices
            # For trend questions, strongly prefer date columns as X axis
            time_rank = 0 if (asks_trend and is_date) else (1 if is_date else 2)
            col_tokens = {
                t.lower()
                for t in re.split(r"[_\s]+", col)
                if t
            } | {col.lower()}
            question_match = 0 if (col_tokens & question_kws) else 1
            return (time_rank, question_match, idx)   # idx = position tiebreaker

        label_index = min(non_numeric_indices, key=_lkey)

    return {
        "label_index": label_index,
        "metric_index": metric_index,
        "date_index": date_index,
    }


def _is_mostly_numeric(values: list[Any]) -> bool:
    if not values:
        return False
    numeric_count = 0
    non_null_count = 0
    for value in values:
        if value is None:
            continue
        non_null_count += 1
        if _to_float(value) is not None:
            numeric_count += 1
    if non_null_count == 0:
        return False
    return (numeric_count / non_null_count) >= 0.8


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _smart_date_format(raw_values: list[Any]) -> str | None:
    """
    Inspect a list of label values; if they are all date-like, return the
    best strftime format based on their span:
      ≤ 62 days  → "%d %b"     e.g. "07 Apr"  (daily / weekly grain)
      ≤ 730 days → "%b '%y"    e.g. "Apr '26" (monthly grain)
      > 730 days → "%b %Y"     e.g. "Apr 2026" (quarterly / yearly)
    Returns None when the values are not date-like (use str() fallback).
    """
    parsed: list[date] = []
    for v in raw_values:
        if isinstance(v, datetime):
            parsed.append(v.date())
        elif isinstance(v, date):
            parsed.append(v)
        elif isinstance(v, str):
            try:
                parsed.append(datetime.fromisoformat(v.strip()[:10]).date())
            except (ValueError, AttributeError):
                return None   # not a parseable date string
        else:
            return None
    if not parsed:
        return None
    span = (max(parsed) - min(parsed)).days
    if span <= 62:
        return "%d %b"    # "07 Apr"
    elif span <= 730:
        return "%b '%y"   # "Apr '26"
    else:
        return "%b %Y"    # "Apr 2026"


def _label_str(value: Any, date_fmt: str | None = None) -> str:
    """Convert a SQL value to a chart-friendly label string.

    ``date_fmt`` is a strftime format string determined by the caller from the
    full set of label values (see ``_smart_date_format``).  When not provided
    the fallback is "%d %b" for dates so individual days are always readable.
    """
    if isinstance(value, (datetime, date)):
        fmt = date_fmt or "%d %b"
        return value.strftime(fmt)
    if isinstance(value, str) and date_fmt:
        try:
            return datetime.fromisoformat(value.strip()[:10]).strftime(date_fmt)
        except (ValueError, AttributeError):
            pass
    return str(value)


# Strip common filler phrases from the start of a question for chart titles.
# Order matters — longer patterns first so they're tried before their prefixes.
_TITLE_PREFIX_RE = re.compile(
    r"^(give\s+me\s+(?:a\s+)?|show\s+me\s+(?:a\s+)?|give\s+(?:a\s+)?|show\s+(?:a\s+)?|"
    r"get\s+me\s+(?:a\s+)?|get\s+(?:a\s+)?|"
    r"what\s+is\s+the\s+|what\s+are\s+the\s+|what\s+is\s+|what\s+are\s+|"
    r"tell\s+me\s+(?:the\s+)?|display\s+(?:the\s+)?|"
    r"plot\s+(?:a\s+|the\s+)?|visualize\s+|provide\s+|calculate\s+|find\s+)",
    re.IGNORECASE,
)
# After prefix removal, also strip leftover chart-type phrases like "pie chart of", "bar graph of"
_CHART_TYPE_RE = re.compile(
    r"^(a\s+)?(pie|bar|line|trend|area)\s+(chart|graph|plot)\s+(of\s+|for\s+)?",
    re.IGNORECASE,
)
# Abbreviations / acronyms to keep ALL-CAPS
_KEEP_UPPER = {"abv", "mtd", "ytd", "qtd", "yoy", "mom", "wow", "kpi", "sql",
               "uspa", "fy", "q1", "q2", "q3", "q4"}
# Small words that stay lowercase (unless first word in title)
_LOWERCASE_WORDS = {"a", "an", "the", "and", "or", "but", "for", "nor",
                    "of", "on", "in", "at", "by", "to", "up", "as", "vs"}


def _generate_chart_title(question: str) -> str:
    """
    Produce a clean, human-readable chart title from the user question.

    Steps:
      1. Strip common filler prefixes ("give me", "show", "what is", …).
      2. Strip leftover chart-type phrases ("pie chart of", "bar graph for", …).
      3. Title-case remaining words:
           - known acronyms → ALL-CAPS  (ABV, MTD, YoY, …)
           - small prepositions/articles → lowercase  (for, by, of, …)
           - everything else → Capitalised
      4. Always capitalise the first word.
      5. Trim to 80 chars.
    """
    title = question.strip()
    title = _TITLE_PREFIX_RE.sub("", title).strip()
    title = _CHART_TYPE_RE.sub("", title).strip()

    words = title.split()
    result_words: list[str] = []
    for i, w in enumerate(words):
        # Strip trailing punctuation for comparison, keep it for output
        punct = ""
        bare = w
        while bare and bare[-1] in ".,?!;:":
            punct = bare[-1] + punct
            bare = bare[:-1]
        bare_low = bare.lower()

        if bare_low in _KEEP_UPPER:
            result_words.append(bare.upper() + punct)
        elif i > 0 and bare_low in _LOWERCASE_WORDS:
            result_words.append(bare.lower() + punct)
        else:
            result_words.append(bare.capitalize() + punct)

    title = " ".join(result_words)
    collapsed = re.sub(r"\s+", " ", title).strip()
    # Ensure first character is always uppercase
    if collapsed:
        collapsed = collapsed[0].upper() + collapsed[1:]
    return collapsed[:80] if collapsed else "Chart"


def _normalize_text(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


def _safe_title(value: str) -> str:
    collapsed = re.sub(r"\s+", " ", value.strip())
    return collapsed[:120] if collapsed else "Chart"

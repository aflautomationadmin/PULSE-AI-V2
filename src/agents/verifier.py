from __future__ import annotations

import re

from src.models import SqlExecutionResult, VerificationIssue, VerificationResult

# ── Number format multipliers (longest suffix first to avoid partial matches) ─
_MULTIPLIERS: list[tuple[str, float]] = [
    # Western
    ("billion", 1e9),
    ("bn",      1e9),
    ("million", 1e6),
    ("mn",      1e6),
    # Indian
    ("crore",   1e7),
    ("cr",      1e7),
    ("lakh",    1e5),
    ("lac",     1e5),
    ("thousand", 1e3),
    ("k",       1e3),
    # "l" kept last — short and ambiguous, only match if nothing else did
    ("l",       1e5),
]

# Numbers in the answer that are under this value are likely row counts /
# ordinal references ("top 5", "3 stores") — skip verifying them.
_MIN_VERIFY_VALUE = 100


def _parse_number(token: str) -> float | None:
    """
    Parse a number token from the answer text into a float.
    Handles:
      - Plain numbers:           "4500000", "45,00,000"
      - Indian short forms:      "45L", "4.5Cr", "200K"
      - Currency prefix:         "₹45L", "Rs4.5Cr"
    Returns None if the token cannot be parsed.
    """
    cleaned = token.strip().replace(",", "").replace("₹", "").replace("Rs", "").replace("rs", "")
    lower = cleaned.lower()

    for suffix, mult in _MULTIPLIERS:
        if lower.endswith(suffix):
            base = cleaned[: -len(suffix)]
            try:
                return float(base) * mult
            except ValueError:
                return None

    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_numbers_from_text(text: str) -> list[tuple[str, float]]:
    """
    Return (raw_token, parsed_value) pairs for every number-like token in text.
    Skips percentages (those are derived, not raw data values).
    """
    # Match: optional ₹/Rs, digits with optional decimals, optional suffix
    pattern = re.compile(
        r"(?:₹|Rs\.?\s*)?"                                     # optional currency prefix
        r"(\d[\d,]*(?:\.\d+)?)"                                # number with optional commas/decimals
        r"\s*"
        r"(billion|bn|million|mn|crore|cr|lakh|lac|thousand|k|l)?"  # optional suffix
        r"(?!\s*%)",                                           # not followed by % (skip percentages)
        flags=re.IGNORECASE,
    )
    results: list[tuple[str, float]] = []
    for m in pattern.finditer(text):
        raw = m.group(0).strip()
        parsed = _parse_number(raw)
        if parsed is not None and parsed >= _MIN_VERIFY_VALUE:
            results.append((raw, parsed))
    return results


_YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def _collect_result_values(result: SqlExecutionResult) -> list[float]:
    """
    Flatten all numeric cell values from the SQL result.
    Stores the absolute value so that negative changes ("−40.3 million"
    written as "40.3 million" in the answer) can still be matched.
    """
    values: list[float] = []
    for row in result.rows:
        for cell in row:
            try:
                v = float(cell)
                av = abs(v)
                if av >= _MIN_VERIFY_VALUE:
                    values.append(av)
            except (TypeError, ValueError):
                pass
    return values


def _is_calendar_year(raw_token: str, parsed_val: float) -> bool:
    """True when the token looks like a 4-digit calendar year (1900–2100)."""
    digits_only = raw_token.replace(",", "").strip()
    return bool(_YEAR_RE.match(digits_only)) and 1900 <= parsed_val <= 2100


def _close_enough(answer_val: float, data_values: list[float], tolerance: float = 0.02) -> bool:
    """Return True if answer_val (or its absolute value) is within tolerance of any data value."""
    check = abs(answer_val)
    for dv in data_values:
        if dv == 0:
            continue
        if abs(check - dv) / dv <= tolerance:
            return True
    return False


def verify_answer(
    answer: str,
    result: SqlExecutionResult,
) -> VerificationResult:
    """
    Rule-based numeric verifier.

    Extracts every number from the answer text and checks whether it
    exists (within 2% tolerance) in the actual SQL result rows.
    Flags any number that cannot be matched as a VerificationIssue.
    """
    if not answer.strip() or result.row_count == 0:
        return VerificationResult(verified=True)

    data_values = _collect_result_values(result)
    if not data_values:
        # No numeric data to verify against
        return VerificationResult(verified=True)

    answer_numbers = _extract_numbers_from_text(answer)
    if not answer_numbers:
        return VerificationResult(verified=True)

    issues: list[VerificationIssue] = []
    seen: set[str] = set()   # avoid duplicate issue for same token

    for raw_token, parsed_val in answer_numbers:
        if raw_token in seen:
            continue
        seen.add(raw_token)

        # Skip calendar years — they appear as date labels, not data values
        if _is_calendar_year(raw_token, parsed_val):
            continue

        if not _close_enough(parsed_val, data_values):
            # Find the closest value in data for the issue message
            closest = min(data_values, key=lambda v: abs(v - parsed_val))
            issues.append(
                VerificationIssue(
                    number_in_answer=raw_token,
                    issue=(
                        f"'{raw_token}' (≈ {parsed_val:,.0f}) not found in result data. "
                        f"Closest value in data: {closest:,.0f}"
                    ),
                )
            )

    return VerificationResult(
        verified=len(issues) == 0,
        issues=issues,
    )

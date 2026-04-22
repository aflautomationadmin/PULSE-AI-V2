from __future__ import annotations

import re


class SqlGuardError(ValueError):
    """Raised when generated SQL violates read-only policy."""


_BLOCKED_PATTERNS = [
    r"\b(insert|update|delete|merge|drop|alter|create|truncate)\b",
    r"\b(exec|execute)\b",
    r"\b(grant|revoke|deny)\b",
    r"\b(backup|restore|dbcc|use|set)\b",
    r"\bsp_[a-z0-9_]*\b",
    r"\bxp_[a-z0-9_]*\b",
    r"\bselect\b[\s\S]*\binto\b",
]

_ALLOWED_TABLE = "prd.fact_sales_ai"


def _strip_sql_comments(sql: str) -> str:
    no_block_comments = re.sub(r"/\*[\s\S]*?\*/", " ", sql)
    no_line_comments = re.sub(r"--.*?$", " ", no_block_comments, flags=re.MULTILINE)
    return no_line_comments


def _normalize_identifier(identifier: str) -> str:
    return identifier.replace("[", "").replace("]", "").strip().lower()


def _extract_table_references(sql_text: str) -> list[str]:
    # Capture object names that immediately follow FROM/JOIN (ignores FROM (subquery)).
    return re.findall(
        r"\b(?:from|join)\s+([\[\]a-zA-Z0-9_\.]+)",
        sql_text,
        flags=re.IGNORECASE,
    )


def _extract_cte_names(sql_text: str) -> set[str]:
    names = re.findall(
        r"(?:\bwith|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(",
        sql_text,
        flags=re.IGNORECASE,
    )
    return {name.lower() for name in names}


def ensure_safe_readonly_sql(sql: str) -> str:
    if not sql or not sql.strip():
        raise SqlGuardError("SQL is empty")

    cleaned = _strip_sql_comments(sql).strip()
    if not cleaned:
        raise SqlGuardError("SQL is empty after removing comments")

    cleaned_no_trailing = cleaned[:-1].strip() if cleaned.endswith(";") else cleaned

    if ";" in cleaned_no_trailing:
        raise SqlGuardError("Multiple SQL statements are not allowed")

    lowered = cleaned_no_trailing.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise SqlGuardError("Only SELECT or WITH...SELECT queries are allowed")

    for pattern in _BLOCKED_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            raise SqlGuardError("SQL contains blocked keywords or patterns")

    cte_names = _extract_cte_names(cleaned_no_trailing)
    refs = [_normalize_identifier(ref) for ref in _extract_table_references(cleaned_no_trailing)]
    refs = [ref for ref in refs if ref]
    if not refs:
        raise SqlGuardError("SQL must reference a table in FROM/JOIN")

    for ref in refs:
        if ref in cte_names:
            continue
        if ref != _ALLOWED_TABLE:
            raise SqlGuardError("SQL can only query table prd.FACT_SALES_AI")

    return cleaned_no_trailing

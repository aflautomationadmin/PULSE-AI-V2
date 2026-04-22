from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from src.business_context import BusinessContext

_QUESTION_STOPWORDS = {
    "a",
    "an",
    "are",
    "as",
    "can",
    "could",
    "fetch",
    "for",
    "get",
    "give",
    "i",
    "is",
    "me",
    "my",
    "on",
    "please",
    "show",
    "tell",
    "the",
    "to",
    "was",
    "were",
    "what",
    "you",
}

_TOKEN_CANONICAL_MAP = {
    "yesterdaya": "yesterday",
    "yesterda": "yesterday",
    "yestarday": "yesterday",
    "todaya": "today",
    "tomorow": "tomorrow",
}


@dataclass
class SqlCacheHit:
    entry_id: int
    sql_text: str
    match_type: str
    similarity: float | None = None


@dataclass
class SqlCacheEntry:
    entry_id: int
    original_question: str
    normalized_question: str
    hit_count: int
    created_at: str
    last_success_at: str
    embedding: list[float] | None


def fingerprint_text(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def normalize_question_for_cache(question: str, context: BusinessContext) -> str:
    normalized = _normalize_text(question)
    if not normalized:
        return ""

    rules: list[tuple[str, str]] = []
    for mapping in context.brand_alias_mappings:
        canonical = _normalize_text(mapping.canonical_brand)
        if not canonical:
            continue
        all_aliases = [mapping.canonical_brand, *mapping.aliases]
        for alias in all_aliases:
            alias_normalized = _normalize_text(alias)
            if alias_normalized:
                rules.append((alias_normalized, canonical))

    for term, meaning in context.business_terms.items():
        key_norm = _normalize_text(term)
        value_norm = _normalize_text(meaning)
        if key_norm and value_norm:
            rules.append((key_norm, value_norm))

    # Replace longer alias phrases first to avoid short alias collisions.
    unique_rules = sorted(set(rules), key=lambda item: len(item[0]), reverse=True)
    output = f" {normalized} "
    for src, dst in unique_rules:
        pattern = rf"(?<!\w){re.escape(src)}(?!\w)"
        output = re.sub(pattern, dst, output)

    normalized_text = _normalize_text(output).lower()
    compact_tokens: list[str] = []
    for token in normalized_text.split():
        if token in _QUESTION_STOPWORDS:
            continue
        canonical = _canonicalize_token(token)
        if canonical and canonical not in _QUESTION_STOPWORDS:
            compact_tokens.append(canonical)

    if compact_tokens:
        # Make cache key order-insensitive for short analytic phrasings
        # like "yesterday sales" vs "sales yesterday".
        return " ".join(sorted(compact_tokens))
    return normalized_text


class SqlQueryCache:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def find_exact(
        self,
        *,
        normalized_question: str,
        schema_fingerprint: str,
        business_fingerprint: str,
    ) -> SqlCacheHit | None:
        if not normalized_question:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, sql_text
                FROM sql_query_cache
                WHERE normalized_question = ?
                  AND schema_fingerprint = ?
                  AND business_fingerprint = ?
                ORDER BY last_success_at DESC
                LIMIT 1
                """,
                (normalized_question, schema_fingerprint, business_fingerprint),
            ).fetchone()
            if row is None:
                return None

            entry_id = int(row[0])
            conn.execute(
                "UPDATE sql_query_cache SET hit_count = hit_count + 1 WHERE id = ?",
                (entry_id,),
            )
            conn.commit()
            return SqlCacheHit(entry_id=entry_id, sql_text=str(row[1]), match_type="exact")

    def find_semantic(
        self,
        *,
        embedding_vector: list[float],
        schema_fingerprint: str,
        business_fingerprint: str,
        min_similarity: float,
        max_candidates: int = 100,
    ) -> SqlCacheHit | None:
        if not embedding_vector:
            return None

        best: tuple[int, str, float] | None = None
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, sql_text, question_embedding
                FROM sql_query_cache
                WHERE schema_fingerprint = ?
                  AND business_fingerprint = ?
                  AND question_embedding IS NOT NULL
                ORDER BY last_success_at DESC
                LIMIT ?
                """,
                (schema_fingerprint, business_fingerprint, int(max_candidates)),
            ).fetchall()

            for row in rows:
                raw_embedding = row[2]
                if not isinstance(raw_embedding, str) or not raw_embedding:
                    continue

                try:
                    candidate_raw = json.loads(raw_embedding)
                    candidate_embedding = [float(value) for value in candidate_raw]
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue

                similarity = _cosine_similarity(embedding_vector, candidate_embedding)
                if similarity < min_similarity:
                    continue
                if best is None or similarity > best[2]:
                    best = (int(row[0]), str(row[1]), similarity)

            if best is None:
                return None

            conn.execute(
                "UPDATE sql_query_cache SET hit_count = hit_count + 1 WHERE id = ?",
                (best[0],),
            )
            conn.commit()
            return SqlCacheHit(
                entry_id=best[0],
                sql_text=best[1],
                match_type="semantic",
                similarity=best[2],
            )

    def upsert(
        self,
        *,
        normalized_question: str,
        original_question: str,
        sql_text: str,
        schema_fingerprint: str,
        business_fingerprint: str,
        question_embedding: list[float] | None = None,
        used_columns: Iterable[str] | None = None,
    ) -> None:
        if not normalized_question or not sql_text.strip():
            return

        now = datetime.now(timezone.utc).isoformat()
        sql_fingerprint = fingerprint_text(sql_text.lower())
        embedding_json = None
        if question_embedding:
            embedding_json = json.dumps([float(value) for value in question_embedding])

        columns_json = None
        if used_columns:
            normalized_columns = sorted(
                {
                    str(column).strip().lower()
                    for column in used_columns
                    if str(column).strip()
                }
            )
            if normalized_columns:
                columns_json = json.dumps(normalized_columns)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sql_query_cache (
                    normalized_question,
                    original_question,
                    question_embedding,
                    sql_text,
                    schema_fingerprint,
                    business_fingerprint,
                    sql_fingerprint,
                    used_columns,
                    hit_count,
                    created_at,
                    last_success_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(normalized_question, schema_fingerprint, business_fingerprint, sql_fingerprint)
                DO UPDATE SET
                    original_question=excluded.original_question,
                    question_embedding=COALESCE(excluded.question_embedding, sql_query_cache.question_embedding),
                    sql_text=excluded.sql_text,
                    used_columns=COALESCE(excluded.used_columns, sql_query_cache.used_columns),
                    last_success_at=excluded.last_success_at
                """,
                (
                    normalized_question,
                    original_question.strip(),
                    embedding_json,
                    sql_text.strip(),
                    schema_fingerprint,
                    business_fingerprint,
                    sql_fingerprint,
                    columns_json,
                    now,
                    now,
                ),
            )
            conn.commit()

    def list_recent_entries(self, *, limit: int = 20) -> list[SqlCacheEntry]:
        row_limit = max(1, int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    original_question,
                    normalized_question,
                    hit_count,
                    created_at,
                    last_success_at,
                    question_embedding
                FROM sql_query_cache
                ORDER BY last_success_at DESC
                LIMIT ?
                """,
                (row_limit,),
            ).fetchall()

        entries: list[SqlCacheEntry] = []
        for row in rows:
            embedding = None
            raw_embedding = row["question_embedding"]
            if isinstance(raw_embedding, str) and raw_embedding:
                try:
                    parsed = json.loads(raw_embedding)
                    if isinstance(parsed, list):
                        embedding = [float(value) for value in parsed]
                except (ValueError, TypeError, json.JSONDecodeError):
                    embedding = None

            entries.append(
                SqlCacheEntry(
                    entry_id=int(row["id"]),
                    original_question=str(row["original_question"]),
                    normalized_question=str(row["normalized_question"]),
                    hit_count=int(row["hit_count"]),
                    created_at=str(row["created_at"]),
                    last_success_at=str(row["last_success_at"]),
                    embedding=embedding,
                )
            )
        return entries

    def clear_all(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sql_query_cache")
            conn.commit()

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sql_query_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    normalized_question TEXT NOT NULL,
                    original_question TEXT NOT NULL,
                    question_embedding TEXT,
                    sql_text TEXT NOT NULL,
                    schema_fingerprint TEXT NOT NULL,
                    business_fingerprint TEXT NOT NULL,
                    sql_fingerprint TEXT NOT NULL,
                    used_columns TEXT,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_success_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_sql_cache_unique
                ON sql_query_cache (
                    normalized_question,
                    schema_fingerprint,
                    business_fingerprint,
                    sql_fingerprint
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sql_cache_exact
                ON sql_query_cache (normalized_question, schema_fingerprint, business_fingerprint)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sql_cache_recent
                ON sql_query_cache (last_success_at DESC)
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection


def _normalize_text(value: str) -> str:
    if not value:
        return ""
    text = re.sub(r"[^A-Za-z0-9]+", " ", value.upper())
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _canonicalize_token(token: str) -> str:
    if not token:
        return ""

    if token in _TOKEN_CANONICAL_MAP:
        return _TOKEN_CANONICAL_MAP[token]

    if token.startswith("yesterday"):
        return "yesterday"
    if token.startswith("today"):
        return "today"
    if token.startswith("tomorrow"):
        return "tomorrow"

    return token


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for lhs, rhs in zip(a, b, strict=True):
        dot += lhs * rhs
        norm_a += lhs * lhs
        norm_b += rhs * rhs
    if norm_a <= 0.0 or norm_b <= 0.0:
        return -1.0
    return dot / ((norm_a ** 0.5) * (norm_b ** 0.5))

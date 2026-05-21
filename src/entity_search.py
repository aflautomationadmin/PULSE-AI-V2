from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from src.db.connection import get_connection
from src.llm import run_embedding, run_embeddings

logger = logging.getLogger(__name__)
_EMBEDDING_BATCH_SIZE = 96

_ENTITY_FUZZY_STOPWORDS = {
    "A",
    "AN",
    "AND",
    "AS",
    "AT",
    "BY",
    "FOR",
    "FROM",
    "IN",
    "IS",
    "OF",
    "ON",
    "THE",
    "TO",
    "WAS",
    "WHAT",
    "YESTERDAY",
    "TODAY",
    "TOMORROW",
    "SALE",
    "SALES",
    "AMOUNT",
    "VALUE",
    "STORE",
}


@dataclass
class EntityMatch:
    column: str
    value: str
    score: float
    source: str = "embedding"


class ColumnEntityResolver:
    def __init__(
        self,
        *,
        cache_path: Path,
        cache_ttl_seconds: int,
        similarity_threshold: float,
        embedding_model: str | None,
        column_name: str,
        use_value_embeddings: bool = True,
    ) -> None:
        self.cache_path = cache_path
        self.cache_ttl_seconds = max(60, int(cache_ttl_seconds))
        self.similarity_threshold = float(similarity_threshold)
        self.fuzzy_threshold = max(0.88, self.similarity_threshold)
        self.embedding_model = embedding_model
        self.column_name = _sanitize_identifier(column_name)
        self.use_value_embeddings = bool(use_value_embeddings)
        self._index: list[tuple[str, list[float]]] | None = None
        self._ensure_storage()

    def resolve(self, question: str) -> EntityMatch | None:
        if not question.strip():
            return None

        index = self._get_index()
        if not index:
            return None

        question_embedding = (
            self._embed(question)
            if self.embedding_model and self.use_value_embeddings
            else None
        )
        best_semantic_value = ""
        best_semantic_score = -1.0

        if question_embedding:
            for entity_value, entity_embedding in index:
                score = _cosine_similarity(question_embedding, entity_embedding)
                if score > best_semantic_score:
                    best_semantic_score = score
                    best_semantic_value = entity_value

        if best_semantic_score >= self.similarity_threshold and best_semantic_value:
            return EntityMatch(
                column=self.column_name,
                value=best_semantic_value,
                score=best_semantic_score,
                source="embedding",
            )

        values = [entity_value for entity_value, _ in index]
        fuzzy_value, fuzzy_score = _best_fuzzy_match(question, values)
        if fuzzy_score >= self.fuzzy_threshold and fuzzy_value:
            return EntityMatch(
                column=self.column_name,
                value=fuzzy_value,
                score=fuzzy_score,
                source="fuzzy",
            )

        if best_semantic_score >= self.fuzzy_threshold and best_semantic_value:
            return EntityMatch(
                column=self.column_name,
                value=best_semantic_value,
                score=best_semantic_score,
                source="embedding",
            )

        return None

    def _get_index(self) -> list[tuple[str, list[float]]]:
        if self._index is not None:
            return self._index

        cached = self._read_cache_if_fresh()
        if cached is not None:
            self._index = cached
            return self._index

        built = self._build_index_from_db()
        self._index = built
        if built:
            self._write_cache(built)
        return self._index

    def _build_index_from_db(self) -> list[tuple[str, list[float]]]:
        values = self._fetch_distinct_values()
        if not values:
            return []

        if not self.embedding_model or not self.use_value_embeddings:
            return [(entity_value, []) for entity_value in values]

        entries: list[tuple[str, list[float]]] = []
        for idx in range(0, len(values), _EMBEDDING_BATCH_SIZE):
            batch = values[idx : idx + _EMBEDDING_BATCH_SIZE]
            try:
                embeddings = run_embeddings(texts=batch, model=self.embedding_model)
            except Exception:
                logger.exception("Failed to embed %s entity batch", self.column_name)
                embeddings = []

            if len(embeddings) != len(batch):
                logger.warning(
                    "Embedding count mismatch for %s: expected %s, got %s",
                    self.column_name,
                    len(batch),
                    len(embeddings),
                )
                embeddings = [[] for _ in batch]

            entries.extend(zip(batch, embeddings, strict=False))
        return entries

    def _fetch_distinct_values(self) -> list[str]:
        query = f"""
        SELECT DISTINCT UPPER(LTRIM(RTRIM({self.column_name}))) AS ENTITY_VALUE
        FROM prd.FACT_SALES_AI
        WHERE {self.column_name} IS NOT NULL
          AND LTRIM(RTRIM({self.column_name})) <> ''
        ORDER BY ENTITY_VALUE
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query)
                rows = cursor.fetchall()
        except Exception:
            logger.exception("Failed to fetch distinct entity values for %s", self.column_name)
            return []

        values: list[str] = []
        for row in rows:
            value = str(row[0]).strip().upper()
            if value:
                values.append(value)
        return values

    def _embed(self, text: str) -> list[float] | None:
        try:
            return run_embedding(text=text, model=self.embedding_model)
        except Exception:
            logger.exception("Failed to embed question for %s resolver", self.column_name)
            return None

    def _connect(self) -> sqlite3.Connection:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.cache_path)

    def _ensure_storage(self) -> None:
        try:
            self._create_schema()
            self._migrate_legacy_state_table_if_needed()
        except sqlite3.DatabaseError:
            legacy_rows, legacy_model, legacy_generated_epoch = self._read_legacy_json_cache()
            target_path = self.cache_path
            try:
                if self.cache_path.exists():
                    backup_path = self.cache_path.with_suffix(self.cache_path.suffix + ".legacy-json")
                    if backup_path.exists():
                        backup_path.unlink()
                    self.cache_path.rename(backup_path)
            except OSError:
                target_path = self.cache_path.with_suffix(self.cache_path.suffix + ".sqlite3")

            self.cache_path = target_path
            self._create_schema()
            self._migrate_legacy_state_table_if_needed()
            if legacy_rows and self.column_name == "STATE":
                self._write_rows(
                    rows=legacy_rows,
                    model=legacy_model,
                    generated_at_epoch=legacy_generated_epoch,
                )

    def _create_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entity_embedding_cache (
                    column_name TEXT NOT NULL,
                    entity_value TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    embedding_model TEXT NOT NULL,
                    generated_at_epoch INTEGER NOT NULL,
                    PRIMARY KEY (column_name, entity_value, embedding_model)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_entity_embedding_model_time
                ON entity_embedding_cache (column_name, embedding_model, generated_at_epoch DESC)
                """
            )
            conn.commit()

    def _migrate_legacy_state_table_if_needed(self) -> None:
        if self.column_name != "STATE":
            return

        with self._connect() as conn:
            legacy_exists = conn.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type='table' AND name='state_embedding_cache'
                """
            ).fetchone()
            if legacy_exists is None:
                return

            rows = conn.execute(
                """
                SELECT state_value, embedding_json, embedding_model, generated_at_epoch
                FROM state_embedding_cache
                """
            ).fetchall()
            if not rows:
                return

            conn.executemany(
                """
                INSERT OR IGNORE INTO entity_embedding_cache (
                    column_name,
                    entity_value,
                    embedding_json,
                    embedding_model,
                    generated_at_epoch
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [("STATE", row[0], row[1], row[2], row[3]) for row in rows],
            )
            conn.commit()

    def _read_legacy_json_cache(self) -> tuple[list[tuple[str, list[float]]], str, int]:
        if self.column_name != "STATE":
            return [], "", int(datetime.now(timezone.utc).timestamp())
        if not self.cache_path.exists():
            return [], "", int(datetime.now(timezone.utc).timestamp())

        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception:
            return [], "", int(datetime.now(timezone.utc).timestamp())

        rows_raw = payload.get("rows")
        if not isinstance(rows_raw, list):
            return [], "", int(datetime.now(timezone.utc).timestamp())

        model = str(payload.get("embedding_model") or "")
        generated_at_raw = payload.get("generated_at")
        try:
            generated_epoch = int(datetime.fromisoformat(str(generated_at_raw)).timestamp())
        except Exception:
            generated_epoch = int(datetime.now(timezone.utc).timestamp())

        parsed: list[tuple[str, list[float]]] = []
        for row in rows_raw:
            if not isinstance(row, dict):
                continue
            value = row.get("value")
            embedding = row.get("embedding")
            if not isinstance(value, str) or not isinstance(embedding, list):
                continue
            try:
                parsed_embedding = [float(item) for item in embedding]
            except (TypeError, ValueError):
                continue
            parsed.append((value, parsed_embedding))

        return parsed, model, generated_epoch

    def _read_cache_if_fresh(self) -> list[tuple[str, list[float]]] | None:
        model = self.embedding_model or ""
        cutoff_epoch = int(datetime.now(timezone.utc).timestamp()) - self.cache_ttl_seconds

        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT entity_value, embedding_json
                    FROM entity_embedding_cache
                    WHERE column_name = ?
                      AND embedding_model = ?
                      AND generated_at_epoch >= ?
                    ORDER BY entity_value
                    """,
                    (self.column_name, model, cutoff_epoch),
                ).fetchall()
        except sqlite3.DatabaseError:
            return None

        if not rows:
            return None

        parsed: list[tuple[str, list[float]]] = []
        for entity_value, embedding_json in rows:
            if not isinstance(entity_value, str) or not isinstance(embedding_json, str):
                continue
            try:
                raw_embedding = json.loads(embedding_json)
                if not isinstance(raw_embedding, list):
                    continue
                parsed_embedding = [float(item) for item in raw_embedding]
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            parsed.append((entity_value, parsed_embedding))

        return parsed or None

    def _write_cache(self, rows: list[tuple[str, list[float]]]) -> None:
        self._write_rows(
            rows=rows,
            model=self.embedding_model or "",
            generated_at_epoch=int(datetime.now(timezone.utc).timestamp()),
        )

    def _write_rows(
        self,
        *,
        rows: list[tuple[str, list[float]]],
        model: str,
        generated_at_epoch: int,
    ) -> None:
        if not rows:
            return

        serialized_rows = [
            (
                self.column_name,
                value,
                json.dumps([float(item) for item in embedding]),
                model,
                generated_at_epoch,
            )
            for value, embedding in rows
            if value
        ]
        if not serialized_rows:
            return

        with self._connect() as conn:
            conn.execute(
                "DELETE FROM entity_embedding_cache WHERE column_name = ? AND embedding_model = ?",
                (self.column_name, model),
            )
            conn.executemany(
                """
                INSERT OR REPLACE INTO entity_embedding_cache (
                    column_name,
                    entity_value,
                    embedding_json,
                    embedding_model,
                    generated_at_epoch
                ) VALUES (?, ?, ?, ?, ?)
                """,
                serialized_rows,
            )
            conn.commit()


class StateEntityResolver(ColumnEntityResolver):
    def __init__(
        self,
        *,
        cache_path: Path,
        cache_ttl_seconds: int,
        similarity_threshold: float,
        embedding_model: str | None,
    ) -> None:
        super().__init__(
            cache_path=cache_path,
            cache_ttl_seconds=cache_ttl_seconds,
            similarity_threshold=similarity_threshold,
            embedding_model=embedding_model,
            column_name="STATE",
        )


class CityEntityResolver(ColumnEntityResolver):
    def __init__(
        self,
        *,
        cache_path: Path,
        cache_ttl_seconds: int,
        similarity_threshold: float,
        embedding_model: str | None,
    ) -> None:
        super().__init__(
            cache_path=cache_path,
            cache_ttl_seconds=cache_ttl_seconds,
            similarity_threshold=similarity_threshold,
            embedding_model=embedding_model,
            column_name="CITY",
        )


class StoreNameEntityResolver(ColumnEntityResolver):
    def __init__(
        self,
        *,
        cache_path: Path,
        cache_ttl_seconds: int,
        similarity_threshold: float,
        embedding_model: str | None,
    ) -> None:
        super().__init__(
            cache_path=cache_path,
            cache_ttl_seconds=cache_ttl_seconds,
            similarity_threshold=similarity_threshold,
            embedding_model=embedding_model,
            column_name="STORE_NAME",
            use_value_embeddings=False,
        )


class CategoryEntityResolver(ColumnEntityResolver):
    def __init__(
        self,
        *,
        cache_path: Path,
        cache_ttl_seconds: int,
        similarity_threshold: float,
        embedding_model: str | None,
    ) -> None:
        super().__init__(
            cache_path=cache_path,
            cache_ttl_seconds=cache_ttl_seconds,
            similarity_threshold=similarity_threshold,
            embedding_model=embedding_model,
            column_name="CATEGORY",
            # Embedding enabled: category list is small enough to index quickly,
            # and semantic matching helps catch synonyms (e.g. "casual wear" → "CASUAL").
            use_value_embeddings=True,
        )


class SubclassEntityResolver(ColumnEntityResolver):
    def __init__(
        self,
        *,
        cache_path: Path,
        cache_ttl_seconds: int,
        similarity_threshold: float,
        embedding_model: str | None,
    ) -> None:
        super().__init__(
            cache_path=cache_path,
            cache_ttl_seconds=cache_ttl_seconds,
            similarity_threshold=similarity_threshold,
            embedding_model=embedding_model,
            column_name="SUBCLASS",
            # Fuzzy-only: embedding the full subclass value list (often 300-500 values)
            # causes hundreds of sequential API calls, blocking the pipeline for minutes.
            # Fuzzy matching handles misspellings (e.g. "Jogers" → "JOGGERS") without
            # any API calls.
            use_value_embeddings=False,
        )


def _sanitize_identifier(value: str) -> str:
    identifier = value.strip().upper()
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", identifier):
        raise ValueError(f"Invalid SQL identifier: {value}")
    return identifier


def _normalize_for_fuzzy(text: str) -> str:
    cleaned = re.sub(r"[^A-Z0-9]+", " ", text.upper())
    return " ".join(cleaned.split())


def _candidate_phrases(question: str, max_tokens: int = 5) -> list[str]:
    normalized = _normalize_for_fuzzy(question)
    if not normalized:
        return []
    tokens = [token for token in normalized.split() if token not in _ENTITY_FUZZY_STOPWORDS]
    if not tokens:
        return []
    phrases: list[str] = [normalized]
    upper_bound = min(max_tokens, len(tokens))
    for size in range(1, upper_bound + 1):
        for idx in range(0, len(tokens) - size + 1):
            phrases.append(" ".join(tokens[idx : idx + size]))
    return phrases


def _best_fuzzy_match(question: str, values: list[str]) -> tuple[str, float]:
    if not values:
        return "", -1.0

    normalized_question = _normalize_for_fuzzy(question)
    if not normalized_question:
        return "", -1.0

    phrases = _candidate_phrases(question)
    best_value = ""
    best_score = -1.0

    for value in values:
        normalized_value = _normalize_for_fuzzy(value)
        if not normalized_value:
            continue
        if normalized_value in normalized_question:
            return value, 1.0

        for phrase in phrases:
            if phrase and len(phrase) >= 4 and phrase in normalized_value:
                phrase_tokens = len(phrase.split())
                if phrase_tokens >= 2:
                    score = 0.9
                else:
                    score = min(0.75, len(phrase) / max(len(normalized_value), 1))
                if score > best_score:
                    best_score = score
                    best_value = value
            score = SequenceMatcher(None, phrase, normalized_value).ratio()
            if score > best_score:
                best_score = score
                best_value = value

    return best_value, best_score


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

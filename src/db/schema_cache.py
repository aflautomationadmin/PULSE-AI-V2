from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import get_settings
from src.db.connection import get_connection
from src.db.schema_introspect import introspect_schema_context


class SchemaCache:
    def __init__(
        self,
        cache_path: Path | str | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        if cache_path is None or ttl_seconds is None:
            settings = get_settings()
            self.cache_path = Path(cache_path or settings.schema_cache_path)
            self.ttl_seconds = int(ttl_seconds or settings.schema_cache_ttl_seconds)
        else:
            self.cache_path = Path(cache_path)
            self.ttl_seconds = int(ttl_seconds)

    def get_schema_context(self, force_refresh: bool = False) -> str:
        if not force_refresh:
            cached = self._read_cache_if_fresh()
            if cached:
                return cached

        return self.refresh()

    def refresh(self) -> str:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

        with closing(get_connection()) as connection:
            schema_context = introspect_schema_context(connection)

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "schema_context": schema_context,
        }
        self.cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return schema_context

    def _read_cache_if_fresh(self) -> str | None:
        if not self.cache_path.exists():
            return None

        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            generated_at = datetime.fromisoformat(payload["generated_at"])
            schema_context = str(payload["schema_context"])
        except Exception:
            return None

        age = datetime.now(timezone.utc) - generated_at
        if age > timedelta(seconds=self.ttl_seconds):
            return None

        return schema_context

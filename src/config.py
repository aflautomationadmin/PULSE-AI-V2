from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    llm_model: str
    embedding_model: str | None
    openai_api_key: str | None
    anthropic_api_key: str | None
    db_server: str
    db_database: str
    db_username: str
    db_password: str
    db_driver: str
    schema_cache_path: Path
    schema_cache_ttl_seconds: int
    max_result_rows: int
    preview_rows: int
    business_context_path: Path
    memory_max_turns: int
    memory_store_path: Path
    memory_default_thread: str
    memory_auto_create_thread: bool
    sql_cache_enabled: bool
    sql_cache_path: Path
    sql_cache_semantic_enabled: bool
    sql_cache_similarity_threshold: float
    sql_debug_max_retries: int
    entity_search_enabled: bool
    entity_state_cache_path: Path
    entity_state_cache_ttl_seconds: int
    entity_state_similarity_threshold: float
    # MongoDB thread storage (optional — falls back to JSON file when not set)
    mongo_uri: str | None = None
    mongo_db_name: str = "ai_da_agents"
    mongo_collection: str = "conversation_threads"
    # User identity — overridden at runtime by the authenticated user's email.
    # Falls back to "anonymous" only when auth is disabled.
    mongo_user_id: str = "anonymous"
    # Azure AD — required for JWT validation when auth is enabled
    azure_tenant_id: str = ""   # AZURE_TENANT_ID
    azure_client_id: str = ""   # AZURE_CLIENT_ID
    # Admin portal — emails allowed to view all users' conversations
    admin_emails: tuple[str, ...] = ()   # ADMIN_EMAILS (comma-separated)
    # Langfuse observability (optional — silently disabled when keys are absent)
    langfuse_public_key: str = ""   # LANGFUSE_PUBLIC_KEY
    langfuse_secret_key: str = ""   # LANGFUSE_SECRET_KEY
    langfuse_host: str = "https://cloud.langfuse.com"  # LANGFUSE_HOST
    llm_timeout_seconds: int = 45
    embedding_timeout_seconds: int = 30
    visualization_enabled: bool = True
    chart_theme_path: Path = Path("chart_theme.json")
    chart_output_dir: Path = Path(".cache/charts")
    chart_max_points: int = 50


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _optional_env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _csv_email_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Parse a comma-separated list of emails into a lowercase tuple."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return tuple(e.strip().lower() for e in raw.split(",") if e.strip())


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv()

    cache_path_raw = os.getenv("SCHEMA_CACHE_PATH", ".cache/schema_cache.json")
    cache_path = Path(cache_path_raw)
    business_context_path = Path(os.getenv("BUSINESS_CONTEXT_PATH", "business_context.json"))
    llm_model = os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL", "openai/gpt-4.1-mini")
    llm_timeout_seconds = _int_env("LLM_TIMEOUT_SECONDS", 45)
    embedding_model = _optional_env("EMBEDDING_MODEL") or "openai/text-embedding-3-small"
    embedding_timeout_seconds = _int_env("EMBEDDING_TIMEOUT_SECONDS", 30)
    memory_store_path = Path(os.getenv("MEMORY_STORE_PATH", ".cache/memory_threads.json"))
    memory_default_thread = os.getenv("MEMORY_DEFAULT_THREAD", "default").strip() or "default"
    sql_cache_path = Path(os.getenv("SQL_CACHE_PATH", ".cache/sql_query_cache.sqlite3"))
    entity_state_cache_path = Path(
        os.getenv("ENTITY_STATE_CACHE_PATH", ".cache/state_entity_index.sqlite3")
    )
    chart_theme_path = Path(os.getenv("CHART_THEME_PATH", "chart_theme.json"))
    chart_output_dir = Path(os.getenv("CHART_OUTPUT_DIR", ".cache/charts"))

    return Settings(
        llm_model=llm_model,
        llm_timeout_seconds=llm_timeout_seconds,
        embedding_model=embedding_model,
        embedding_timeout_seconds=embedding_timeout_seconds,
        openai_api_key=_optional_env("OPENAI_API_KEY"),
        anthropic_api_key=_optional_env("ANTHROPIC_API_KEY"),
        mongo_uri=_optional_env("MONGO_URI"),
        mongo_db_name=os.getenv("MONGO_DB_NAME", "ai_da_agents"),
        mongo_collection=os.getenv("MONGO_COLLECTION", "conversation_threads"),
        mongo_user_id=os.getenv("MONGO_USER_ID", "anonymous"),
        azure_tenant_id=os.getenv("AZURE_TENANT_ID", "").strip(),
        azure_client_id=os.getenv("AZURE_CLIENT_ID", "").strip(),
        admin_emails=_csv_email_env(
            "ADMIN_EMAILS",
            ("mohit.lokhande@arvindfashions.com", "radhakishan.thakur@arvindfashions.com"),
        ),
        langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY", "").strip(),
        langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY", "").strip(),
        langfuse_host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com").strip(),
        db_server=_required_env("DB_SERVER"),
        db_database=_required_env("DB_DATABASE"),
        db_username=_required_env("DB_USERNAME"),
        db_password=_required_env("DB_PASSWORD"),
        db_driver=os.getenv("DB_DRIVER", "{ODBC Driver 18 for SQL Server}"),
        schema_cache_path=cache_path,
        schema_cache_ttl_seconds=_int_env("SCHEMA_CACHE_TTL_SECONDS", 3600),
        max_result_rows=_int_env("MAX_RESULT_ROWS", 200),
        preview_rows=_int_env("PREVIEW_ROWS", 10),
        business_context_path=business_context_path,
        memory_max_turns=_int_env("MEMORY_MAX_TURNS", 12),
        memory_store_path=memory_store_path,
        memory_default_thread=memory_default_thread,
        memory_auto_create_thread=_bool_env("MEMORY_AUTO_CREATE_THREAD", True),
        sql_cache_enabled=_bool_env("SQL_CACHE_ENABLED", True),
        sql_cache_path=sql_cache_path,
        sql_cache_semantic_enabled=_bool_env("SQL_CACHE_SEMANTIC_ENABLED", True),
        sql_cache_similarity_threshold=_float_env("SQL_CACHE_SIMILARITY_THRESHOLD", 0.92),
        sql_debug_max_retries=_int_env("SQL_DEBUG_MAX_RETRIES", 1),
        entity_search_enabled=_bool_env("ENTITY_SEARCH_ENABLED", True),
        entity_state_cache_path=entity_state_cache_path,
        entity_state_cache_ttl_seconds=_int_env("ENTITY_STATE_CACHE_TTL_SECONDS", 86400),
        entity_state_similarity_threshold=_float_env("ENTITY_STATE_SIMILARITY_THRESHOLD", 0.86),
        visualization_enabled=_bool_env("VISUALIZATION_ENABLED", True),
        chart_theme_path=chart_theme_path,
        chart_output_dir=chart_output_dir,
        chart_max_points=_int_env("CHART_MAX_POINTS", 50),
    )

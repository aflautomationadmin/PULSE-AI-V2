from __future__ import annotations

import json
import os
import re
from collections.abc import Generator
from typing import Any
from typing import TypeVar

import litellm
from pydantic import BaseModel, ValidationError

from src.config import get_settings
from src.tracing import current_trace_id, current_user_id

# ── Langfuse auto-logging via LiteLLM callback ────────────────────────────────
# Activates once on import. Every completion() / embedding() call is logged to
# Langfuse automatically when LANGFUSE_* env vars are set.
litellm.success_callback = ["langfuse"]


def _lf_meta(generation_name: str) -> dict:
    """Build the metadata dict that links a LiteLLM call to the current trace."""
    meta: dict = {"generation_name": generation_name}
    tid = current_trace_id()
    if tid:
        meta["trace_id"]      = tid
        meta["trace_user_id"] = current_user_id() or "anonymous"
    return meta

T = TypeVar("T", bound=BaseModel)


class LlmRuntimeError(RuntimeError):
    """Raised when LLM runtime fails."""


def _raise_runtime_error(exc: Exception) -> None:
    message = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in message or "SSL:" in message:
        raise LlmRuntimeError(
            "LLM connection failed due to TLS certificate verification. "
            "Set SSL_CERT_FILE in .env to your organization root CA bundle path "
            "(or SSL_CERT_DIR for a cert directory), then restart the app."
        ) from exc

    if "Connection error" in message or "APIConnectionError" in message:
        raise LlmRuntimeError(
            "LLM connection failed. Check network/proxy access and "
            "TLS certificate settings."
        ) from exc

    raise LlmRuntimeError(message) from exc


def _load_litellm_completion():
    try:
        from litellm import completion  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise LlmRuntimeError(
            "LiteLLM is not installed. Install dependencies from requirements.txt"
        ) from exc
    return completion


def _load_litellm_embedding():
    try:
        from litellm import embedding  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise LlmRuntimeError(
            "LiteLLM is not installed. Install dependencies from requirements.txt"
        ) from exc
    return embedding


def _resolve_model(model: str | None) -> str:
    settings = get_settings()
    selected = (model or settings.llm_model).strip()
    if not selected:
        raise LlmRuntimeError("Missing LLM model. Set LLM_MODEL in .env.")
    return selected


def _configure_provider_keys() -> None:
    settings = get_settings()
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    if settings.anthropic_api_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)


def _build_messages(instructions: str, user_input: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": instructions},
        {"role": "user", "content": user_input},
    ]


def _extract_text_content(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices")

    if choices:
        first = choices[0]
        message = getattr(first, "message", None)
        if message is None and isinstance(first, dict):
            message = first.get("message")

        content = None
        if message is not None:
            content = getattr(message, "content", None)
            if content is None and isinstance(message, dict):
                content = message.get("content")

        if content is None:
            content = getattr(first, "text", None)
            if content is None and isinstance(first, dict):
                content = first.get("text")

        if isinstance(content, str) and content.strip():
            return content.strip()

        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                    continue
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            final = "\n".join(part for part in parts if part).strip()
            if final:
                return final

    output_text = getattr(response, "output_text", None)
    if output_text is None and isinstance(response, dict):
        output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    raise LlmRuntimeError("LLM response did not contain text content")


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    candidates: list[str] = []
    text = raw_text.strip()
    if text:
        candidates.append(text)

    fenced_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw_text, flags=re.IGNORECASE)
    if fenced_match:
        candidates.insert(0, fenced_match.group(1).strip())

    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(raw_text[start : end + 1].strip())

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise LlmRuntimeError("LLM JSON output was not a valid JSON object")


def run_text_agent(
    *,
    agent_name: str,
    instructions: str,
    user_input: str,
    model: str | None = None,
) -> str:
    _ = agent_name
    settings = get_settings()
    selected_model = _resolve_model(model)
    _configure_provider_keys()
    completion = _load_litellm_completion()

    try:
        result = completion(
            model=selected_model,
            messages=_build_messages(instructions, user_input),
            temperature=0,
            timeout=float(max(1, settings.llm_timeout_seconds)),
            metadata=_lf_meta(agent_name or "completion"),
        )
    except Exception as exc:
        _raise_runtime_error(exc)

    return _extract_text_content(result)


def stream_text_agent(
    *,
    agent_name: str,
    instructions: str,
    user_input: str,
    model: str | None = None,
) -> Generator[str, None, None]:
    """
    Like run_text_agent but yields text chunks as they arrive (stream=True).
    Each yielded value is a non-empty string delta from the LLM.
    """
    _ = agent_name
    settings = get_settings()
    selected_model = _resolve_model(model)
    _configure_provider_keys()
    completion = _load_litellm_completion()

    try:
        response = completion(
            model=selected_model,
            messages=_build_messages(instructions, user_input),
            temperature=0,
            timeout=float(max(1, settings.llm_timeout_seconds)),
            stream=True,
            metadata=_lf_meta(agent_name or "stream"),
        )
    except Exception as exc:
        _raise_runtime_error(exc)

    for chunk in response:
        try:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
        except (AttributeError, IndexError):
            continue


def run_json_agent(
    *,
    agent_name: str,
    instructions: str,
    user_input: str,
    response_model: type[T],
    model: str | None = None,
) -> T:
    json_instructions = (
        f"{instructions}\n\n"
        "Return only a valid JSON object with no markdown fences or extra text."
    )
    raw_output = run_text_agent(
        agent_name=agent_name,
        instructions=json_instructions,
        user_input=user_input,
        model=model,
    )

    try:
        return response_model.model_validate_json(raw_output)
    except (ValidationError, ValueError):
        parsed = _extract_json_object(raw_output)
        return response_model.model_validate(parsed)


def run_embedding(*, text: str, model: str | None = None) -> list[float]:
    settings = get_settings()
    selected_raw = model or settings.embedding_model
    if selected_raw is None or not selected_raw.strip():
        raise LlmRuntimeError("Missing embedding model. Set EMBEDDING_MODEL in .env.")
    selected_model = selected_raw.strip()
    _configure_provider_keys()
    embedding = _load_litellm_embedding()

    try:
        result = embedding(
            model=selected_model,
            input=[text],
            timeout=float(max(1, settings.embedding_timeout_seconds)),
            metadata=_lf_meta("embedding"),
        )
    except Exception as exc:
        _raise_runtime_error(exc)

    data = getattr(result, "data", None)
    if data is None and isinstance(result, dict):
        data = result.get("data")
    if not data:
        raise LlmRuntimeError("Embedding response did not contain data")

    first = data[0]
    vector = getattr(first, "embedding", None)
    if vector is None and isinstance(first, dict):
        vector = first.get("embedding")
    if not isinstance(vector, list) or not vector:
        raise LlmRuntimeError("Embedding response did not contain a valid vector")

    out: list[float] = []
    for value in vector:
        out.append(float(value))
    return out

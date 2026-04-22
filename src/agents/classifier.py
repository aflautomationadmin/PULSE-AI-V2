from __future__ import annotations

from pydantic import BaseModel

from src.llm import run_json_agent
from src.models import ClassificationResult


class _RawClassification(BaseModel):
    label: str
    confidence: float = 0.0


_CLASSIFIER_INSTRUCTIONS = """
You classify user messages for a retail-sales text-to-SQL assistant.
Return JSON only with fields:
- label: either "business_question" or "normal_chat"
- confidence: float from 0 to 1

Choose business_question only when the user asks about data insights,
metrics, trends, aggregations, filtering, or anything that should query a SQL database.
Choose normal_chat for greetings, chit-chat, opinions, or non-data conversation.
""".strip()


_ALLOWED_LABELS = {"business_question", "normal_chat"}


def _normalize_result(raw: _RawClassification | dict | object) -> ClassificationResult:
    if isinstance(raw, _RawClassification):
        label = raw.label
        confidence = raw.confidence
    elif isinstance(raw, dict):
        label = raw.get("label", "normal_chat")
        confidence = raw.get("confidence", 0.0)
    else:
        label = getattr(raw, "label", "normal_chat")
        confidence = getattr(raw, "confidence", 0.0)

    if label not in _ALLOWED_LABELS:
        label = "normal_chat"

    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.0

    confidence_value = min(1.0, max(0.0, confidence_value))
    return ClassificationResult(label=label, confidence=confidence_value)


def classify_question(question: str) -> ClassificationResult:
    raw_result = run_json_agent(
        agent_name="question-classifier",
        instructions=_CLASSIFIER_INSTRUCTIONS,
        user_input=question,
        response_model=_RawClassification,
    )
    return _normalize_result(raw_result)

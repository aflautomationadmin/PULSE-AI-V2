from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ClassificationResult(BaseModel):
    label: Literal["business_question", "normal_chat"]
    confidence: float = Field(ge=0.0, le=1.0)


class SqlExecutionResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int = Field(ge=0)
    elapsed_ms: int = Field(ge=0)


class Citation(BaseModel):
    """A single factual claim in the answer traced back to a result row."""
    claim: str           # Short description of the claim e.g. "ARROW led sales"
    source_column: str   # Column used to identify the row e.g. "BRAND"
    source_value: str    # Value in that column e.g. "ARROW"
    metric_column: str   # Column containing the cited number e.g. "NETAMT"
    metric_value: str    # Actual value from the result row (as string)
    row_index: int       # 0-based index into the result rows


class VerificationIssue(BaseModel):
    """A number found in the answer that could not be matched to the result data."""
    number_in_answer: str   # The number as it appeared in the answer text
    issue: str              # Human-readable description of the mismatch


class VerificationResult(BaseModel):
    verified: bool                        # True = all numbers match the data
    issues: list[VerificationIssue] = []  # Empty when verified=True


class ChartDataModel(BaseModel):
    """Chart config sent as JSON to the frontend — no file I/O needed."""
    chart_type: str                          # bar | line | pie | table
    title: str
    labels: list[str] = []
    datasets: list[dict[str, Any]] = []
    columns: list[str] = []                  # table only
    rows: list[list[Any]] = []               # table only


class BotReply(BaseModel):
    route: Literal["business_question", "normal_chat"]
    answer_text: str
    sql_used: str | None = None
    row_preview: list[dict[str, Any]] | None = None
    chart_data: ChartDataModel | None = None
    chart_type: str | None = None
    visualization_reason: str | None = None
    citations: list[Citation] = []
    verification: VerificationResult | None = None
    sql_explanation: str | None = None   # plain-English version of the SQL for grounding

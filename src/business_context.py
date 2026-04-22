from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError


class KpiDefinition(BaseModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    formula: str | None = None
    sql_hint: str | None = None


class BrandAliasMapping(BaseModel):
    canonical_brand: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    scope_column: str = "BRAND"
    note: str | None = None


class BusinessContext(BaseModel):
    table_name: str = "prd.FACT_SALES_AI"
    column_descriptions: dict[str, str] = Field(default_factory=dict)
    column_data_types: dict[str, str] = Field(default_factory=dict)
    kpis: list[KpiDefinition] = Field(default_factory=list)
    kpi_procedures: list[dict] = Field(default_factory=list)
    business_terms: dict[str, str] = Field(default_factory=dict)
    brand_alias_mappings: list[BrandAliasMapping] = Field(default_factory=list)


@dataclass
class BusinessContextStore:
    path: Path
    _cached_context: BusinessContext | None = None
    _cached_mtime_ns: int | None = None

    def get_context(self) -> BusinessContext:
        if not self.path.exists():
            if self._cached_context is None:
                self._cached_context = BusinessContext()
            return self._cached_context

        stat = self.path.stat()
        mtime_ns = stat.st_mtime_ns
        if self._cached_context is not None and self._cached_mtime_ns == mtime_ns:
            return self._cached_context

        try:
            raw_payload = json.loads(self.path.read_text(encoding="utf-8"))
            context = BusinessContext.model_validate(raw_payload)
        except (OSError, json.JSONDecodeError, ValidationError):
            context = BusinessContext()

        self._cached_context = context
        self._cached_mtime_ns = mtime_ns
        return context


def format_context_for_prompt(context: BusinessContext) -> str:
    lines: list[str] = [
        f"Target table: {context.table_name}",
        "Column definitions:",
    ]

    all_columns = sorted(
        set(context.column_descriptions.keys()) | set(context.column_data_types.keys())
    )
    if all_columns:
        for column_name in all_columns:
            description = context.column_descriptions.get(column_name, "(no description)").strip()
            data_type = context.column_data_types.get(column_name, "(no data type)").strip()
            lines.append(
                f"- {column_name}: data_type={data_type} | description={description}"
            )
    else:
        lines.append("- (no column metadata provided)")

    lines.append("KPI definitions:")
    if context.kpis:
        for kpi in context.kpis:
            detail = f"- {kpi.name}: {kpi.description}"
            if kpi.formula:
                detail += f" | formula: {kpi.formula}"
            if kpi.sql_hint:
                detail += f" | sql_hint: {kpi.sql_hint}"
            lines.append(detail)
    else:
        lines.append("- (no KPI definitions provided)")

    lines.append("Business term mappings:")
    if context.business_terms:
        for term, meaning in sorted(context.business_terms.items()):
            lines.append(f"- {term}: {meaning}")
    else:
        lines.append("- (no business term mappings provided)")

    lines.append("Brand alias mappings:")
    if context.brand_alias_mappings:
        for mapping in context.brand_alias_mappings:
            alias_text = ", ".join(mapping.aliases) if mapping.aliases else "(no aliases)"
            detail = (
                f"- {mapping.canonical_brand} | scope={mapping.scope_column} | aliases=[{alias_text}]"
            )
            if mapping.note:
                detail += f" | note: {mapping.note}"
            lines.append(detail)
    else:
        lines.append("- (no brand alias mappings provided)")

    return "\n".join(lines)

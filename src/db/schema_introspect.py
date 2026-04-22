from __future__ import annotations

from collections import defaultdict


def introspect_schema_context(connection) -> str:
    query = """
    SELECT
        TABLE_SCHEMA,
        TABLE_NAME,
        COLUMN_NAME,
        DATA_TYPE,
        ORDINAL_POSITION
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'prd'
      AND TABLE_NAME = 'FACT_SALES_AI'
    ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
    """

    cursor = connection.cursor()
    cursor.execute(query)
    rows = cursor.fetchall()

    grouped: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        table_schema, table_name, column_name, data_type, _ordinal = row
        table_key = f"{table_schema}.{table_name}"
        grouped[table_key].append(f"{column_name} {data_type}")

    lines = [
        "Retail sales schema discovered from INFORMATION_SCHEMA:",
        "Allowed table: prd.FACT_SALES_AI only.",
    ]

    for table_name in sorted(grouped.keys()):
        columns = ", ".join(grouped[table_name])
        lines.append(f"- {table_name}")
        lines.append(f"  columns: {columns}")

    if len(lines) == 2:
        lines.append("- prd.FACT_SALES_AI (not found or no columns visible)")

    return "\n".join(lines)

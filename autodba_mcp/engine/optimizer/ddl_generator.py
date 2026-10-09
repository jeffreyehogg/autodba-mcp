"""
AutoDBA zero-downtime index synthesis engine.
Transforms column usage telemetry into optimized DDL tailored for T-SQL, Postgres, and MySQL.
"""

from __future__ import annotations

import re
from collections import defaultdict

from autodba_mcp.engine.schemas import (
    AnalysisReport,
    Dialect,
    ImpactLevel,
    IndexRecommendation,
)


def _clean_name(name: str) -> str:
    """Sanitize identifier for DDL naming."""
    return re.sub(r"[^A-Za-z0-9_]", "", name)


def build_index_ddl(
    table: str,
    key_columns: list[str],
    include_columns: list[str],
    dialect: Dialect,
    where_clause: str | None = None,
) -> str:
    """
    Generate production-ready, zero-downtime CREATE INDEX / ALTER TABLE DDL.
    """
    clean_tbl = _clean_name(table)
    clean_keys = [_clean_name(c) for c in key_columns if c and c != "*"]
    clean_incs = [_clean_name(c) for c in include_columns if c and c != "*" and c not in clean_keys]

    if not clean_keys:
        return ""

    # Generate index name
    short_cols = "_".join(clean_keys[:2])
    idx_name_raw = f"IX_{clean_tbl}_{short_cols}"

    if dialect == "tsql":
        idx_name = idx_name_raw[:128]
        key_sql = ", ".join(f"[{c}]" for c in clean_keys)
        inc_sql = f" INCLUDE ({', '.join(f'[{c}]' for c in clean_incs)})" if clean_incs else ""
        where_sql = f" WHERE {where_clause}" if where_clause else ""
        return (
            f"CREATE NONCLUSTERED INDEX [{idx_name}] ON [dbo].[{clean_tbl}] ({key_sql}){inc_sql}{where_sql} "
            f"WITH (ONLINE = ON, SORT_IN_TEMPDB = ON, DATA_COMPRESSION = PAGE);"
        )

    elif dialect == "postgres":
        idx_name = idx_name_raw.lower()[:63]
        key_sql = ", ".join(f"{c.lower()}" for c in clean_keys)
        inc_sql = f" INCLUDE ({', '.join(f'{c.lower()}' for c in clean_incs)})" if clean_incs else ""
        where_sql = f" WHERE {where_clause}" if where_clause else ""
        return (
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {idx_name} ON {clean_tbl.lower()} ({key_sql}){inc_sql}{where_sql};"
        )

    elif dialect == "mysql":
        idx_name = idx_name_raw.lower()[:64]
        # MySQL InnoDB does not support INCLUDE; merge covering columns into composite key
        combined_cols = clean_keys + clean_incs[:4]
        cols_sql = ", ".join(f"`{c}`" for c in combined_cols)
        return (
            f"ALTER TABLE `{clean_tbl}` ADD INDEX `{idx_name}` ({cols_sql}), "
            f"ALGORITHM=INPLACE, LOCK=NONE;"
        )

    return ""


def synthesize_indexes(report: AnalysisReport) -> list[IndexRecommendation]:
    """
    Synthesize covering indexes based on column usage extracted during static AST analysis.
    Groups column usages by table: equality -> join -> range -> include.
    """
    # Check if query had a wildcard SELECT *
    has_select_star = any(d.rule_id == "SELECT_STAR" for d in report.diagnostics)

    # Group usages by base table name
    table_usages: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

    for usage in report.column_usage:
        if not usage.table or not usage.column or usage.column == "*":
            continue
        tbl = usage.table
        col = usage.column
        if col not in table_usages[tbl][usage.usage]:
            table_usages[tbl][usage.usage].append(col)

    recommendations: list[IndexRecommendation] = []

    for tbl_name, usages in table_usages.items():
        eq_cols = usages.get("equality", [])
        join_cols = [c for c in usages.get("join", []) if c not in eq_cols]
        range_cols = [c for c in usages.get("range", []) if c not in eq_cols and c not in join_cols]

        # Key columns order: equality -> join -> range
        key_cols = (eq_cols + join_cols + range_cols)[:4]
        if not key_cols:
            continue

        # Covering columns: select / order / group not already in key
        covering_candidates = [
            c
            for kind in ("select", "order", "group")
            for c in usages.get(kind, [])
            if c not in key_cols
        ]
        # Deduplicate preserving order
        seen_inc = set()
        include_cols = []
        if not has_select_star:
            for c in covering_candidates:
                if c not in seen_inc and len(include_cols) < 8:
                    seen_inc.add(c)
                    include_cols.append(c)

        ddl = build_index_ddl(tbl_name, key_cols, include_cols, report.dialect)
        if not ddl:
            continue

        impact: ImpactLevel = "high" if len(eq_cols) > 0 or len(join_cols) > 0 else "medium"
        rationale_parts = []
        if eq_cols:
            rationale_parts.append(f"equality seeks on ({', '.join(eq_cols)})")
        if join_cols:
            rationale_parts.append(f"hash/loop join seeks on ({', '.join(join_cols)})")
        if range_cols:
            rationale_parts.append(f"range bounds on ({', '.join(range_cols)})")
        if include_cols:
            rationale_parts.append(f"covering projection on ({', '.join(include_cols)})")
        elif has_select_star:
            rationale_parts.append("covering INCLUDE omitted due to wildcard SELECT * projection")

        rationale = f"Composite index optimized for {'; '.join(rationale_parts)}."

        recommendations.append(
            IndexRecommendation(
                table=tbl_name,
                key_columns=key_cols,
                include_columns=include_cols,
                ddl=ddl,
                rationale=rationale,
                estimated_impact=impact,
            )
        )

    return recommendations

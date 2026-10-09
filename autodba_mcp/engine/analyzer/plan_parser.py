"""
AutoDBA execution plan parser.
Supports SQL Server ShowPlanXML, PostgreSQL EXPLAIN JSON, and MySQL EXPLAIN JSON.
"""

from __future__ import annotations

import json
from typing import Any

try:
    import defusedxml.ElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET

from autodba_mcp.engine.analyzer.rules import make_diagnostic
from autodba_mcp.engine.schemas import (
    Diagnostic,
    Dialect,
    IndexRecommendation,
    PlanOperator,
    PlanSummary,
)

_SQLSERVER_NS = {"sp": "http://schemas.microsoft.com/sqlserver/2004/07/showplan"}


def _clean_bracket(name: str | None) -> str:
    """Strip T-SQL bracket or quotes: [dbo].[Leads] -> Leads."""
    if not name:
        return ""
    clean = name.strip()
    if clean.startswith("[") and clean.endswith("]"):
        clean = clean[1:-1]
    return clean


def _parse_sqlserver_xml(plan_text: str) -> PlanSummary:
    """Parse SQL Server ShowPlan XML."""
    try:
        root = ET.fromstring(plan_text)
    except Exception as e:
        return PlanSummary(
            format="sqlserver_xml",
            parse_error=f"Malformed ShowPlanXML: {e}",
        )

    # Total query cost
    total_cost: float | None = None
    stmt = root.find(".//sp:StmtSimple", _SQLSERVER_NS)
    if stmt is not None and "StatementSubTreeCost" in stmt.attrib:
        try:
            total_cost = float(stmt.attrib["StatementSubTreeCost"])
        except ValueError:
            pass

    operators: list[PlanOperator] = []
    bottlenecks: list[Diagnostic] = []
    missing_indexes: list[IndexRecommendation] = []

    # 1. Parse MissingIndexGroups
    for mig in root.findall(".//sp:MissingIndexGroup", _SQLSERVER_NS):
        impact_str = mig.attrib.get("Impact", "80")
        impact_flt = float(impact_str) if impact_str.replace(".", "", 1).isdigit() else 80.0
        impact_level = "high" if impact_flt >= 75 else "medium"

        for mi in mig.findall("./sp:MissingIndex", _SQLSERVER_NS):
            tbl = _clean_bracket(mi.attrib.get("Table", "TargetTable"))
            schema = _clean_bracket(mi.attrib.get("Schema", "dbo"))

            eq_cols: list[str] = []
            ineq_cols: list[str] = []
            inc_cols: list[str] = []

            for cg in mi.findall("./sp:ColumnGroup", _SQLSERVER_NS):
                usage = cg.attrib.get("Usage", "").upper()
                for c in cg.findall("./sp:Column", _SQLSERVER_NS):
                    cname = _clean_bracket(c.attrib.get("Name", ""))
                    if not cname:
                        continue
                    if usage == "EQUALITY":
                        eq_cols.append(cname)
                    elif usage == "INEQUALITY":
                        ineq_cols.append(cname)
                    elif usage == "INCLUDE":
                        inc_cols.append(cname)

            key_cols = eq_cols + ineq_cols
            if not key_cols:
                continue

            idx_name = f"IX_{tbl}_{'_'.join(key_cols[:2])}"
            key_sql = ", ".join(f"[{c}]" for c in key_cols)
            inc_sql = f" INCLUDE ({', '.join(f'[{c}]' for c in inc_cols)})" if inc_cols else ""
            ddl = f"CREATE NONCLUSTERED INDEX [{idx_name}] ON [{schema}].[{tbl}] ({key_sql}){inc_sql} WITH (ONLINE = ON, SORT_IN_TEMPDB = ON, DATA_COMPRESSION = PAGE);"

            missing_indexes.append(
                IndexRecommendation(
                    table=tbl,
                    key_columns=key_cols,
                    include_columns=inc_cols,
                    ddl=ddl,
                    rationale=f"SQL Server query engine reported missing index with {impact_str}% estimated query cost improvement.",
                    estimated_impact=impact_level,
                )
            )

    # 2. Parse RelOps
    for relop in root.findall(".//sp:RelOp", _SQLSERVER_NS):
        phys_op = relop.attrib.get("PhysicalOp", "Operator")
        _log_op = relop.attrib.get("LogicalOp", "")
        est_rows = float(relop.attrib.get("EstimateRows", "0"))
        sub_cost = float(relop.attrib.get("EstimatedTotalSubtreeCost", "0"))

        cost_pct = (sub_cost / total_cost * 100.0) if (total_cost and total_cost > 0) else 0.0
        cost_pct = min(100.0, max(0.0, cost_pct))

        # Target object
        target_obj = None
        obj_node = relop.find(".//sp:Object", _SQLSERVER_NS)
        if obj_node is not None:
            t_name = _clean_bracket(obj_node.attrib.get("Table"))
            idx_name = _clean_bracket(obj_node.attrib.get("Index"))
            target_obj = f"{t_name} ({idx_name})" if idx_name else t_name

        warnings: list[str] = []
        is_bottleneck = False

        # Warnings inspection
        warn_node = relop.find("./sp:Warnings", _SQLSERVER_NS)
        if warn_node is not None:
            if warn_node.find("./sp:SpillToTempDb", _SQLSERVER_NS) is not None:
                warnings.append("SpillToTempDb: operator spilled sort/hash to tempdb")
                bottlenecks.append(
                    make_diagnostic(
                        "PLAN_SORT_SPILL",
                        f"Operator {phys_op} on {target_obj or 'intermediate'} spilled rows to TempDB.",
                        suggestion="Increase memory grant or add index covering ORDER BY / GROUP BY to avoid physical disk spill.",
                    )
                )
                is_bottleneck = True

            if warn_node.find("./sp:PlanAffectingConvert", _SQLSERVER_NS) is not None:
                warnings.append("PlanAffectingConvert: type conversion prevents index seek")
                bottlenecks.append(
                    make_diagnostic(
                        "PLAN_IMPLICIT_CONVERSION",
                        f"Type conversion in {phys_op} prevents direct index seek.",
                        suggestion="Align predicate parameter and literal types with column definition.",
                    )
                )
                is_bottleneck = True

            if warn_node.find("./sp:NoJoinPredicate", _SQLSERVER_NS) is not None:
                warnings.append("NoJoinPredicate: nested loops join has no join predicate")
                bottlenecks.append(
                    make_diagnostic(
                        "CARTESIAN_JOIN",
                        f"Execution plan detected join operator {phys_op} without join predicate.",
                        suggestion="Add explicit join condition to avoid Cartesian product.",
                    )
                )
                is_bottleneck = True

        # Heavy scans
        if phys_op in ("Table Scan", "Clustered Index Scan") and est_rows >= 100_000:
            is_bottleneck = True
            warnings.append(f"High-volume scan ({est_rows:,.0f} rows)")
            bottlenecks.append(
                make_diagnostic(
                    "PLAN_TABLE_SCAN",
                    f"{phys_op} on {target_obj} scanned estimated {est_rows:,.0f} rows.",
                    suggestion="Add selective index matching query filter to replace table scan with index seek.",
                )
            )

        if "Lookup" in phys_op:
            is_bottleneck = True
            warnings.append("Bookmark / Key Lookup against base clustered table")
            bottlenecks.append(
                make_diagnostic(
                    "PLAN_KEY_LOOKUP",
                    f"Key Lookup on {target_obj} incurs bookmark pointer dereferences for non-covered columns.",
                    suggestion="Add projected columns to index INCLUDE clause to create covering index.",
                )
            )

        if cost_pct >= 30.0 and len(operators) > 1:
            is_bottleneck = True

        operators.append(
            PlanOperator(
                op=phys_op,
                object=target_obj,
                estimated_rows=est_rows,
                estimated_cost=sub_cost,
                cost_pct=round(cost_pct, 1),
                is_bottleneck=is_bottleneck,
                warnings=warnings,
            )
        )

    # Sort operators by subtree cost descending
    operators.sort(key=lambda o: o.estimated_cost or 0.0, reverse=True)

    return PlanSummary(
        format="sqlserver_xml",
        total_cost=total_cost,
        operators=operators[:12],  # Cap at top 12 most costly
        bottlenecks=bottlenecks,
        missing_indexes=missing_indexes,
    )


def _parse_postgres_json(data: Any) -> PlanSummary:
    """Parse PostgreSQL EXPLAIN (FORMAT JSON)."""
    try:
        plan_root = data[0]["Plan"] if isinstance(data, list) else data.get("Plan", {})
    except Exception as e:
        return PlanSummary(format="postgres_json", parse_error=f"Unexpected PG plan structure: {e}")

    total_cost: float | None = plan_root.get("Total Cost")
    operators: list[PlanOperator] = []
    bottlenecks: list[Diagnostic] = []

    def walk_pg_node(node: dict[str, Any]) -> None:
        op_name = node.get("Node Type", "Operator")
        rel_name = node.get("Relation Name") or node.get("Alias")
        node_cost = float(node.get("Total Cost", 0.0))
        node_rows = float(node.get("Plan Rows", 0.0))

        cost_pct = (node_cost / total_cost * 100.0) if (total_cost and total_cost > 0) else 0.0
        cost_pct = min(100.0, max(0.0, cost_pct))

        warnings: list[str] = []
        is_bottleneck = False

        if op_name == "Seq Scan" and (node_rows >= 50_000 or cost_pct >= 25.0):
            is_bottleneck = True
            warnings.append(f"Seq Scan on {rel_name} ({node_rows:,.0f} rows)")
            bottlenecks.append(
                make_diagnostic(
                    "PLAN_TABLE_SCAN",
                    f"Postgres Seq Scan on relation '{rel_name}' evaluated {node_rows:,.0f} rows with cost {node_cost:,.1f}.",
                    suggestion=f"Create a b-tree index on '{rel_name}' covering query filter predicates.",
                )
            )

        sort_method = str(node.get("Sort Method", "")).lower()
        if "external" in sort_method or "disk" in sort_method:
            is_bottleneck = True
            warnings.append(f"Sort spilled to disk: {sort_method}")
            bottlenecks.append(
                make_diagnostic(
                    "PLAN_SORT_SPILL",
                    f"Sort operator spilled to temporary disk files ({sort_method}).",
                    suggestion="Increase work_mem or index sort columns to enable index-based ordering.",
                )
            )

        if cost_pct >= 35.0:
            is_bottleneck = True

        operators.append(
            PlanOperator(
                op=op_name,
                object=rel_name,
                estimated_rows=node_rows,
                estimated_cost=node_cost,
                cost_pct=round(cost_pct, 1),
                is_bottleneck=is_bottleneck,
                warnings=warnings,
            )
        )

        for child in node.get("Plans", []):
            walk_pg_node(child)

    walk_pg_node(plan_root)
    operators.sort(key=lambda o: o.estimated_cost or 0.0, reverse=True)

    return PlanSummary(
        format="postgres_json",
        total_cost=total_cost,
        operators=operators[:12],
        bottlenecks=bottlenecks,
        missing_indexes=[],
    )


def _parse_mysql_json(data: dict[str, Any]) -> PlanSummary:
    """Parse MySQL EXPLAIN FORMAT=JSON."""
    q_block = data.get("query_block", {})
    cost_info = q_block.get("cost_info", {})
    total_cost_str = cost_info.get("query_cost", "0")
    total_cost = float(total_cost_str) if str(total_cost_str).replace(".", "", 1).isdigit() else 100.0

    operators: list[PlanOperator] = []
    bottlenecks: list[Diagnostic] = []

    def inspect_obj(obj: Any) -> None:
        if not isinstance(obj, dict):
            return

        if obj.get("using_temporary_table"):
            bottlenecks.append(
                make_diagnostic(
                    "PLAN_TEMP_TABLE",
                    "MySQL generated internal temporary table for grouping/aggregation.",
                    suggestion="Add composite index matching GROUP BY columns to avoid memory-to-disk temporary table spill.",
                )
            )

        if obj.get("using_filesort"):
            bottlenecks.append(
                make_diagnostic(
                    "PLAN_FILESORT",
                    "MySQL required extra filesort pass to resolve ORDER BY.",
                    suggestion="Add an index with columns matching ORDER BY sequence to allow index scan ordering.",
                )
            )

        tbl = obj.get("table")
        if isinstance(tbl, dict):
            t_name = tbl.get("table_name", "table")
            access = tbl.get("access_type", "")
            rows = float(tbl.get("rows_examined_per_scan", 0))
            is_bottleneck = False
            warnings: list[str] = []

            if access == "ALL":
                is_bottleneck = True
                warnings.append(f"Full table scan (access_type ALL, {rows:,.0f} rows examined)")
                bottlenecks.append(
                    make_diagnostic(
                        "PLAN_TABLE_SCAN",
                        f"MySQL table scan (ALL) on '{t_name}' examines {rows:,.0f} rows per scan.",
                        suggestion=f"Add index on '{t_name}' to enable 'ref' or 'range' access types.",
                    )
                )

            operators.append(
                PlanOperator(
                    op=f"Table Access ({access})",
                    object=t_name,
                    estimated_rows=rows,
                    estimated_cost=total_cost,
                    cost_pct=50.0 if access == "ALL" else 20.0,
                    is_bottleneck=is_bottleneck,
                    warnings=warnings,
                )
            )

        for v in obj.values():
            if isinstance(v, dict):
                inspect_obj(v)
            elif isinstance(v, list):
                for item in v:
                    inspect_obj(item)

    inspect_obj(q_block)

    return PlanSummary(
        format="mysql_json",
        total_cost=total_cost,
        operators=operators[:10],
        bottlenecks=bottlenecks,
        missing_indexes=[],
    )


def parse_plan(plan_text: str | None, dialect: Dialect) -> PlanSummary:
    """
    Parse an execution plan string (XML or JSON).
    Guaranteed never to raise exceptions.
    """
    if not plan_text or not plan_text.strip():
        return PlanSummary(
            format="unknown",
            operators=[],
            bottlenecks=[],
            missing_indexes=[],
            parse_error=None,
        )

    clean = plan_text.strip()

    # SQL Server ShowPlanXML
    if "<ShowPlanXML" in clean or ("<BatchSequence" in clean and "<QueryPlan" in clean):
        return _parse_sqlserver_xml(clean)

    # JSON formats
    if clean.startswith("{") or clean.startswith("["):
        try:
            parsed_json = json.loads(clean)
        except Exception as e:
            return PlanSummary(format="unknown", parse_error=f"Invalid JSON plan: {e}")

        # Check Postgres
        if (isinstance(parsed_json, list) and len(parsed_json) > 0 and "Plan" in parsed_json[0]) or (
            isinstance(parsed_json, dict) and "Plan" in parsed_json
        ):
            return _parse_postgres_json(parsed_json)

        # Check MySQL
        if isinstance(parsed_json, dict) and "query_block" in parsed_json:
            return _parse_mysql_json(parsed_json)

    return PlanSummary(
        format="unknown",
        parse_error="Plan format unrecognized; provide SQL Server ShowPlanXML or Postgres/MySQL EXPLAIN JSON.",
    )

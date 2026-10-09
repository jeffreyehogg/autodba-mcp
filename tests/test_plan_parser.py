"""
Tests for execution plan parser (SQL Server XML, Postgres JSON, MySQL JSON).
"""

from autodba_mcp.engine.analyzer.plan_parser import parse_plan
from autodba_mcp.engine.data.presets import PRESETS


def test_parse_sqlserver_xml_plan():
    p = next(x for x in PRESETS if x.id == "lgi-lead-attribution")
    plan = parse_plan(p.plan, "tsql")
    assert plan.format == "sqlserver_xml"
    assert plan.total_cost is not None
    assert len(plan.operators) > 0
    assert len(plan.missing_indexes) > 0
    assert any(b.rule_id == "PLAN_TABLE_SCAN" for b in plan.bottlenecks)


def test_parse_postgres_json_plan():
    p = next(x for x in PRESETS if x.id == "pacs-badge-telemetry")
    plan = parse_plan(p.plan, "postgres")
    assert plan.format == "postgres_json"
    assert plan.total_cost is not None
    assert len(plan.operators) > 0
    assert any(b.rule_id == "PLAN_TABLE_SCAN" for b in plan.bottlenecks)


def test_parse_mysql_json_plan():
    p = next(x for x in PRESETS if x.id == "ecom-order-history")
    plan = parse_plan(p.plan, "mysql")
    assert plan.format == "mysql_json"
    assert len(plan.operators) > 0
    rule_ids = [b.rule_id for b in plan.bottlenecks]
    assert "PLAN_TEMP_TABLE" in rule_ids or "PLAN_FILESORT" in rule_ids


def test_parse_empty_or_invalid_plan():
    assert parse_plan(None, "tsql").format == "unknown"
    assert parse_plan("", "tsql").format == "unknown"
    res = parse_plan("INVALID PLAN NOT XML OR JSON", "tsql")
    assert res.format == "unknown"
    assert res.parse_error is not None

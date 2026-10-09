"""
Tests for AutoDBA FastMCP Server tools and stdio integration.
"""

import pytest

from autodba_mcp.engine.data.presets import PRESETS
from autodba_mcp.server import (
    analyze_query,
    explain_execution_plan,
    get_rules_catalog,
    list_supported_rules,
    main,
    mcp,
    optimize_query,
    optimize_query_prompt,
)


def test_mcp_server_initialization_and_tool_registration():
    """Verify FastMCP server instance has registered all required tools."""
    assert mcp.name == "autodba-mcp"
    # FastMCP holds registered tools in internal registry
    # Let's inspect registered tool names
    tool_names = [tool.name for tool in mcp._tool_manager.list_tools()]
    assert "analyze_query" in tool_names
    assert "optimize_query" in tool_names
    assert "explain_execution_plan" in tool_names
    assert "list_supported_rules" in tool_names


def test_tool_analyze_query():
    """Verify analyze_query tool detects anti-patterns and returns structured Markdown."""
    sql = "SELECT * FROM dbo.Leads l WHERE YEAR(l.CreatedDate) = 2026"
    report_md = analyze_query(sql, dialect="tsql")

    assert isinstance(report_md, str)
    assert "# AutoDBA Query Analysis Report (TSQL)" in report_md
    assert "NON_SARGABLE_FUNCTION" in report_md
    assert "SELECT_STAR" in report_md
    assert "Complexity Score" in report_md


@pytest.mark.asyncio
async def test_tool_optimize_query():
    """Verify optimize_query rewrites anti-patterns and synthesizes index DDL."""
    sql = "SELECT LeadId FROM dbo.Leads l WHERE YEAR(l.CreatedDate) = 2026"
    result_md = await optimize_query(sql, dialect="tsql")

    assert isinstance(result_md, str)
    assert "# AutoDBA Query Optimization Report (TSQL)" in result_md
    assert "Optimized SQL Query" in result_md
    assert "2026-01-01" in result_md
    assert "Recommended Zero-Downtime Index Migration" in result_md
    assert "ONLINE = ON" in result_md


def test_tool_explain_execution_plan():
    """Verify explain_execution_plan parses ShowPlan XML and reports bottlenecks."""
    # Use real enterprise preset plan
    preset = next(p for p in PRESETS if p.id == "lgi-lead-attribution")
    assert preset.plan is not None

    plan_md = explain_execution_plan(preset.plan, dialect="tsql")
    assert isinstance(plan_md, str)
    assert "# AutoDBA Execution Plan Diagnostics (TSQL)" in plan_md
    assert "Microsoft SQL Server ShowPlan XML" in plan_md
    assert "Detected Bottlenecks" in plan_md
    assert "PLAN_TABLE_SCAN" in plan_md
    assert "Operator Breakdown" in plan_md


def test_tool_list_supported_rules():
    """Verify list_supported_rules outputs complete rules catalog."""
    rules_md = list_supported_rules()
    assert isinstance(rules_md, str)
    assert "# AutoDBA Diagnostic Rules Catalog" in rules_md
    assert "NON_SARGABLE_FUNCTION" in rules_md
    assert "CARTESIAN_JOIN" in rules_md
    assert "CURSOR_LOOP" in rules_md
    assert "PLAN_TABLE_SCAN" in rules_md
    assert "PLAN_SORT_SPILL" in rules_md


def test_resource_rules_catalog():
    """Verify rules://catalog resource returns complete catalog Markdown."""
    res_md = get_rules_catalog()
    assert isinstance(res_md, str)
    assert "# AutoDBA Diagnostic Rules Catalog" in res_md


def test_prompt_optimize_query():
    """Verify optimize_query_prompt returns structured guidance prompt."""
    prompt = optimize_query_prompt("SELECT * FROM dbo.Leads", dialect="tsql")
    assert isinstance(prompt, str)
    assert "analyze_query" in prompt
    assert "optimize_query" in prompt
    assert "TSQL" in prompt


def test_cli_version_and_help(monkeypatch, capsys):
    """Verify CLI prints version and help info without hanging."""
    monkeypatch.setattr("sys.argv", ["autodba-mcp", "--version"])
    main()
    captured = capsys.readouterr()
    assert "autodba-mcp 0.1.0" in captured.out

    monkeypatch.setattr("sys.argv", ["autodba-mcp", "--help"])
    main()
    captured = capsys.readouterr()
    assert "AutoDBA MCP Server" in captured.out
    assert "Usage:" in captured.out

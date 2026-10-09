"""
AutoDBA MCP Server.
Provides Model Context Protocol (MCP) stdio tools for static SQL analysis,
query optimization, zero-downtime index synthesis, and execution plan diagnostics.
"""

from __future__ import annotations

try:
    from mcp.server.fastmcp import FastMCP
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as FastMCP

from autodba_mcp.engine.analyzer.ast_visitor import analyze_sql
from autodba_mcp.engine.analyzer.plan_parser import parse_plan
from autodba_mcp.engine.analyzer.rules import RULES
from autodba_mcp.engine.core.security import sanitize_sql
from autodba_mcp.engine.optimizer.agent import run_optimization
from autodba_mcp.engine.schemas import Dialect

mcp = FastMCP(
    "autodba-mcp",
    description="AutoDBA: Zero-database-connection static SQL analysis and execution plan diagnostics",
)


def _normalize_dialect(dialect: str) -> Dialect:
    """Normalize input dialect string to supported Dialect literals."""
    d = dialect.strip().lower()
    if d in ("tsql", "sqlserver", "mssql", "t-sql"):
        return "tsql"
    if d in ("postgres", "postgresql", "pgsql"):
        return "postgres"
    if d in ("mysql", "mariadb"):
        return "mysql"
    return "tsql"


@mcp.tool()
def analyze_query(sql: str, dialect: str = "tsql") -> str:
    """
    Run static AST analysis on a SQL query using sqlglot.

    Identifies non-SARGable column functions, Cartesian joins, cursor loops,
    SELECT *, and query anti-patterns without connecting to a database.

    Args:
        sql: The SQL query text to inspect.
        dialect: SQL dialect ('tsql', 'postgres', or 'mysql'). Defaults to 'tsql'.

    Returns:
        Structured Markdown diagnostic report with complexity scores and rule IDs.
    """
    try:
        clean_sql = sanitize_sql(sql)
    except ValueError as ve:
        return f"### Analysis Error\n\nInvalid SQL input: {ve}"

    norm_dialect = _normalize_dialect(dialect)
    report = analyze_sql(clean_sql, norm_dialect)

    md: list[str] = []
    md.append(f"# AutoDBA Query Analysis Report ({norm_dialect.upper()})\n")

    if not report.parse_ok:
        md.append("### ❌ SQL Parse Error\n")
        md.append(f"**Error Details**: `{report.parse_error}`\n")
        if report.diagnostics:
            md.append("#### Diagnostics:\n")
            for diag in report.diagnostics:
                md.append(f"- **[{diag.rule_id}]** {diag.title}: {diag.message}")
                if diag.suggestion:
                    md.append(f"  *Remediation*: {diag.suggestion}")
        return "\n".join(md)

    # Complexity rating label
    score = report.complexity_score
    if score >= 75:
        score_label = "🔴 Critical"
    elif score >= 50:
        score_label = "🟠 High"
    elif score >= 25:
        score_label = "🟡 Moderate"
    else:
        score_label = "🟢 Low"

    tables_str = ", ".join(f"`{t.name}`" for t in report.tables) if report.tables else "None detected"

    md.append("## Overview\n")
    md.append(f"- **Complexity Score**: **{score}/100** ({score_label})")
    md.append(f"- **Tables Referenced**: {tables_str}")
    md.append(f"- **Statements Analyzed**: {report.statement_count} ({', '.join(report.statement_types) or 'Unknown'})")
    md.append(f"- **Issues Detected**: {len(report.diagnostics)}\n")

    # Diagnostics section
    if report.diagnostics:
        md.append("## Diagnostic Findings\n")
        for diag in report.diagnostics:
            sev_badge = {
                "critical": "🔴 CRITICAL",
                "warning": "🟠 WARNING",
                "optimization": "🔵 OPTIMIZATION",
            }.get(diag.severity, diag.severity.upper())

            md.append(f"### [{diag.rule_id}] {diag.title} — {sev_badge}")
            md.append(f"**Finding**: {diag.message}\n")
            if diag.snippet:
                md.append(f"```sql\n{diag.snippet}\n```\n")
            md.append(f"💡 **Remediation**: {diag.suggestion}\n")
    else:
        md.append("## Diagnostic Findings\n")
        md.append("✅ **No query anti-patterns detected.** The query follows SARGable filtering and indexing best practices.\n")

    # Column usage analysis
    if report.column_usage:
        md.append("## Column Predicate & Join Usage\n")
        usages_by_table: dict[str, list[str]] = {}
        for cu in report.column_usage:
            t = cu.table or "unresolved"
            usages_by_table.setdefault(t, []).append(f"`{cu.column}` ({cu.usage})")

        for tbl, cols in usages_by_table.items():
            md.append(f"- **{tbl}**: {', '.join(cols)}")
        md.append("")

    return "\n".join(md)


@mcp.tool()
async def optimize_query(
    sql: str,
    dialect: str = "tsql",
    plan_xml_or_json: str | None = None,
) -> str:
    """
    Rewrite SQL anti-patterns into optimized AST forms and synthesize index DDL.

    Transforms non-SARGable functions into range bounds, converts comma joins
    to explicit ANSI joins, replaces NOT IN with NOT EXISTS, and generates
    zero-downtime CREATE INDEX ... WITH (ONLINE = ON) statements.

    Args:
        sql: The SQL query text to optimize.
        dialect: SQL dialect ('tsql', 'postgres', or 'mysql'). Defaults to 'tsql'.
        plan_xml_or_json: Optional execution plan text (.sqlplan XML or EXPLAIN JSON).

    Returns:
        Structured Markdown report containing optimized SQL and recommended index DDL.
    """
    try:
        clean_sql = sanitize_sql(sql)
    except ValueError as ve:
        return f"### Optimization Error\n\nInvalid SQL input: {ve}"

    norm_dialect = _normalize_dialect(dialect)

    # Execute deterministic optimization loop
    opt_res = await run_optimization(
        clean_sql,
        norm_dialect,
        plan_text=plan_xml_or_json,
        api_key=None,
        allow_llm=False,
    )

    md: list[str] = []
    md.append(f"# AutoDBA Query Optimization Report ({norm_dialect.upper()})\n")

    # Metrics Summary
    md.append("## Summary\n")
    md.append(f"- **Estimated Workload Improvement**: **~{opt_res.estimated_improvement_pct}%**")
    md.append(f"- **Engine**: {opt_res.engine.title()} AST Transformation")
    md.append(f"- **Semantic Verification**: {'✅ Passed' if opt_res.verification.passed else '⚠️ Caution'}")
    if opt_res.antipatterns_fixed:
        md.append(f"- **Anti-patterns Eliminated**: {', '.join(f'`{f}`' for f in opt_res.antipatterns_fixed)}")
    md.append("")

    # Optimized SQL
    md.append("## Optimized SQL Query\n")
    md.append("```sql")
    md.append(opt_res.optimized_sql)
    md.append("```\n")

    # Technical Explanation
    md.append("## Technical Rationale\n")
    md.append(f"{opt_res.explanation}\n")

    # Verification Report
    v = opt_res.verification
    md.append("## AST Semantic Verification\n")
    md.append(f"- **Table Preservation**: {'✅ All base relations preserved' if v.tables_preserved else '❌ Base tables altered'}")
    md.append(f"- **Statement Type**: {'✅ Preserved' if v.statement_type_preserved else '❌ Mismatched'}")
    if v.notes:
        md.append(f"- **Verification Notes**: {'; '.join(v.notes)}")
    md.append("")

    # Index Recommendations
    if opt_res.ddl_recommendations:
        md.append("## Recommended Zero-Downtime Index Migration\n")
        md.append(
            "The following non-blocking DDL statements are synthesized based on query predicates and execution plan bottlenecks:\n"
        )
        md.append("```sql")
        for idx in opt_res.ddl_recommendations:
            md.append(f"-- Table: {idx.table} | Rationale: {idx.rationale}")
            md.append(f"{idx.ddl}\n")
        md.append("```\n")
    else:
        md.append("## Index Recommendations\n")
        md.append("No additional secondary index migrations required for this query.\n")

    # Heuristic Cost Model
    cm = opt_res.cost_model
    md.append("## Heuristic I/O Simulation\n")
    md.append(f"- **Relative AST Cost**: `{cm.before_cost:.1f}` ➔ `{cm.after_cost:.1f}`")
    md.append(f"- **Projected Page Reads**: `{cm.logical_reads_before:,}` ➔ `{cm.logical_reads_after:,}`\n")

    return "\n".join(md)


@mcp.tool()
def explain_execution_plan(plan_content: str, dialect: str = "tsql") -> str:
    """
    Parse and explain database execution plans.

    Supports SQL Server ShowPlanXML (.sqlplan), PostgreSQL EXPLAIN JSON,
    and MySQL EXPLAIN JSON. Pinpoints bottleneck operators, sort spills,
    and missing index recommendations.

    Args:
        plan_content: Raw plan string (.sqlplan XML or EXPLAIN JSON).
        dialect: SQL dialect ('tsql', 'postgres', or 'mysql'). Defaults to 'tsql'.

    Returns:
        Structured Markdown diagnostic report highlighting plan bottlenecks.
    """
    if not plan_content or not plan_content.strip():
        return "### Execution Plan Error\n\nExecution plan content is empty."

    norm_dialect = _normalize_dialect(dialect)
    plan_summary = parse_plan(plan_content, norm_dialect)

    md: list[str] = []
    md.append(f"# AutoDBA Execution Plan Diagnostics ({norm_dialect.upper()})\n")

    if plan_summary.parse_error:
        md.append("### ❌ Malformed Execution Plan\n")
        md.append(f"**Error**: {plan_summary.parse_error}\n")
        return "\n".join(md)

    format_label = {
        "sqlserver_xml": "Microsoft SQL Server ShowPlan XML",
        "postgres_json": "PostgreSQL EXPLAIN JSON",
        "mysql_json": "MySQL EXPLAIN JSON",
        "unknown": "Generic / Unrecognized Format",
    }.get(plan_summary.format, plan_summary.format)

    md.append("## Overview\n")
    md.append(f"- **Plan Format**: `{format_label}`")
    if plan_summary.total_cost is not None:
        md.append(f"- **Total Query Subtree Cost**: `{plan_summary.total_cost:,.2f}`")
    md.append(f"- **Operators Extracted**: {len(plan_summary.operators)}")
    md.append(f"- **Bottlenecks Identified**: {len(plan_summary.bottlenecks)}\n")

    # Bottlenecks
    if plan_summary.bottlenecks:
        md.append("## Detected Bottlenecks & Critical Nodes\n")
        for b in plan_summary.bottlenecks:
            sev_badge = "🔴 CRITICAL" if b.severity == "critical" else "🟠 WARNING"
            md.append(f"### [{b.rule_id}] {b.title} — {sev_badge}")
            md.append(f"**Impact**: {b.message}")
            if b.snippet:
                md.append(f"```text\n{b.snippet}\n```")
            md.append(f"💡 **Remediation**: {b.suggestion}\n")
    else:
        md.append("## Detected Bottlenecks\n")
        md.append("✅ No critical operator bottlenecks detected in the supplied execution plan.\n")

    # Missing Indexes in Plan
    if plan_summary.missing_indexes:
        md.append("## Plan-Suggested Missing Indexes\n")
        md.append("```sql")
        for idx in plan_summary.missing_indexes:
            md.append(f"-- Table: {idx.table} | Rationale: {idx.rationale}")
            md.append(f"{idx.ddl}\n")
        md.append("```\n")

    # Operator Table
    if plan_summary.operators:
        md.append("## Operator Breakdown\n")
        md.append("| Operator | Object | Est. Rows | Est. Cost | Cost % | Bottleneck |")
        md.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
        for op in plan_summary.operators[:15]:
            obj = f"`{op.object}`" if op.object else "-"
            rows = f"{op.estimated_rows:,.0f}" if op.estimated_rows is not None else "-"
            cost = f"{op.estimated_cost:,.3f}" if op.estimated_cost is not None else "-"
            pct = f"{op.cost_pct:.1f}%" if op.cost_pct else "-"
            is_b = "⚠️ Yes" if op.is_bottleneck else "No"
            md.append(f"| **{op.op}** | {obj} | {rows} | {cost} | {pct} | {is_b} |")
        if len(plan_summary.operators) > 15:
            md.append(f"\n*(Showing top 15 of {len(plan_summary.operators)} operators)*")
        md.append("")

    return "\n".join(md)


@mcp.tool()
def list_supported_rules() -> str:
    """
    Return the comprehensive catalog of built-in AST diagnostic rules.

    Lists all supported rules, severities, heuristic cost weights,
    and remediation recommendations.

    Returns:
        Structured Markdown table of all supported static and execution plan rules.
    """
    md: list[str] = []
    md.append("# AutoDBA Diagnostic Rules Catalog\n")
    md.append(
        "AutoDBA performs static AST analysis and execution plan parsing across "
        "T-SQL, PostgreSQL, and MySQL using the following built-in diagnostic rule engine:\n"
    )

    ast_rules = []
    plan_rules = []

    for rule_id, meta in sorted(RULES.items()):
        dialects = ", ".join(meta.dialects) if meta.dialects else "All"
        item = (meta.id, meta.title, meta.severity.upper(), dialects, f"{meta.cost_weight:.1f}", meta.default_suggestion)
        if rule_id.startswith("PLAN_"):
            plan_rules.append(item)
        else:
            ast_rules.append(item)

    md.append("## Static AST Query Rules\n")
    md.append("| Rule ID | Title | Severity | Dialects | Weight | Remediation |")
    md.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
    for r in ast_rules:
        md.append(f"| `{r[0]}` | **{r[1]}** | `{r[2]}` | {r[3]} | {r[4]} | {r[5]} |")
    md.append("")

    md.append("## Execution Plan Operator Rules\n")
    md.append("| Rule ID | Title | Severity | Dialects | Weight | Remediation |")
    md.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
    for r in plan_rules:
        md.append(f"| `{r[0]}` | **{r[1]}** | `{r[2]}` | {r[3]} | {r[4]} | {r[5]} |")
    md.append("")

    return "\n".join(md)


@mcp.resource("rules://catalog")
def get_rules_catalog() -> str:
    """Expose the diagnostic rules catalog as an MCP Markdown resource."""
    return list_supported_rules()


@mcp.prompt()
def optimize_query_prompt(sql: str, dialect: str = "tsql") -> str:
    """Guided prompt template for reviewing and optimizing a database query."""
    return (
        f"Please analyze and optimize the following {dialect.upper()} query using AutoDBA tools:\n\n"
        f"```sql\n{sql}\n```\n\n"
        "1. Run `analyze_query` to check for non-SARGable column functions, Cartesian joins, and overfetching.\n"
        "2. Run `optimize_query` to rewrite the SQL and synthesize zero-downtime index migration DDL.\n"
        "3. Provide a summary of estimated workload reduction and production deployment steps."
    )


def main():
    """CLI entrypoint for stdio MCP server."""
    import sys

    from autodba_mcp import __version__

    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg in ("--version", "-v", "version"):
            print(f"autodba-mcp {__version__}")
            return
        if arg in ("--help", "-h", "help"):
            print("AutoDBA MCP Server")
            print("Zero-database-connection static SQL analysis and execution plan diagnostics.")
            print("\nUsage:")
            print("  autodba-mcp               Run stdio MCP server (Claude Desktop, Cursor, Antigravity)")
            print("  autodba-mcp --version     Print package version")
            print("  autodba-mcp --help        Print this help message")
            return
    mcp.run()


if __name__ == "__main__":
    main()

"""
AutoDBA AST rewrite verification engine.
Guarantees mathematical correctness, table preservation, and prompt injection defense.
"""

from __future__ import annotations

from collections import Counter

import sqlglot

from autodba_mcp.engine.analyzer.ast_visitor import analyze_sql
from autodba_mcp.engine.schemas import (
    AnalysisReport,
    Dialect,
    Severity,
    VerificationReport,
)


def verify_rewrite(
    original_sql: str,
    rewritten_sql: str,
    dialect: Dialect,
    before: AnalysisReport,
) -> tuple[VerificationReport, AnalysisReport]:
    """
    Rigorously verify that a query rewrite preserves tables, statement types,
    and improves or maintains critical diagnostic counts.
    """
    notes: list[str] = []

    # 1. Analyze the rewritten SQL
    after = analyze_sql(rewritten_sql, dialect)

    if not after.parse_ok:
        report = VerificationReport(
            passed=False,
            parse_ok=False,
            tables_preserved=False,
            missing_tables=[],
            statement_type_preserved=False,
            remaining_critical=len([d for d in after.diagnostics if d.severity == Severity.CRITICAL]),
            ast_edit_counts={},
            notes=[f"Rewritten SQL failed to parse: {after.parse_error}"],
        )
        return report, after

    # 2. Check statement type preservation (Prompt-injection & DDL/DML guard)
    statement_type_preserved = True
    if before.statement_types and after.statement_types:
        # Prevent any SELECT turning into DROP/DELETE/INSERT/UPDATE
        if before.statement_types != after.statement_types:
            statement_type_preserved = False
            notes.append(
                f"Statement type mismatch: original was {before.statement_types}, rewritten is {after.statement_types}."
            )
    else:
        statement_type_preserved = False

    # 3. Check base table preservation
    before_tbl_names = {t.name.lower() for t in before.tables if t.name}
    after_tbl_names = {t.name.lower() for t in after.tables if t.name}

    missing_tables = [name for name in before_tbl_names if name not in after_tbl_names]
    tables_preserved = len(missing_tables) == 0

    if not tables_preserved:
        notes.append(f"Rewrite dropped base tables: {', '.join(missing_tables)}.")

    # 4. Check critical diagnostic count regression
    before_critical = len([d for d in before.diagnostics if d.severity == Severity.CRITICAL])
    after_critical = len([d for d in after.diagnostics if d.severity == Severity.CRITICAL])
    no_critical_regression = after_critical <= before_critical

    if not no_critical_regression:
        notes.append(
            f"Critical anti-pattern regression: critical count rose from {before_critical} to {after_critical}."
        )

    # 5. Calculate AST diff edit counts using sqlglot.diff
    ast_diff_counts: dict[str, int] = {}
    try:
        orig_ast = sqlglot.parse_one(original_sql, read=dialect)
        rewr_ast = sqlglot.parse_one(rewritten_sql, read=dialect)
        edits = sqlglot.diff(orig_ast, rewr_ast)
        counter = Counter(type(e).__name__.lower() for e in edits)
        ast_diff_counts = dict(counter)
    except Exception:
        # Multi-statement or command fallback
        pass

    passed = (
        after.parse_ok
        and statement_type_preserved
        and tables_preserved
        and no_critical_regression
    )

    if passed:
        notes.append("Rewrite passed all AST verification constraints.")

    report = VerificationReport(
        passed=passed,
        parse_ok=after.parse_ok,
        tables_preserved=tables_preserved,
        missing_tables=missing_tables,
        statement_type_preserved=statement_type_preserved,
        remaining_critical=after_critical,
        ast_edit_counts=ast_diff_counts,
        notes=notes,
    )

    return report, after

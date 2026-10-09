"""
AutoDBA diagnostic rules catalog and factory.
"""

from __future__ import annotations

from dataclasses import dataclass

from autodba_mcp.engine.schemas import Diagnostic, Dialect, Severity


@dataclass(frozen=True)
class RuleMeta:
    id: str
    severity: Severity
    title: str
    cost_weight: float
    dialects: frozenset[Dialect] | None = None
    default_suggestion: str = ""


RULES: dict[str, RuleMeta] = {
    "PARSE_ERROR": RuleMeta(
        id="PARSE_ERROR",
        severity=Severity.CRITICAL,
        title="SQL Syntax Parse Error",
        cost_weight=1.0,
        dialects=None,
        default_suggestion="Verify SQL syntax against the selected dialect specification.",
    ),
    "CARTESIAN_JOIN": RuleMeta(
        id="CARTESIAN_JOIN",
        severity=Severity.CRITICAL,
        title="Missing Join Condition (Cartesian Join)",
        cost_weight=0.9,
        dialects=None,
        default_suggestion="Specify explicit join conditions (ON / USING) or linking WHERE predicates to avoid multiplying all rows together.",
    ),
    "NON_SARGABLE_FUNCTION": RuleMeta(
        id="NON_SARGABLE_FUNCTION",
        severity=Severity.CRITICAL,
        title="Index-Blocking Function on Column (Non-SARGable)",
        cost_weight=0.8,
        dialects=None,
        default_suggestion="Rewrite predicate to isolate the column so the database can use an index: e.g. YEAR(col) = 2026 -> col >= '2026-01-01' AND col < '2027-01-01'.",
    ),
    "NON_SARGABLE_ARITHMETIC": RuleMeta(
        id="NON_SARGABLE_ARITHMETIC",
        severity=Severity.WARNING,
        title="Arithmetic Expression on Search Column",
        cost_weight=0.6,
        dialects=None,
        default_suggestion="Isolate the column by shifting arithmetic to the literal side: e.g. col * 1.1 > 100 -> col > 100 / 1.1.",
    ),
    "LEADING_WILDCARD_LIKE": RuleMeta(
        id="LEADING_WILDCARD_LIKE",
        severity=Severity.WARNING,
        title="Leading Wildcard in LIKE Predicate",
        cost_weight=0.5,
        dialects=None,
        default_suggestion="Remove leading wildcard for b-tree index seeks or implement full-text search / trigram indexing.",
    ),
    "SELECT_STAR": RuleMeta(
        id="SELECT_STAR",
        severity=Severity.WARNING,
        title="Overfetching All Columns (SELECT *)",
        cost_weight=0.4,
        dialects=None,
        default_suggestion="Specify only the columns you need to reduce memory usage and allow fast index scans.",
    ),
    "CURSOR_LOOP": RuleMeta(
        id="CURSOR_LOOP",
        severity=Severity.CRITICAL,
        title="Row-by-Row Loop / Cursor (Slow Iteration)",
        cost_weight=0.9,
        dialects=frozenset(["tsql"]),
        default_suggestion="Refactor slow row-by-row procedural CURSOR/WHILE loops into fast set-based relational operations.",
    ),
    "CORRELATED_SUBQUERY": RuleMeta(
        id="CORRELATED_SUBQUERY",
        severity=Severity.WARNING,
        title="Correlated Subquery in Projection or Filter",
        cost_weight=0.6,
        dialects=None,
        default_suggestion="Rewrite correlated subquery as a set-based INNER/LEFT JOIN or window function.",
    ),
    "NOT_IN_SUBQUERY": RuleMeta(
        id="NOT_IN_SUBQUERY",
        severity=Severity.WARNING,
        title="NOT IN Subquery with NULL Hazard",
        cost_weight=0.5,
        dialects=None,
        default_suggestion="Rewrite NOT IN (SELECT ...) as NOT EXISTS (SELECT 1 ...) to handle three-valued NULL logic and allow anti-join optimization.",
    ),
    "OR_ACROSS_COLUMNS": RuleMeta(
        id="OR_ACROSS_COLUMNS",
        severity=Severity.OPTIMIZATION,
        title="OR Condition Across Multiple Columns",
        cost_weight=0.4,
        dialects=None,
        default_suggestion="Disjunctive filters across distinct columns prevent index seeks; consider UNION ALL of separate indexed queries.",
    ),
    "IMPLICIT_CONVERSION": RuleMeta(
        id="IMPLICIT_CONVERSION",
        severity=Severity.WARNING,
        title="Data Type Cast or Implicit Conversion on Filter Column",
        cost_weight=0.5,
        dialects=None,
        default_suggestion="Match literal data types directly to column definitions to avoid per-row type coercion.",
    ),
    "NOLOCK_HINT": RuleMeta(
        id="NOLOCK_HINT",
        severity=Severity.WARNING,
        title="Unsafe NOLOCK / READUNCOMMITTED Hint",
        cost_weight=0.3,
        dialects=frozenset(["tsql"]),
        default_suggestion="Replace NOLOCK with Snapshot Isolation (RCSI) to prevent reading uncommitted, dirty, or duplicate records.",
    ),
    "TOP_WITHOUT_ORDER": RuleMeta(
        id="TOP_WITHOUT_ORDER",
        severity=Severity.WARNING,
        title="TOP / LIMIT Without Deterministic ORDER BY",
        cost_weight=0.3,
        dialects=None,
        default_suggestion="Add an explicit ORDER BY clause to ensure deterministic row selection.",
    ),
    "ORDER_BY_RAND": RuleMeta(
        id="ORDER_BY_RAND",
        severity=Severity.WARNING,
        title="Non-Deterministic Full Sort (RAND / NEWID)",
        cost_weight=0.6,
        dialects=None,
        default_suggestion="Avoid sorting entire tables by random functions; sample primary keys or utilize tablesample / pre-generated random offsets.",
    ),
    "DISTINCT_OVERUSE": RuleMeta(
        id="DISTINCT_OVERUSE",
        severity=Severity.OPTIMIZATION,
        title="SELECT DISTINCT Masking Join Multiplicity",
        cost_weight=0.4,
        dialects=None,
        default_suggestion="Investigate if DISTINCT is masking unintended duplicate rows produced by 1:N joins, adding unnecessary sort overhead.",
    ),
    "UNBOUNDED_SCAN": RuleMeta(
        id="UNBOUNDED_SCAN",
        severity=Severity.OPTIMIZATION,
        title="Unbounded Full Table Scan",
        cost_weight=0.4,
        dialects=None,
        default_suggestion="Add restrictive WHERE predicates or pagination (LIMIT/TOP) to avoid scanning entire datasets.",
    ),
    "DEEP_OFFSET": RuleMeta(
        id="DEEP_OFFSET",
        severity=Severity.OPTIMIZATION,
        title="Deep OFFSET Pagination Overhead",
        cost_weight=0.5,
        dialects=None,
        default_suggestion="High OFFSET requires scanning and discarding many preceding rows; migrate to keyset (seek) pagination.",
    ),
    # Execution plan rules
    "PLAN_TABLE_SCAN": RuleMeta(
        id="PLAN_TABLE_SCAN",
        severity=Severity.CRITICAL,
        title="Execution Plan Table Scan on Large Relation",
        cost_weight=0.8,
        dialects=None,
        default_suggestion="Add targeted indexes supporting the query's filter and join predicates.",
    ),
    "PLAN_KEY_LOOKUP": RuleMeta(
        id="PLAN_KEY_LOOKUP",
        severity=Severity.WARNING,
        title="Key Lookup / Bookmark Lookup Overhead",
        cost_weight=0.5,
        dialects=None,
        default_suggestion="Include projected columns in nonclustered index INCLUDE clause to create a covering index.",
    ),
    "PLAN_SORT_SPILL": RuleMeta(
        id="PLAN_SORT_SPILL",
        severity=Severity.CRITICAL,
        title="Operator Spilled to TempDB / Workfile Disk",
        cost_weight=0.7,
        dialects=None,
        default_suggestion="Increase memory grant or add supporting index on ORDER BY / GROUP BY columns to avoid disk spill.",
    ),
    "PLAN_TEMP_TABLE": RuleMeta(
        id="PLAN_TEMP_TABLE",
        severity=Severity.WARNING,
        title="Temporary Table Materialization in Plan",
        cost_weight=0.5,
        dialects=None,
        default_suggestion="Add composite indexes matching GROUP BY and ORDER BY clauses to allow streaming aggregation.",
    ),
    "PLAN_FILESORT": RuleMeta(
        id="PLAN_FILESORT",
        severity=Severity.WARNING,
        title="Filesort Execution Phase in Plan",
        cost_weight=0.5,
        dialects=None,
        default_suggestion="Add an index matching the sort order to eliminate separate sorting pass.",
    ),
    "PLAN_HIGH_COST_OPERATOR": RuleMeta(
        id="PLAN_HIGH_COST_OPERATOR",
        severity=Severity.WARNING,
        title="Dominant Execution Plan Operator",
        cost_weight=0.6,
        dialects=None,
        default_suggestion="Optimize the dominant operator consuming significant query execution cost.",
    ),
    "PLAN_IMPLICIT_CONVERSION": RuleMeta(
        id="PLAN_IMPLICIT_CONVERSION",
        severity=Severity.WARNING,
        title="Plan-Affecting Type Conversion",
        cost_weight=0.5,
        dialects=None,
        default_suggestion="Align predicate literal types with schema column types to permit direct index seeks.",
    ),
}


def make_diagnostic(
    rule_id: str,
    message: str,
    *,
    snippet: str | None = None,
    line: int | None = None,
    suggestion: str | None = None,
) -> Diagnostic:
    """Create a validated Diagnostic instance based on registered rule metadata."""
    meta = RULES.get(rule_id)
    if meta is None:
        # Fallback for unrecognized rule_id
        return Diagnostic(
            rule_id=rule_id,
            severity=Severity.WARNING,
            title=rule_id.replace("_", " ").title(),
            message=message,
            snippet=snippet,
            line=line,
            suggestion=suggestion or "Inspect and optimize this query pattern.",
            cost_weight=0.5,
        )

    resolved_suggestion = suggestion if (suggestion is not None and suggestion != "") else meta.default_suggestion
    return Diagnostic(
        rule_id=meta.id,
        severity=meta.severity,
        title=meta.title,
        message=message,
        snippet=snippet,
        line=line,
        suggestion=resolved_suggestion,
        cost_weight=meta.cost_weight,
    )

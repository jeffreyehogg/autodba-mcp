"""
AutoDBA AST static analyzer powered by sqlglot.
Extracts schema targets, column usage telemetry, and diagnoses query antipatterns.
"""

from __future__ import annotations

import logging
import time

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from autodba_mcp.engine.analyzer.rules import make_diagnostic
from autodba_mcp.engine.schemas import (
    AnalysisReport,
    ColumnUsage,
    Diagnostic,
    Dialect,
    Severity,
    TableRef,
)

logger = logging.getLogger("sqlglot")
logger.setLevel(logging.ERROR)

# Safe function classes to evaluate for non-SARGable wrappers
_SARG_FUNCTION_TYPES: tuple[type[exp.Expression], ...] = tuple(
    cls
    for name in (
        "Year",
        "Month",
        "Day",
        "Cast",
        "TryCast",
        "Substring",
        "Left",
        "Right",
        "Upper",
        "Lower",
        "Trim",
        "Coalesce",
        "DateTrunc",
        "TimestampTrunc",
        "Date",
        "TsOrDsToDate",
        "DateAdd",
        "DateDiff",
        "Anonymous",
    )
    if (cls := getattr(exp, name, None)) is not None
)

_COMPARISON_TYPES: tuple[type[exp.Expression], ...] = (
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.Like,
    exp.ILike,
    exp.In,
    exp.Between,
)

_ARITHMETIC_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Add,
    exp.Sub,
    exp.Mul,
    exp.Div,
    exp.Mod,
)


def _find_line(sql: str, snippet: str | None) -> int | None:
    """Best-effort 1-based line finder for snippet in raw SQL."""
    if not snippet or not snippet.strip():
        return None
    snip_clean = snippet.strip().splitlines()[0].strip()
    if not snip_clean:
        return None
    for idx, line in enumerate(sql.splitlines(), start=1):
        if snip_clean in line:
            return idx
    # Fuzzy first token
    first_token = snip_clean.split()[0] if snip_clean.split() else ""
    if len(first_token) > 3:
        for idx, line in enumerate(sql.splitlines(), start=1):
            if first_token in line:
                return idx
    return None


def _get_table_name(table_exp: exp.Table) -> str:
    """Extract plain table name without schema or quotes."""
    return table_exp.name or table_exp.this.name if hasattr(table_exp.this, "name") else str(table_exp.this)


def _extract_ctes(ast: exp.Expression) -> set[str]:
    """Find all CTE names in statement."""
    cte_names: set[str] = set()
    for cte in ast.find_all(exp.CTE):
        if cte.alias:
            cte_names.add(cte.alias.lower())
    return cte_names


def _resolve_tables(ast: exp.Expression) -> tuple[list[TableRef], dict[str, str]]:
    """
    Extract base tables (ignoring CTE names).
    Returns (tables_list, alias_to_base_table_map).
    """
    cte_names = _extract_ctes(ast)
    tables: list[TableRef] = []
    alias_map: dict[str, str] = {}
    seen: set[tuple[str, str | None, str | None]] = set()

    for tbl in ast.find_all(exp.Table):
        name = _get_table_name(tbl)
        if not name or name.lower() in cte_names:
            continue
        schema = tbl.db or None
        alias = tbl.alias or None
        key = (name.lower(), schema.lower() if schema else None, alias.lower() if alias else None)
        if key not in seen:
            seen.add(key)
            tables.append(TableRef(name=name, schema_name=schema, alias=alias))

        # Map alias or name to base table name
        if alias:
            alias_map[alias.lower()] = name
        alias_map[name.lower()] = name

    return tables, alias_map


def _check_cartesian_join(
    select: exp.Select,
    dialect: Dialect,
    alias_map: dict[str, str],
    diagnostics: list[Diagnostic],
    sql: str,
) -> None:
    """Detect unconstrained Cartesian joins or implicit comma joins without predicates."""
    joins = select.args.get("joins") or []
    _from_clause = select.args.get("from_") or select.args.get("from")

    for join in joins:
        # Check explicit CROSS JOIN
        if join.kind and "CROSS" in join.kind.upper():
            snip = join.sql(dialect)
            diagnostics.append(
                make_diagnostic(
                    "CARTESIAN_JOIN",
                    f"Explicit CROSS JOIN on {join.this.sql(dialect)} will produce Cartesian product.",
                    snippet=snip,
                    line=_find_line(sql, snip),
                    suggestion="Ensure cross product is intentional, or convert to INNER/LEFT JOIN with an ON condition.",
                )
            )
            continue

        # Check comma join (Join with no 'on' and no 'using' and no 'kind')
        is_comma_join = not join.args.get("on") and not join.args.get("using") and not join.kind
        if is_comma_join:
            # Check if there is an equality linking condition in WHERE clause
            where = select.find(exp.Where)
            has_linking_eq = False
            if where:
                for eq in where.find_all(exp.EQ):
                    left_col = eq.left.find(exp.Column)
                    right_col = eq.right.find(exp.Column)
                    if left_col and right_col:
                        # Linking condition between distinct tables
                        t1 = (left_col.table or "").lower()
                        t2 = (right_col.table or "").lower()
                        if t1 != t2:
                            has_linking_eq = True
                            break

            if not has_linking_eq:
                snip = join.sql(dialect)
                diagnostics.append(
                    make_diagnostic(
                        "CARTESIAN_JOIN",
                        f"Unconstrained comma join on table {join.this.sql(dialect)} lacks explicit join or WHERE link.",
                        snippet=snip,
                        line=_find_line(sql, snip),
                        suggestion="Replace implicit comma join with explicit INNER JOIN ... ON syntax.",
                    )
                )


def _check_non_sargable_predicates(
    ast: exp.Expression,
    dialect: Dialect,
    diagnostics: list[Diagnostic],
    sql: str,
) -> None:
    """Detect functions or arithmetic wrapping columns in search predicates."""
    for node in ast.walk():
        # Check comparisons
        if not isinstance(node, _COMPARISON_TYPES):
            continue

        operands: list[exp.Expression] = []
        if isinstance(node, exp.Between):
            operands = [node.this]
        elif isinstance(node, exp.In):
            operands = [node.this]
        elif hasattr(node, "left") and hasattr(node, "right"):
            operands = [node.left, node.right]

        for op in operands:
            if op is None:
                continue

            # 1. Non-SARGable function wrapper
            for fn in op.find_all(_SARG_FUNCTION_TYPES):
                # Ensure it's not exp.And or exp.Or
                if isinstance(fn, (exp.And, exp.Or)):
                    continue

                col = fn.find(exp.Column)
                if col:
                    snip = node.sql(dialect)
                    func_name = fn.key.upper() if hasattr(fn, "key") and fn.key else type(fn).__name__
                    diagnostics.append(
                        make_diagnostic(
                            "NON_SARGABLE_FUNCTION",
                            f"Column {col.sql(dialect)} is wrapped in {func_name}() inside comparison.",
                            snippet=snip,
                            line=_find_line(sql, snip),
                            suggestion=f"Rewrite condition to isolate {col.sql(dialect)} on one side (e.g., using range bounds) to enable index seek.",
                        )
                    )
                    break

            # 2. Non-SARGable arithmetic on column
            for arith in op.find_all(_ARITHMETIC_TYPES):
                col = arith.find(exp.Column)
                if col:
                    snip = node.sql(dialect)
                    diagnostics.append(
                        make_diagnostic(
                            "NON_SARGABLE_ARITHMETIC",
                            f"Arithmetic expression on column {col.sql(dialect)} prevents index seek.",
                            snippet=snip,
                            line=_find_line(sql, snip),
                            suggestion="Shift arithmetic to the constant operand side: e.g. col * 1.1 > 100 -> col > 100 / 1.1.",
                        )
                    )
                    break


def _check_general_rules(
    ast: exp.Expression,
    dialect: Dialect,
    diagnostics: list[Diagnostic],
    sql: str,
) -> None:
    """Check remaining AST rules."""
    # 1. SELECT *
    for sel in ast.find_all(exp.Select):
        for expr in sel.expressions:
            if isinstance(expr, exp.Star):
                snip = sel.sql(dialect)[:80]
                diagnostics.append(
                    make_diagnostic(
                        "SELECT_STAR",
                        "Query projects unbounded columns using SELECT *.",
                        snippet=snip,
                        line=_find_line(sql, "*"),
                        suggestion="Enumerate explicit projection columns to prevent wide table scans and allow covering index optimization.",
                    )
                )
                break
            elif isinstance(expr, exp.Column) and expr.name == "*":
                snip = expr.sql(dialect)
                diagnostics.append(
                    make_diagnostic(
                        "SELECT_STAR",
                        f"Query projects table wildcard {snip}.",
                        snippet=snip,
                        line=_find_line(sql, snip),
                        suggestion="Explicitly name required columns rather than requesting entire row.",
                    )
                )
                break

    # 2. Leading wildcard in LIKE
    for like_node in ast.find_all((exp.Like, exp.ILike)):
        right_str = ""
        if isinstance(like_node.right, exp.Literal):
            right_str = str(like_node.right.this)
        if right_str.startswith("%") or right_str.startswith("_"):
            snip = like_node.sql(dialect)
            diagnostics.append(
                make_diagnostic(
                    "LEADING_WILDCARD_LIKE",
                    f"LIKE pattern '{right_str}' starts with wildcard, preventing b-tree index usage.",
                    snippet=snip,
                    line=_find_line(sql, snip),
                    suggestion="Remove leading wildcard if possible, or leverage full-text search / trigram indexing.",
                )
            )

    # 3. NOT IN (SELECT ...)
    for in_node in ast.find_all(exp.In):
        is_neg = bool(in_node.args.get("is_not") or isinstance(in_node.parent, exp.Not))
        if is_neg and in_node.find(exp.Select):
            snip = in_node.parent.sql(dialect) if isinstance(in_node.parent, exp.Not) else in_node.sql(dialect)
            diagnostics.append(
                make_diagnostic(
                    "NOT_IN_SUBQUERY",
                    "NOT IN subquery creates three-valued NULL hazards and disables anti-join optimizations.",
                    snippet=snip,
                    line=_find_line(sql, snip),
                    suggestion="Rewrite as NOT EXISTS (SELECT 1 ...) to safely handle potential NULL values.",
                )
            )

    # 4. Correlated subquery in SELECT
    for sel in ast.find_all(exp.Select):
        for expr in sel.expressions:
            subquery = expr.find(exp.Select)
            if subquery:
                snip = subquery.sql(dialect)[:100]
                diagnostics.append(
                    make_diagnostic(
                        "CORRELATED_SUBQUERY",
                        "Scalar subquery in SELECT projection evaluates row-by-row.",
                        snippet=snip,
                        line=_find_line(sql, snip),
                        suggestion="Convert scalar subquery into an explicit LEFT JOIN or analytic window function.",
                    )
                )

    # 5. OR across different columns in WHERE
    for where in ast.find_all(exp.Where):
        for or_node in where.find_all(exp.Or):
            cols = {c.name.lower() for c in or_node.find_all(exp.Column) if c.name}
            if len(cols) > 1:
                snip = or_node.sql(dialect)[:120]
                diagnostics.append(
                    make_diagnostic(
                        "OR_ACROSS_COLUMNS",
                        f"OR condition bridges distinct columns ({', '.join(cols)}), degrading index seeks into scans.",
                        snippet=snip,
                        line=_find_line(sql, snip),
                        suggestion="Evaluate splitting into separate queries joined by UNION ALL, or indexing each column independently.",
                    )
                )

    # 6. NOLOCK hint (tsql)
    if dialect == "tsql":
        sql_upper = sql.upper()
        if "NOLOCK" in sql_upper or "READUNCOMMITTED" in sql_upper:
            diagnostics.append(
                make_diagnostic(
                    "NOLOCK_HINT",
                    "Query specifies WITH (NOLOCK) / READUNCOMMITTED hint.",
                    snippet="WITH (NOLOCK)",
                    line=_find_line(sql, "NOLOCK"),
                    suggestion="Migrate to Read Committed Snapshot Isolation (RCSI) to avoid dirty reads, skipped records, and duplicate data.",
                )
            )

    # 7. ORDER BY RAND()
    for order in ast.find_all(exp.Order):
        for ordered in order.expressions:
            fn = ordered.find(exp.Func)
            if fn and fn.key.upper() in ("RAND", "RANDOM", "NEWID"):
                snip = order.sql(dialect)
                diagnostics.append(
                    make_diagnostic(
                        "ORDER_BY_RAND",
                        f"ORDER BY {fn.key.upper()} forces full table sort on random values.",
                        snippet=snip,
                        line=_find_line(sql, snip),
                        suggestion="Avoid random sorting across large tables; select by primary key offsets or use indexed sampling.",
                    )
                )

    # 8. TOP / LIMIT without ORDER BY
    for sel in ast.find_all(exp.Select):
        has_limit = bool(sel.args.get("limit") or sel.args.get("top"))
        has_order = bool(sel.args.get("order"))
        if has_limit and not has_order:
            snip = sel.sql(dialect)[:80]
            diagnostics.append(
                make_diagnostic(
                    "TOP_WITHOUT_ORDER",
                    "Query restricts row count (TOP/LIMIT) without a deterministic ORDER BY.",
                    snippet=snip,
                    line=_find_line(sql, "LIMIT") or _find_line(sql, "TOP"),
                    suggestion="Add an explicit ORDER BY clause to guarantee consistent and deterministic result sets.",
                )
            )

    # 9. DISTINCT with joins
    for sel in ast.find_all(exp.Select):
        is_distinct = bool(sel.args.get("distinct"))
        joins = sel.args.get("joins") or []
        if is_distinct and joins:
            snip = sel.sql(dialect)[:80]
            diagnostics.append(
                make_diagnostic(
                    "DISTINCT_OVERUSE",
                    "SELECT DISTINCT is applied across joined tables, often masking duplicate rows from 1:N relations.",
                    snippet=snip,
                    line=_find_line(sql, "DISTINCT"),
                    suggestion="Verify join cardinalities; consider EXISTS or GROUP BY instead of forcing an expensive sorting deduplication pass.",
                )
            )

    # 10. Deep OFFSET
    for offset_node in ast.find_all(exp.Offset):
        try:
            val = int(offset_node.expression.this)
            if val >= 1000:
                snip = offset_node.sql(dialect)
                diagnostics.append(
                    make_diagnostic(
                        "DEEP_OFFSET",
                        f"High OFFSET ({val}) requires reading and discarding {val} rows on every request.",
                        snippet=snip,
                        line=_find_line(sql, "OFFSET"),
                        suggestion="Refactor from offset pagination to keyset (seek) pagination using indexed column bounds.",
                    )
                )
        except (ValueError, TypeError, AttributeError):
            pass

    # 11. Unbounded scan (simple SELECT from table with no WHERE and no LIMIT)
    for sel in ast.find_all(exp.Select):
        from_clause = sel.args.get("from_") or sel.args.get("from")
        has_where = bool(sel.args.get("where"))
        has_limit = bool(sel.args.get("limit") or sel.args.get("top"))
        if from_clause and not has_where and not has_limit:
            snip = sel.sql(dialect)[:80]
            diagnostics.append(
                make_diagnostic(
                    "UNBOUNDED_SCAN",
                    "Query selects from table without WHERE predicates or row limits.",
                    snippet=snip,
                    line=_find_line(sql, snip),
                    suggestion="Add restrictive WHERE predicates or pagination boundaries to avoid full table scans.",
                )
            )


def _check_cursor_loops(
    statements: list[exp.Expression],
    dialect: Dialect,
    diagnostics: list[Diagnostic],
    sql: str,
) -> None:
    """Detect T-SQL CURSOR declaration, FETCH NEXT, and WHILE loops."""
    raw_upper = sql.upper()
    if dialect == "tsql":
        if "CURSOR FOR" in raw_upper or "FETCH NEXT" in raw_upper:
            diagnostics.append(
                make_diagnostic(
                    "CURSOR_LOOP",
                    "Procedural CURSOR iteration detected (RBAR: Row-By-Agonizing-Row).",
                    snippet="DECLARE ... CURSOR",
                    line=_find_line(sql, "CURSOR"),
                    suggestion="Replace procedural CURSOR loop with set-based operations or window analytic calculations.",
                )
            )

    for stmt in statements:
        if stmt is None:
            continue
        if isinstance(stmt, exp.WhileBlock):
            snip = stmt.sql(dialect)[:80]
            diagnostics.append(
                make_diagnostic(
                    "CURSOR_LOOP",
                    "Procedural WHILE loop block detected.",
                    snippet=snip,
                    line=_find_line(sql, "WHILE"),
                    suggestion="Refactor iterative WHILE loop into set-based relational operations.",
                )
            )


def _extract_column_usages(
    ast: exp.Expression,
    alias_map: dict[str, str],
) -> list[ColumnUsage]:
    """Inspect column usages across predicates, projections, and groupings."""
    usages: list[ColumnUsage] = []
    seen: set[tuple[str | None, str, str]] = set()

    def record(tbl_alias: str | None, col_name: str, kind: str) -> None:
        if not col_name or col_name == "*":
            return
        base_tbl = None
        if tbl_alias:
            base_tbl = alias_map.get(tbl_alias.lower())
        elif len(alias_map) == 1:
            base_tbl = list(alias_map.values())[0]
        key = (base_tbl, col_name, kind)
        if key not in seen:
            seen.add(key)
            usages.append(ColumnUsage(table=base_tbl, column=col_name, usage=kind))  # type: ignore

    # WHERE clauses
    for where in ast.find_all(exp.Where):
        # Equality: col = literal / param
        for eq in where.find_all(exp.EQ):
            c1 = eq.left.find(exp.Column)
            c2 = eq.right.find(exp.Column)
            if c1 and c2:
                # Join predicate
                record(c1.table, c1.name, "join")
                record(c2.table, c2.name, "join")
            elif c1:
                record(c1.table, c1.name, "equality")
            elif c2:
                record(c2.table, c2.name, "equality")

        # Range predicates
        for comp in where.find_all((exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between, exp.Like, exp.ILike)):
            for col in comp.find_all(exp.Column):
                record(col.table, col.name, "range")

    # JOIN ON conditions
    for join in ast.find_all(exp.Join):
        on = join.args.get("on")
        if on:
            for col in on.find_all(exp.Column):
                record(col.table, col.name, "join")

    # ORDER BY
    for order in ast.find_all(exp.Order):
        for col in order.find_all(exp.Column):
            record(col.table, col.name, "order")

    # GROUP BY
    for group in ast.find_all(exp.Group):
        for col in group.find_all(exp.Column):
            record(col.table, col.name, "group")

    # Projections (SELECT)
    for sel in ast.find_all(exp.Select):
        for proj in sel.expressions:
            for col in proj.find_all(exp.Column):
                record(col.table, col.name, "select")

    return usages


def _calculate_complexity(
    ast: exp.Expression,
    tables: list[TableRef],
    diagnostics: list[Diagnostic],
) -> int:
    """Calculate 0-100 complexity score."""
    score = 10
    score += len(tables) * 8
    score += len(list(ast.find_all(exp.Join))) * 10
    score += len(list(ast.find_all(exp.CTE))) * 12
    score += len(list(ast.find_all(exp.Union))) * 15
    score += len(list(ast.find_all(exp.Group))) * 10
    score += len(list(ast.find_all(exp.Order))) * 5
    score += len(diagnostics) * 6
    return min(100, max(5, score))


_SEVERITY_ORDER = {
    Severity.CRITICAL: 0,
    Severity.WARNING: 1,
    Severity.OPTIMIZATION: 2,
}


def analyze_sql(sql: str, dialect: Dialect) -> AnalysisReport:
    """
    Statically parse and analyze SQL text using sqlglot.
    Guaranteed never to raise exceptions.
    """
    t0 = time.perf_counter()

    if not sql or not sql.strip():
        return AnalysisReport(
            dialect=dialect,
            parse_ok=False,
            parse_error="SQL query is empty.",
            complexity_score=0,
            normalized_sql="",
            duration_ms=0.0,
        )

    try:
        raw_statements = sqlglot.parse(sql, read=dialect)
    except ParseError as pe:
        elapsed = (time.perf_counter() - t0) * 1000
        diag = make_diagnostic(
            "PARSE_ERROR",
            f"Failed to parse SQL: {str(pe)[:180]}",
            suggestion="Check syntax for dialect compatibility.",
        )
        return AnalysisReport(
            dialect=dialect,
            parse_ok=False,
            parse_error=str(pe),
            statement_count=0,
            statement_types=[],
            tables=[],
            column_usage=[],
            diagnostics=[diag],
            complexity_score=0,
            normalized_sql=sql,
            duration_ms=round(elapsed, 2),
        )
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        diag = make_diagnostic(
            "PARSE_ERROR",
            f"Unexpected parser error: {str(e)[:180]}",
            suggestion="Check syntax for dialect compatibility.",
        )
        return AnalysisReport(
            dialect=dialect,
            parse_ok=False,
            parse_error=str(e),
            statement_count=0,
            statement_types=[],
            tables=[],
            column_usage=[],
            diagnostics=[diag],
            complexity_score=0,
            normalized_sql=sql,
            duration_ms=round(elapsed, 2),
        )

    statements = [s for s in raw_statements if s is not None]
    if not statements:
        elapsed = (time.perf_counter() - t0) * 1000
        return AnalysisReport(
            dialect=dialect,
            parse_ok=False,
            parse_error="No valid SQL statements found.",
            statement_count=0,
            statement_types=[],
            tables=[],
            column_usage=[],
            diagnostics=[
                make_diagnostic("PARSE_ERROR", "No executable SQL statements found in input.")
            ],
            complexity_score=0,
            normalized_sql=sql,
            duration_ms=round(elapsed, 2),
        )

    stmt_types = [type(s).__name__.upper() for s in statements]

    # Combine analysis across statements
    all_tables: list[TableRef] = []
    alias_map_all: dict[str, str] = {}
    diagnostics: list[Diagnostic] = []
    column_usages: list[ColumnUsage] = []
    normalized_parts: list[str] = []

    # Check for procedural loops across raw statement set
    _check_cursor_loops(statements, dialect, diagnostics, sql)

    for stmt in statements:
        tables, alias_map = _resolve_tables(stmt)
        all_tables.extend(tables)
        alias_map_all.update(alias_map)

        # Check Selects
        for select in stmt.find_all(exp.Select):
            _check_cartesian_join(select, dialect, alias_map, diagnostics, sql)

        _check_non_sargable_predicates(stmt, dialect, diagnostics, sql)
        _check_general_rules(stmt, dialect, diagnostics, sql)

        column_usages.extend(_extract_column_usages(stmt, alias_map_all))
        try:
            normalized_parts.append(stmt.sql(dialect=dialect, pretty=True))
        except Exception:
            normalized_parts.append(stmt.sql())

    # Deduplicate tables
    seen_tables: set[tuple[str, str | None]] = set()
    dedup_tables: list[TableRef] = []
    for t in all_tables:
        k = (t.name.lower(), t.schema_name.lower() if t.schema_name else None)
        if k not in seen_tables:
            seen_tables.add(k)
            dedup_tables.append(t)

    # Deduplicate diagnostics by (rule_id, snippet)
    seen_diags: set[tuple[str, str | None]] = set()
    dedup_diags: list[Diagnostic] = []
    for d in diagnostics:
        snip_key = d.snippet.strip() if d.snippet else None
        key = (d.rule_id, snip_key)
        if key not in seen_diags:
            seen_diags.add(key)
            dedup_diags.append(d)

    # Sort diagnostics: critical first, then warning, then optimization
    dedup_diags.sort(key=lambda d: _SEVERITY_ORDER.get(d.severity, 99))

    primary_stmt = statements[0]
    complexity = _calculate_complexity(primary_stmt, dedup_tables, dedup_diags)
    duration = (time.perf_counter() - t0) * 1000

    return AnalysisReport(
        dialect=dialect,
        parse_ok=True,
        parse_error=None,
        statement_count=len(statements),
        statement_types=stmt_types,
        tables=dedup_tables,
        column_usage=column_usages,
        diagnostics=dedup_diags,
        complexity_score=complexity,
        normalized_sql="\n;\n".join(normalized_parts),
        duration_ms=round(duration, 2),
    )

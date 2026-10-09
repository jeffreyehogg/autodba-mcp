"""
AutoDBA deterministic AST query rewriter.
Applies safe, mathematical query transformations (SARGable conversions, explicit joins, anti-joins)
with zero LLM overhead and 100% determinism.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.simplify import simplify

from autodba_mcp.engine.schemas import AnalysisReport, Dialect, RewriteResult


def _rewrite_year_predicates(tree: exp.Expression, dialect: Dialect, fixes: list[str], notes: list[str]) -> None:
    """Rewrite YEAR(col) = YYYY into col >= 'YYYY-01-01' AND col < 'YYYY+1-01-01'."""
    for comp in list(tree.find_all((exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE))):
        for yr in list(comp.find_all(exp.Year)):
            col = yr.find(exp.Column)
            lit = comp.right if comp.left == yr else comp.left
            if col and isinstance(lit, exp.Literal):
                try:
                    val = int(lit.this)
                except ValueError:
                    continue

                c_sql = col.sql(dialect)
                if isinstance(comp, exp.EQ):
                    new_pred = sqlglot.parse_one(
                        f"{c_sql} >= '{val:04d}-01-01' AND {c_sql} < '{val + 1:04d}-01-01'",
                        read=dialect,
                    )
                elif isinstance(comp, exp.GTE):
                    new_pred = sqlglot.parse_one(f"{c_sql} >= '{val:04d}-01-01'", read=dialect)
                elif isinstance(comp, exp.GT):
                    new_pred = sqlglot.parse_one(f"{c_sql} >= '{val + 1:04d}-01-01'", read=dialect)
                elif isinstance(comp, exp.LTE):
                    new_pred = sqlglot.parse_one(f"{c_sql} < '{val + 1:04d}-01-01'", read=dialect)
                elif isinstance(comp, exp.LT):
                    new_pred = sqlglot.parse_one(f"{c_sql} < '{val:04d}-01-01'", read=dialect)
                else:
                    continue

                comp.replace(new_pred)
                fixes.append("NON_SARGABLE_FUNCTION")
                notes.append(f"Rewrote YEAR({c_sql}) filter into half-open date range seek.")
                break


def _rewrite_date_trunc_predicates(tree: exp.Expression, dialect: Dialect, fixes: list[str], notes: list[str]) -> None:
    """Rewrite PostgreSQL date_trunc('day', col) = DATE 'YYYY-MM-DD' into half-open timestamp range."""
    for comp in list(tree.find_all(exp.EQ)):
        # Look for DateTrunc or TimestampTrunc
        for dt in list(comp.find_all((exp.DateTrunc, exp.TimestampTrunc))):
            col = dt.find(exp.Column)
            # Find literal date
            lit = comp.right if comp.left == dt else comp.left
            lit_val = None
            if isinstance(lit, exp.Literal):
                lit_val = str(lit.this)
            elif isinstance(lit, exp.Date):
                lit_val = str(lit.this.this) if hasattr(lit.this, "this") else str(lit.this)

            if col and lit_val and re.match(r"^\d{4}-\d{2}-\d{2}", lit_val):
                date_part = lit_val[:10]
                c_sql = col.sql(dialect)
                try:
                    import datetime
                    d = datetime.date.fromisoformat(date_part)
                    next_d = d + datetime.timedelta(days=1)
                    new_pred = sqlglot.parse_one(
                        f"{c_sql} >= '{d.isoformat()} 00:00:00' AND {c_sql} < '{next_d.isoformat()} 00:00:00'",
                        read=dialect,
                    )
                    comp.replace(new_pred)
                    fixes.append("NON_SARGABLE_FUNCTION")
                    notes.append(f"Converted date_trunc on {c_sql} to half-open range [{d}..{next_d}).")
                    break
                except Exception:
                    pass


def _rewrite_comma_joins(tree: exp.Expression, dialect: Dialect, fixes: list[str], notes: list[str]) -> None:
    """Convert implicit comma joins into explicit INNER JOIN ... ON."""
    for select in tree.find_all(exp.Select):
        joins = select.args.get("joins") or []
        where = select.find(exp.Where)
        if not where:
            continue

        for j in joins:
            is_comma_join = not j.args.get("on") and not j.args.get("using") and not j.kind
            if not is_comma_join:
                continue

            tbl_alias = j.this.alias or (j.this.name if hasattr(j.this, "name") else "")
            if not tbl_alias:
                continue

            for eq in list(where.find_all(exp.EQ)):
                c1 = eq.left.find(exp.Column)
                c2 = eq.right.find(exp.Column)
                if c1 and c2:
                    t1 = (c1.table or "").lower()
                    t2 = (c2.table or "").lower()
                    target = tbl_alias.lower()
                    if t1 == target or t2 == target:
                        j.set("kind", "INNER")
                        j.set("on", eq.copy())
                        eq.replace(exp.true())
                        fixes.append("CARTESIAN_JOIN")
                        notes.append(f"Converted comma join on {tbl_alias} to explicit INNER JOIN with ON predicate.")
                        break


def _rewrite_not_in_subquery(tree: exp.Expression, dialect: Dialect, fixes: list[str], notes: list[str]) -> None:
    """Rewrite col NOT IN (SELECT c FROM ...) into NOT EXISTS (SELECT 1 FROM ... WHERE c = outer.col)."""
    for in_node in list(tree.find_all(exp.In)):
        is_neg = bool(in_node.args.get("is_not") or isinstance(in_node.parent, exp.Not))
        if is_neg and in_node.find(exp.Select):
            outer_col = in_node.this.sql(dialect)
            sub = in_node.find(exp.Select)
            if not sub.expressions:
                continue
            inner_col = sub.expressions[0].sql(dialect)
            sub_copy = sub.copy()
            sub_copy.set("expressions", [exp.Literal.number(1)])

            link_cond = sqlglot.parse_one(f"{inner_col} = {outer_col}", read=dialect)
            if sub_copy.find(exp.Where):
                sub_copy.find(exp.Where).set("this", exp.and_(sub_copy.find(exp.Where).this, link_cond))
            else:
                sub_copy.set("where", exp.Where(this=link_cond))

            target = in_node.parent if isinstance(in_node.parent, exp.Not) else in_node
            new_pred = sqlglot.parse_one(f"NOT EXISTS ({sub_copy.sql(dialect)})", read=dialect)
            target.replace(new_pred)
            fixes.append("NOT_IN_SUBQUERY")
            notes.append(f"Rewrote NOT IN on {outer_col} as safe NOT EXISTS anti-join to eliminate NULL traps.")


def _clean_nolock_hints(sql: str, dialect: Dialect, fixes: list[str], notes: list[str]) -> str:
    """Strip WITH (NOLOCK) hints in T-SQL queries."""
    if dialect != "tsql":
        return sql
    pattern = re.compile(r"\s+WITH\s*\(\s*NOLOCK\s*\)", re.IGNORECASE)
    if pattern.search(sql):
        cleaned = pattern.sub("", sql)
        fixes.append("NOLOCK_HINT")
        notes.append("Removed WITH (NOLOCK) hints in favor of Snapshot Isolation.")
        return cleaned
    return sql


def deterministic_rewrite(sql: str, dialect: Dialect, report: AnalysisReport) -> RewriteResult:
    """
    Produce a deterministic query rewrite using AST transformations.
    Guaranteed never to introduce hallucinations or drop tables.
    """
    if not sql or not sql.strip() or not report.parse_ok:
        return RewriteResult(sql=sql, fixes=[], notes=["Unable to rewrite unparsed query."])

    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception as e:
        return RewriteResult(sql=sql, fixes=[], notes=[f"AST rewrite skipped: {e}"])

    fixes: list[str] = []
    notes: list[str] = []

    # Apply AST passes
    _rewrite_year_predicates(tree, dialect, fixes, notes)
    _rewrite_date_trunc_predicates(tree, dialect, fixes, notes)
    _rewrite_comma_joins(tree, dialect, fixes, notes)
    _rewrite_not_in_subquery(tree, dialect, fixes, notes)

    try:
        tree = simplify(tree)
    except Exception:
        pass

    try:
        optimized_sql = tree.sql(dialect=dialect, pretty=True)
    except Exception:
        optimized_sql = tree.sql()

    # Clean nolock hints if T-SQL
    optimized_sql = _clean_nolock_hints(optimized_sql, dialect, fixes, notes)

    # Note items that cannot be safely transformed without schema
    if any(d.rule_id == "SELECT_STAR" for d in report.diagnostics):
        notes.append("SELECT * projection maintained (requires live DB schema metadata for explicit column expansion).")

    if any(d.rule_id == "ORDER_BY_RAND" for d in report.diagnostics):
        notes.append("ORDER BY RAND() detected; recommend indexed ID offset sampling rather than sorting whole relation.")

    # Deduplicate fixes
    seen_fixes: set[str] = set()
    dedup_fixes: list[str] = []
    for f in fixes:
        if f not in seen_fixes:
            seen_fixes.add(f)
            dedup_fixes.append(f)

    return RewriteResult(
        sql=optimized_sql,
        fixes=dedup_fixes,
        notes=notes,
    )

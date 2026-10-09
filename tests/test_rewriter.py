"""
Tests for deterministic AST query rewriter.
"""

from autodba_mcp.engine.analyzer.ast_visitor import analyze_sql
from autodba_mcp.engine.optimizer.rewriter import deterministic_rewrite


def test_rewrite_year_function():
    sql = "SELECT LeadId FROM dbo.Leads l WHERE YEAR(l.CreatedDate) = 2026"
    rep = analyze_sql(sql, "tsql")
    res = deterministic_rewrite(sql, "tsql", rep)
    assert "NON_SARGABLE_FUNCTION" in res.fixes
    assert "2026-01-01" in res.sql
    assert "2027-01-01" in res.sql


def test_rewrite_comma_join():
    sql = "SELECT l.LeadId FROM dbo.Leads l, dbo.Communities c WHERE l.CommunityId = c.CommunityId AND l.Active = 1"
    rep = analyze_sql(sql, "tsql")
    res = deterministic_rewrite(sql, "tsql", rep)
    assert "CARTESIAN_JOIN" in res.fixes
    assert "INNER JOIN" in res.sql


def test_rewrite_strip_nolock():
    sql = "SELECT LeadId FROM dbo.Leads WITH (NOLOCK) WHERE Active = 1"
    rep = analyze_sql(sql, "tsql")
    res = deterministic_rewrite(sql, "tsql", rep)
    assert "NOLOCK_HINT" in res.fixes
    assert "NOLOCK" not in res.sql.upper()


def test_rewrite_not_in_to_not_exists():
    sql = "SELECT id FROM events e WHERE e.badge_id NOT IN (SELECT badge_id FROM revoked_badges)"
    rep = analyze_sql(sql, "postgres")
    res = deterministic_rewrite(sql, "postgres", rep)
    assert "NOT_IN_SUBQUERY" in res.fixes
    assert "NOT EXISTS" in res.sql.upper()

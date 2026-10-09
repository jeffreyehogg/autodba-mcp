"""
Tests for query rewrite verifier (table preservation, statement type safety).
"""

from autodba_mcp.engine.analyzer.ast_visitor import analyze_sql
from autodba_mcp.engine.optimizer.verifier import verify_rewrite


def test_verifier_passes_on_valid_rewrite():
    orig = "SELECT * FROM dbo.Leads l WHERE YEAR(l.CreatedDate) = 2026"
    rewr = "SELECT l.LeadId, l.Email FROM dbo.Leads l WHERE l.CreatedDate >= '2026-01-01' AND l.CreatedDate < '2027-01-01'"
    before = analyze_sql(orig, "tsql")
    v_rep, after = verify_rewrite(orig, rewr, "tsql", before)
    assert v_rep.passed
    assert v_rep.tables_preserved
    assert v_rep.statement_type_preserved
    assert "insert" in v_rep.ast_edit_counts or "update" in v_rep.ast_edit_counts or "remove" in v_rep.ast_edit_counts


def test_verifier_fails_when_table_dropped():
    orig = "SELECT l.Id, c.Name FROM Leads l JOIN Communities c ON c.Id = l.CId"
    bad_rewr = "SELECT l.Id FROM Leads l"  # Dropped Communities table
    before = analyze_sql(orig, "tsql")
    v_rep, _ = verify_rewrite(orig, bad_rewr, "tsql", before)
    assert not v_rep.passed
    assert not v_rep.tables_preserved
    assert "communities" in v_rep.missing_tables


def test_verifier_fails_on_statement_type_mismatch():
    orig = "SELECT * FROM Leads"
    bad_rewr = "DELETE FROM Leads"  # Destructive mutation injection
    before = analyze_sql(orig, "tsql")
    v_rep, _ = verify_rewrite(orig, bad_rewr, "tsql", before)
    assert not v_rep.passed
    assert not v_rep.statement_type_preserved

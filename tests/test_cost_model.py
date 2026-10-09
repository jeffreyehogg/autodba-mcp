"""
Tests for heuristic cost estimation and logical reads.
"""

from autodba_mcp.engine.analyzer.ast_visitor import analyze_sql
from autodba_mcp.engine.optimizer.cost_model import estimate_cost
from autodba_mcp.engine.optimizer.ddl_generator import synthesize_indexes


def test_cost_model_improvement_and_clamping():
    orig = "SELECT * FROM dbo.Leads l WHERE YEAR(l.CreatedDate) = 2026"
    rewr = "SELECT l.LeadId FROM dbo.Leads l WHERE l.CreatedDate >= '2026-01-01' AND l.CreatedDate < '2027-01-01'"
    before = analyze_sql(orig, "tsql")
    after = analyze_sql(rewr, "tsql")
    idxs = synthesize_indexes(before)

    cost_est, pct = estimate_cost(before, after, None, idxs)
    assert 0 <= pct <= 95
    assert cost_est.after_cost <= cost_est.before_cost
    assert cost_est.logical_reads_after <= cost_est.logical_reads_before


def test_cost_model_determinism():
    orig = "SELECT id FROM orders WHERE DATE(created_at) = '2026-01-01'"
    before = analyze_sql(orig, "mysql")
    after = analyze_sql(orig, "mysql")
    est1, pct1 = estimate_cost(before, after, None, [])
    est2, pct2 = estimate_cost(before, after, None, [])
    assert pct1 == pct2
    assert est1.before_cost == est2.before_cost

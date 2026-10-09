"""
Tests for agent optimization workflow and mock LLM loop.
"""

import asyncio

from autodba_mcp.engine.optimizer.agent import run_optimization
from autodba_mcp.engine.schemas import LLMRewrite


def test_agent_deterministic_flow():
    sql = "SELECT l.* FROM dbo.Leads l WHERE YEAR(l.CreatedDate) = 2026"
    res = asyncio.run(run_optimization(sql, "tsql", None, api_key=None, allow_llm=False))
    assert res.engine == "deterministic"
    assert res.verification.passed
    assert len(res.trace) >= 4
    assert res.estimated_improvement_pct > 0


def test_agent_mock_llm_success(monkeypatch):
    from autodba_mcp.engine.optimizer import agent

    mock_rewrite = LLMRewrite(
        optimized_sql="SELECT l.LeadId, l.Email FROM dbo.Leads l WHERE l.CreatedDate >= '2026-01-01' AND l.CreatedDate < '2027-01-01'",
        explanation="Eliminated non-SARGable YEAR function using half-open bounds and explicit column projections.",
        antipatterns_fixed=["NON_SARGABLE_FUNCTION", "SELECT_STAR"],
        ddl_recommendations=[],
        estimated_improvement_pct=90,
    )

    async def fake_generate(*args, **kwargs):
        return mock_rewrite

    monkeypatch.setattr(agent, "generate_rewrite", fake_generate)

    sql = "SELECT * FROM dbo.Leads l WHERE YEAR(l.CreatedDate) = 2026"
    res = asyncio.run(run_optimization(sql, "tsql", None, api_key="fake-key-for-test-mock123", allow_llm=True))
    assert res.engine == "gemini"
    assert res.verification.passed
    assert any(step.node == "verify" and step.status == "ok" for step in res.trace)


def test_agent_mock_llm_critique_retry(monkeypatch):
    """Simulate iteration 1 failing verification (dropped table), then iteration 2 succeeding."""
    from autodba_mcp.engine.optimizer import agent

    calls = 0

    async def fake_generate(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            # Bad rewrite: drops Leads table
            return LLMRewrite(
                optimized_sql="SELECT 1",
                explanation="Bad rewrite",
                antipatterns_fixed=[],
                ddl_recommendations=[],
                estimated_improvement_pct=10,
            )
        else:
            # Good rewrite: preserves Leads table
            return LLMRewrite(
                optimized_sql="SELECT l.LeadId FROM dbo.Leads l WHERE l.CreatedDate >= '2026-01-01'",
                explanation="Good retry rewrite",
                antipatterns_fixed=["NON_SARGABLE_FUNCTION"],
                ddl_recommendations=[],
                estimated_improvement_pct=85,
            )

    monkeypatch.setattr(agent, "generate_rewrite", fake_generate)

    sql = "SELECT * FROM dbo.Leads l WHERE YEAR(l.CreatedDate) = 2026"
    res = asyncio.run(run_optimization(sql, "tsql", None, api_key="fake-key-for-test-mock123", allow_llm=True))
    assert res.engine == "gemini"
    assert calls == 2
    assert any(step.node == "critique" for step in res.trace)

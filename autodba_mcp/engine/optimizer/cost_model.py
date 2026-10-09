"""
AutoDBA simulated I/O cost model.
Provides deterministic, repeatable before/after workload telemetry estimates.
"""

from __future__ import annotations

from autodba_mcp.engine.schemas import (
    AnalysisReport,
    CostEstimate,
    IndexRecommendation,
    PlanSummary,
)


def estimate_cost(
    before: AnalysisReport,
    after: AnalysisReport,
    plan: PlanSummary | None,
    indexes: list[IndexRecommendation],
) -> tuple[CostEstimate, int]:
    """
    Calculate deterministic before/after cost estimates and improvement percentage.
    Clamped strictly between 0% and 95%.
    """
    # 1. Establish baseline cost
    if plan and plan.total_cost and plan.total_cost > 0:
        base_cost = float(plan.total_cost)
    else:
        # Heuristic baseline from complexity and diagnostics
        diag_weight = sum(d.cost_weight * 15.0 for d in before.diagnostics)
        base_cost = max(20.0, float(before.complexity_score) * 1.5 + diag_weight)

    # 2. Calculate diagnostic weight reduction
    before_diag_weight = sum(d.cost_weight for d in before.diagnostics)
    after_diag_weight = sum(d.cost_weight for d in after.diagnostics)
    diag_reduction_ratio = 0.0
    if before_diag_weight > 0:
        diag_reduction_ratio = max(0.0, (before_diag_weight - after_diag_weight) / before_diag_weight)

    # 3. Factor in supporting indexes
    # Each relevant synthesized index cuts remaining cost by 20%
    index_factor = min(0.6, len(indexes) * 0.20)

    # Overall reduction
    reduction_pct_raw = (diag_reduction_ratio * 0.55 + index_factor) * 100.0

    # If there were severe bottlenecks (table scan, Cartesian join, cursor loop)
    has_severe = any(
        d.rule_id in ("CARTESIAN_JOIN", "CURSOR_LOOP", "NON_SARGABLE_FUNCTION", "PLAN_TABLE_SCAN")
        for d in before.diagnostics
    )
    if has_severe and reduction_pct_raw < 50.0:
        reduction_pct_raw += 30.0

    improvement_pct = int(min(95.0, max(0.0, reduction_pct_raw)))

    # Compute after cost
    factor = 1.0 - (improvement_pct / 100.0)
    after_cost = max(1.0, round(base_cost * factor, 2))
    before_cost = max(after_cost + 1.0, round(base_cost, 2))

    # Compute simulated logical reads (1 read ~= 8KB page)
    reads_scale = 80
    reads_before = int(max(1000, before_cost * reads_scale))
    reads_after = int(max(100, reads_before * factor))

    cost_estimate = CostEstimate(
        before_cost=before_cost,
        after_cost=after_cost,
        logical_reads_before=reads_before,
        logical_reads_after=reads_after,
    )

    return cost_estimate, improvement_pct

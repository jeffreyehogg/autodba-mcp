"""
AutoDBA shared data contracts (Pydantic v2).

This module is the SINGLE SOURCE OF TRUTH for every payload that crosses a
module boundary (analyzer -> optimizer -> API -> frontend). The TypeScript
mirror lives in `apps/autodba/lib/types.ts` and must be kept in sync.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

Dialect = Literal["tsql", "postgres", "mysql"]
ColumnUsageKind = Literal["equality", "range", "join", "select", "order", "group"]
EngineKind = Literal["gemini", "deterministic", "preset"]
ImpactLevel = Literal["high", "medium", "low"]
PlanFormat = Literal["sqlserver_xml", "postgres_json", "mysql_json", "unknown"]
AgentNode = Literal[
    "analyze",
    "plan",
    "rewrite_deterministic",
    "llm_rewrite",
    "verify",
    "critique",
    "finalize",
]
StepStatus = Literal["ok", "retry", "failed", "skipped"]


class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    OPTIMIZATION = "optimization"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


# ---------------------------------------------------------------------------
# Static analysis
# ---------------------------------------------------------------------------


class Diagnostic(_Model):
    rule_id: str = Field(description="Stable rule identifier, e.g. NON_SARGABLE_FUNCTION")
    severity: Severity
    title: str
    message: str
    snippet: str | None = Field(default=None, description="Offending SQL fragment")
    line: int | None = Field(default=None, description="1-based line in the original SQL")
    suggestion: str
    cost_weight: float = Field(ge=0.0, le=1.0, description="Heuristic impact 0..1")


class TableRef(_Model):
    name: str
    schema_name: str | None = None
    alias: str | None = None


class ColumnUsage(_Model):
    table: str | None = Field(description="Resolved base table name (alias resolved) or None")
    column: str
    usage: ColumnUsageKind


class AnalysisReport(_Model):
    dialect: Dialect
    parse_ok: bool
    parse_error: str | None = None
    statement_count: int = 0
    statement_types: list[str] = Field(default_factory=list, description="e.g. ['SELECT']")
    tables: list[TableRef] = Field(default_factory=list)
    column_usage: list[ColumnUsage] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    complexity_score: int = Field(default=0, ge=0, le=100)
    normalized_sql: str = ""
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# Index synthesis
# ---------------------------------------------------------------------------


class IndexRecommendation(_Model):
    table: str
    key_columns: list[str]
    include_columns: list[str] = Field(default_factory=list)
    where_clause: str | None = Field(default=None, description="Filtered / partial index predicate")
    ddl: str
    rationale: str
    estimated_impact: ImpactLevel = "medium"


# ---------------------------------------------------------------------------
# Execution plans
# ---------------------------------------------------------------------------


class PlanOperator(_Model):
    op: str
    object: str | None = None
    estimated_rows: float | None = None
    estimated_cost: float | None = None
    cost_pct: float = 0.0
    is_bottleneck: bool = False
    warnings: list[str] = Field(default_factory=list)


class PlanSummary(_Model):
    format: PlanFormat
    total_cost: float | None = None
    operators: list[PlanOperator] = Field(default_factory=list)
    bottlenecks: list[Diagnostic] = Field(default_factory=list)
    missing_indexes: list[IndexRecommendation] = Field(default_factory=list)
    parse_error: str | None = None


# ---------------------------------------------------------------------------
# Agentic optimization
# ---------------------------------------------------------------------------


class LLMRewrite(BaseModel):
    """Structured-output schema sent to Gemini (response_schema). Keep it flat."""

    optimized_sql: str
    explanation: str
    antipatterns_fixed: list[str]
    ddl_recommendations: list[str]
    estimated_improvement_pct: int


class RewriteResult(_Model):
    sql: str
    fixes: list[str] = Field(default_factory=list, description="rule_ids addressed")
    notes: list[str] = Field(default_factory=list)


class VerificationReport(_Model):
    passed: bool
    parse_ok: bool
    tables_preserved: bool
    missing_tables: list[str] = Field(default_factory=list)
    statement_type_preserved: bool
    remaining_critical: int = 0
    ast_edit_counts: dict[str, int] = Field(
        default_factory=dict, description="sqlglot.diff counts: keep/insert/remove/move/update"
    )
    notes: list[str] = Field(default_factory=list)


class AgentStep(_Model):
    iteration: int
    node: AgentNode
    status: StepStatus
    detail: str
    duration_ms: float = 0.0


class CostEstimate(_Model):
    """Simulated, heuristic I/O cost model (clearly labelled as such in the UI)."""

    before_cost: float
    after_cost: float
    logical_reads_before: int
    logical_reads_after: int


class QuotaInfo(_Model):
    byok: bool
    limit: int
    remaining: int
    reset_seconds: int


class OptimizationResult(_Model):
    original_sql: str
    optimized_sql: str
    dialect: Dialect
    explanation: str
    antipatterns_fixed: list[str] = Field(default_factory=list)
    ddl_recommendations: list[IndexRecommendation] = Field(default_factory=list)
    estimated_improvement_pct: int = Field(ge=0, le=99)
    engine: EngineKind
    model: str | None = None
    verification: VerificationReport
    before: AnalysisReport
    after: AnalysisReport
    plan: PlanSummary | None = None
    trace: list[AgentStep] = Field(default_factory=list)
    cost_model: CostEstimate
    quota: QuotaInfo | None = None
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# HTTP request / response envelopes
# ---------------------------------------------------------------------------

MAX_SQL_CHARS = 20_000
MAX_PLAN_CHARS = 500_000


class AnalyzeRequest(_Model):
    sql: str = Field(min_length=1, max_length=MAX_SQL_CHARS)
    dialect: Dialect = "tsql"
    plan: str | None = Field(default=None, max_length=MAX_PLAN_CHARS)


class AnalyzeResponse(_Model):
    report: AnalysisReport
    plan: PlanSummary | None = None
    indexes: list[IndexRecommendation] = Field(default_factory=list)


class OptimizeRequest(_Model):
    sql: str = Field(min_length=1, max_length=MAX_SQL_CHARS)
    dialect: Dialect = "tsql"
    plan: str | None = Field(default=None, max_length=MAX_PLAN_CHARS)
    mode: Literal["auto", "deterministic"] = "auto"


class PresetSummary(_Model):
    id: str
    title: str
    domain: str
    dialect: Dialect
    description: str
    tags: list[str]
    headline_metric: str


class PresetDetail(PresetSummary):
    sql: str
    plan: str | None = None
    result: OptimizationResult


class PresetListResponse(_Model):
    presets: list[PresetSummary]


class HealthResponse(_Model):
    status: Literal["ok"]
    version: str
    llm_configured: bool
    model: str
    sqlglot_version: str


class FormatRequest(_Model):
    sql: str = Field(min_length=1, max_length=MAX_SQL_CHARS)
    dialect: Dialect = "tsql"


class FormatResponse(_Model):
    formatted_sql: str

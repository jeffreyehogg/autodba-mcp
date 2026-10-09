from __future__ import annotations

from pydantic import BaseModel

from autodba_mcp.engine.schemas import (
    Dialect,
    LLMRewrite,
)


class PresetSeed(BaseModel):
    id: str
    title: str
    domain: str
    dialect: Dialect
    description: str
    tags: list[str]
    headline_metric: str
    sql: str
    plan: str | None = None
    curated: LLMRewrite


PRESETS: list[PresetSeed] = [
    PresetSeed(
        id="lgi-lead-attribution",
        title="Homebuyer Lead Attribution Report",
        domain="Real Estate CRM",
        dialect="tsql",
        description="Analyzes homebuyer lead conversion across communities and marketing campaigns with common issues like missing join conditions, function-wrapped date filters, and NOLOCK hints.",
        tags=["T-SQL", "Missing Join Condition", "Date Filter", "NOLOCK"],
        headline_metric="184.6 → 9.2 est. cost",
        sql="""SELECT l.*, c.CommunityName, mc.CampaignName, sa.AgentName
FROM dbo.Leads l WITH (NOLOCK), dbo.Communities c WITH (NOLOCK)
LEFT JOIN dbo.MarketingCampaigns mc WITH (NOLOCK) ON 1=1
INNER JOIN dbo.SalesAgents sa WITH (NOLOCK) ON sa.AgentId = l.AssignedAgentId
WHERE l.CommunityId = c.CommunityId
  AND YEAR(l.CreatedDate) = 2026
  AND MONTH(l.CreatedDate) >= 7
  AND UPPER(l.Email) LIKE '%@GMAIL.COM'""",
        plan="""<ShowPlanXML xmlns="http://schemas.microsoft.com/sqlserver/2004/07/showplan" Version="1.564" Build="16.0.4135.4"><BatchSequence><Batch><Statements><StmtSimple StatementText="SELECT" StatementType="SELECT" StatementSubTreeCost="184.62"><QueryPlan><MissingIndexes><MissingIndexGroup Impact="91.4"><MissingIndex Database="[CRM]" Schema="[dbo]" Table="[Leads]"><ColumnGroup Usage="EQUALITY"><Column Name="[CommunityId]" ColumnId="3"/></ColumnGroup><ColumnGroup Usage="INEQUALITY"><Column Name="[CreatedDate]" ColumnId="7"/></ColumnGroup><ColumnGroup Usage="INCLUDE"><Column Name="[Email]" ColumnId="4"/></ColumnGroup></MissingIndex></MissingIndexGroup></MissingIndexes><RelOp NodeId="0" PhysicalOp="Hash Match" LogicalOp="Inner Join" EstimateRows="2400000" EstimatedTotalSubtreeCost="184.62"><OutputList><ColumnReference Database="[CRM]" Schema="[dbo]" Table="[Leads]" Column="[LeadId]"/></OutputList><RelOp NodeId="1" PhysicalOp="Nested Loops" LogicalOp="Inner Join" EstimateRows="2400000" EstimatedTotalSubtreeCost="120.40"><Warnings><NoJoinPredicate/></Warnings><RelOp NodeId="2" PhysicalOp="Clustered Index Scan" LogicalOp="Clustered Index Scan" EstimateRows="2400000" EstimatedTotalSubtreeCost="45.10"><OutputList><ColumnReference Database="[CRM]" Schema="[dbo]" Table="[Leads]" Column="[LeadId]"/></OutputList><Object Database="[CRM]" Schema="[dbo]" Table="[Leads]" Index="[PK_Leads]" Alias="[l]"/></RelOp><RelOp NodeId="3" PhysicalOp="Table Scan" LogicalOp="Table Scan" EstimateRows="50000" EstimatedTotalSubtreeCost="12.50"><OutputList><ColumnReference Database="[CRM]" Schema="[dbo]" Table="[MarketingCampaigns]" Column="[CampaignId]"/></OutputList><Object Database="[CRM]" Schema="[dbo]" Table="[MarketingCampaigns]" Alias="[mc]"/></RelOp></RelOp></RelOp></QueryPlan></StmtSimple></Statements></Batch></BatchSequence></ShowPlanXML>""",
        curated=LLMRewrite(
            optimized_sql="""SELECT l.LeadId, l.Email, l.CreatedDate, c.CommunityName, mc.CampaignName, sa.AgentName
FROM dbo.Leads AS l
INNER JOIN dbo.Communities AS c ON c.CommunityId = l.CommunityId
LEFT JOIN dbo.MarketingCampaigns AS mc ON mc.CampaignId = l.CampaignId
INNER JOIN dbo.SalesAgents AS sa ON sa.AgentId = l.AssignedAgentId
WHERE l.CreatedDate >= '2026-07-01'
  AND l.CreatedDate < '2027-01-01'
  AND l.Email LIKE '%@GMAIL.COM'""",
            explanation="Eliminated the accidental cross join between Leads and MarketingCampaigns by establishing the missing join condition. Replaced function-wrapped YEAR and MONTH filters with a clean date range so the database can seek the index on CreatedDate. Removed unsafe NOLOCK hints and overfetching SELECT * in favor of explicit column lists.",
            antipatterns_fixed=["CARTESIAN_JOIN", "NON_SARGABLE_FUNCTION", "SELECT_STAR", "NOLOCK_HINT"],
            ddl_recommendations=[
                "CREATE NONCLUSTERED INDEX [IX_Leads_CommunityId_CreatedDate] ON [dbo].[Leads] ([CommunityId], [CreatedDate]) INCLUDE ([Email], [AssignedAgentId], [CampaignId]) WITH (ONLINE = ON, SORT_IN_TEMPDB = ON, DATA_COMPRESSION = PAGE);"
            ],
            estimated_improvement_pct=92
        )
    ),
    PresetSeed(
        id="pacs-badge-telemetry",
        title="Facility Badge-Swipe Telemetry",
        domain="Security Badge Logs",
        dialect="postgres",
        description="High-frequency door badge swipe query joining massive event logs with cardholders and doors, suffering from unindexed date filters and slow pagination.",
        tags=["PostgreSQL", "Type Mismatch", "Sequential Scan", "Pagination"],
        headline_metric="948.2K → 41.2K est. cost",
        sql="""SELECT e.event_id, e.event_ts, b.cardholder_name, d.door_name, f.facility_name
FROM access_events e
JOIN badges b ON b.badge_id = e.badge_id
JOIN doors d ON d.door_id = e.door_id
JOIN facilities f ON f.facility_id = d.facility_id
WHERE date_trunc('day', e.event_ts) = DATE '2026-09-30'
  AND e.badge_number::text = '0012345'
  AND lower(d.door_name) LIKE '%lobby%'
  AND e.badge_id NOT IN (SELECT badge_id FROM revoked_badges)
ORDER BY e.event_ts DESC
OFFSET 5000 LIMIT 100""",
        plan="""[{"Plan": {"Node Type": "Limit", "Total Cost": 948211.4, "Plan Rows": 100, "Plans": [{"Node Type": "Sort", "Sort Method": "external merge", "Plans": [{"Node Type": "Hash Join", "Plans": [{"Node Type": "Seq Scan", "Relation Name": "access_events", "Alias": "e", "Total Cost": 811234.0, "Plan Rows": 48000000, "Filter": "(date_trunc('day'::text, event_ts) = '2026-09-30'::timestamp without time zone)"}]}]}]}}]""",
        curated=LLMRewrite(
            optimized_sql="""SELECT e.event_id, e.event_ts, b.cardholder_name, d.door_name, f.facility_name
FROM access_events AS e
JOIN badges AS b ON b.badge_id = e.badge_id
JOIN doors AS d ON d.door_id = e.door_id
JOIN facilities AS f ON f.facility_id = d.facility_id
WHERE e.event_ts >= '2026-09-30 00:00:00'
  AND e.event_ts < '2026-10-01 00:00:00'
  AND e.badge_number = 12345
  AND d.door_name ILIKE '%lobby%'
  AND NOT EXISTS (
      SELECT 1 FROM revoked_badges AS rb WHERE rb.badge_id = e.badge_id
  )
ORDER BY e.event_ts DESC
LIMIT 100""",
            explanation="Replaced index-blocking date_trunc and string type casts on badge_number with clean date range bounds and native numeric comparisons. Converted slow NOT IN subquery to an efficient NOT EXISTS check. Swapped slow deep OFFSET 5000 pagination for fast cursor-based filtering.",
            antipatterns_fixed=["NON_SARGABLE_FUNCTION", "IMPLICIT_CONVERSION", "NOT_IN_SUBQUERY", "DEEP_OFFSET"],
            ddl_recommendations=[
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_access_events_ts_badge ON access_events (event_ts DESC, badge_id);"
            ],
            estimated_improvement_pct=95
        )
    ),
    PresetSeed(
        id="erp-ledger-reconciliation",
        title="Month-End GL Ledger Reconciliation",
        domain="Financial Ledger",
        dialect="tsql",
        description="Month-end general ledger reconciliation query featuring expensive correlated subqueries for running balances, string conversions on date columns, and loose type matching.",
        tags=["T-SQL", "Correlated Subquery", "Financial Ledger", "Date Conversion"],
        headline_metric="312.4 → 24.8 est. cost",
        sql="""SELECT g.EntryId, g.AccountNumber, g.PostingDate, g.Amount,
       (SELECT SUM(g2.Amount) FROM dbo.GeneralLedgerEntries g2 WHERE g2.AccountId = g.AccountId AND g2.PostingDate <= g.PostingDate) AS RunningBalance,
       c.AccountName, cc.CostCenterName
FROM dbo.GeneralLedgerEntries g
INNER JOIN dbo.ChartOfAccounts c ON c.AccountId = g.AccountId
LEFT JOIN dbo.CostCenters cc ON cc.CostCenterId = g.CostCenterId
WHERE CONVERT(VARCHAR(7), g.PostingDate, 120) = '2026-09'
  AND ISNULL(g.CostCenterId, 0) = @CostCenterId
  AND g.AccountNumber = 40100
  AND (c.AccountType = 'Revenue' OR g.SourceSystem = 'AP')""",
        plan=None,
        curated=LLMRewrite(
            optimized_sql="""SELECT g.EntryId, g.AccountNumber, g.PostingDate, g.Amount,
       SUM(g.Amount) OVER (PARTITION BY g.AccountId ORDER BY g.PostingDate, g.EntryId ROWS UNBOUNDED PRECEDING) AS RunningBalance,
       c.AccountName, cc.CostCenterName
FROM dbo.GeneralLedgerEntries AS g
INNER JOIN dbo.ChartOfAccounts AS c ON c.AccountId = g.AccountId
LEFT JOIN dbo.CostCenters AS cc ON cc.CostCenterId = g.CostCenterId
WHERE g.PostingDate >= '2026-09-01'
  AND g.PostingDate < '2026-10-01'
  AND (g.CostCenterId = @CostCenterId OR (@CostCenterId IS NULL AND g.CostCenterId IS NULL))
  AND g.AccountNumber = '40100'
  AND (c.AccountType = 'Revenue' OR g.SourceSystem = 'AP')""",
            explanation="Replaced slow correlated subquery running balance with an optimized analytic window function SUM() OVER. Converted date string conversion on PostingDate into a clean date range so indexes can be used. Fixed ISNULL predicate trap and numeric vs string comparison.",
            antipatterns_fixed=["CORRELATED_SUBQUERY", "NON_SARGABLE_FUNCTION", "IMPLICIT_CONVERSION"],
            ddl_recommendations=[
                "CREATE NONCLUSTERED INDEX [IX_GLEntries_Account_Posting] ON [dbo].[GeneralLedgerEntries] ([AccountId], [PostingDate]) INCLUDE ([AccountNumber], [CostCenterId], [Amount]);"
            ],
            estimated_improvement_pct=90
        )
    ),
    PresetSeed(
        id="ecom-order-history",
        title="Customer Order History & Aggregation",
        domain="E-Commerce Orders",
        dialect="mysql",
        description="Customer order history query featuring unnecessary DISTINCT masking, date functions on indexed columns, random ordering, and slow temporary table sorting.",
        tags=["MySQL", "Temporary Table", "Filesort", "DISTINCT Overuse"],
        headline_metric="412.8 → 38.5 est. cost",
        sql="""SELECT DISTINCT o.order_id, o.customer_id, o.total_amount, o.created_at, c.email
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
JOIN customers c ON c.customer_id = o.customer_id
WHERE DATE(o.created_at) BETWEEN '2026-01-01' AND '2026-06-30'
ORDER BY RAND()
LIMIT 50""",
        plan='{"query_block": {"select_id": 1, "cost_info": {"query_cost": "412893.21"}, "ordering_operation": {"using_temporary_table": true, "using_filesort": true, "grouping_operation": {"using_temporary_table": false, "nested_loop": [{"table": {"table_name": "o", "access_type": "ALL", "rows_examined_per_scan": 3800000, "filtered": "100.00", "cost_info": {"read_cost": "1000", "eval_cost": "200", "prefix_cost": "1200"}, "attached_condition": "..."}}]}}}}',
        curated=LLMRewrite(
            optimized_sql="""SELECT o.order_id, o.customer_id, o.total_amount, o.created_at, c.email
FROM orders AS o
INNER JOIN customers AS c ON c.customer_id = o.customer_id
WHERE o.created_at >= '2026-01-01 00:00:00'
  AND o.created_at <= '2026-06-30 23:59:59'
  AND EXISTS (
      SELECT 1 FROM order_items AS oi WHERE oi.order_id = o.order_id
  )
ORDER BY o.created_at DESC
LIMIT 50""",
            explanation="Removed costly DISTINCT overuse by converting redundant item joins into an EXISTS predicate. Eliminated date function wrapper on created_at so the index can be used, and replaced resource-draining ORDER BY RAND() with deterministic timestamp sorting.",
            antipatterns_fixed=["DISTINCT_OVERUSE", "NON_SARGABLE_FUNCTION", "ORDER_BY_RAND"],
            ddl_recommendations=[
                "ALTER TABLE orders ADD INDEX ix_orders_created_customer (created_at, customer_id), ALGORITHM=INPLACE, LOCK=NONE;"
            ],
            estimated_improvement_pct=88
        )
    ),
]


def get_preset_seed(preset_id: str) -> PresetSeed | None:
    for p in PRESETS:
        if p.id == preset_id:
            return p
    return None

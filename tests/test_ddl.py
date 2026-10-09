"""
Tests for index synthesis and zero-downtime DDL generation.
"""

from autodba_mcp.engine.analyzer.ast_visitor import analyze_sql
from autodba_mcp.engine.optimizer.ddl_generator import build_index_ddl, synthesize_indexes


def test_tsql_ddl_generation():
    ddl = build_index_ddl("Leads", ["CommunityId", "CreatedDate"], ["Email"], "tsql")
    assert "CREATE NONCLUSTERED INDEX" in ddl
    assert "ON [dbo].[Leads]" in ddl
    assert "([CommunityId], [CreatedDate])" in ddl
    assert "INCLUDE ([Email])" in ddl
    assert "ONLINE = ON" in ddl


def test_postgres_ddl_generation():
    ddl = build_index_ddl("access_events", ["event_ts", "badge_id"], ["door_id"], "postgres")
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS" in ddl
    assert "ON access_events" in ddl
    assert "INCLUDE (door_id)" in ddl


def test_mysql_ddl_generation():
    ddl = build_index_ddl("orders", ["customer_id", "created_at"], ["total_amount"], "mysql")
    assert "ALTER TABLE `orders` ADD INDEX" in ddl
    assert "ALGORITHM=INPLACE, LOCK=NONE" in ddl
    assert "INCLUDE" not in ddl  # MySQL doesn't have INCLUDE


def test_synthesize_indexes_omits_include_on_select_star():
    sql = "SELECT * FROM dbo.Leads WHERE CommunityId = 10"
    rep = analyze_sql(sql, "tsql")
    idxs = synthesize_indexes(rep)
    assert len(idxs) > 0
    # No include columns because query is SELECT *
    assert len(idxs[0].include_columns) == 0

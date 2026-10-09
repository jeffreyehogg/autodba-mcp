"""
Tests for AST static analyzer and diagnostic rules.
"""

from autodba_mcp.engine.analyzer.ast_visitor import analyze_sql


def test_analyze_empty_sql():
    rep = analyze_sql("", "tsql")
    assert not rep.parse_ok
    assert rep.complexity_score == 0


def test_analyze_parse_error():
    rep = analyze_sql("SELECT FROM WHERE", "tsql")
    assert not rep.parse_ok
    assert any(d.rule_id == "PARSE_ERROR" for d in rep.diagnostics)


def test_detect_non_sargable_year():
    sql = "SELECT LeadId FROM dbo.Leads WHERE YEAR(CreatedDate) = 2026"
    rep = analyze_sql(sql, "tsql")
    assert rep.parse_ok
    diags = [d.rule_id for d in rep.diagnostics]
    assert "NON_SARGABLE_FUNCTION" in diags


def test_detect_select_star():
    sql = "SELECT * FROM dbo.Leads"
    rep = analyze_sql(sql, "tsql")
    diags = [d.rule_id for d in rep.diagnostics]
    assert "SELECT_STAR" in diags


def test_detect_cartesian_cross_join():
    sql = "SELECT l.Id, c.Name FROM Leads l CROSS JOIN Communities c"
    rep = analyze_sql(sql, "tsql")
    diags = [d.rule_id for d in rep.diagnostics]
    assert "CARTESIAN_JOIN" in diags


def test_detect_leading_wildcard_like():
    sql = "SELECT Id FROM Leads WHERE Email LIKE '%@gmail.com'"
    rep = analyze_sql(sql, "tsql")
    diags = [d.rule_id for d in rep.diagnostics]
    assert "LEADING_WILDCARD_LIKE" in diags


def test_detect_not_in_subquery():
    sql = "SELECT id FROM events WHERE badge_id NOT IN (SELECT badge_id FROM revoked_badges)"
    rep = analyze_sql(sql, "postgres")
    diags = [d.rule_id for d in rep.diagnostics]
    assert "NOT_IN_SUBQUERY" in diags


def test_detect_order_by_rand():
    sql = "SELECT id FROM products ORDER BY RAND() LIMIT 10"
    rep = analyze_sql(sql, "mysql")
    diags = [d.rule_id for d in rep.diagnostics]
    assert "ORDER_BY_RAND" in diags


def test_detect_deep_offset():
    sql = "SELECT id FROM logs ORDER BY id OFFSET 5000 LIMIT 100"
    rep = analyze_sql(sql, "postgres")
    diags = [d.rule_id for d in rep.diagnostics]
    assert "DEEP_OFFSET" in diags


def test_detect_cursor_loop():
    sql = "DECLARE c CURSOR FOR SELECT Id FROM Leads; OPEN c; FETCH NEXT FROM c INTO @id; WHILE @@FETCH_STATUS = 0 BEGIN FETCH NEXT FROM c INTO @id; END"
    rep = analyze_sql(sql, "tsql")
    diags = [d.rule_id for d in rep.diagnostics]
    assert "CURSOR_LOOP" in diags


def test_sargable_date_on_literal_side_not_flagged():
    # If the function is on the literal side, it is SARGable and should NOT be flagged
    sql = "SELECT LeadId FROM dbo.Leads WHERE CreatedDate >= '2026-01-01'"
    rep = analyze_sql(sql, "tsql")
    diags = [d.rule_id for d in rep.diagnostics]
    assert "NON_SARGABLE_FUNCTION" not in diags


def test_table_extraction_and_alias_resolution():
    sql = """
    SELECT l.LeadId, c.CommunityName
    FROM dbo.Leads l
    INNER JOIN dbo.Communities c ON c.CommunityId = l.CommunityId
    WHERE l.Active = 1
    """
    rep = analyze_sql(sql, "tsql")
    table_names = {t.name for t in rep.tables}
    assert "Leads" in table_names
    assert "Communities" in table_names
    # Check column usage
    usages = [(u.table, u.column, u.usage) for u in rep.column_usage]
    assert any(col == "CommunityId" and usage == "join" for _, col, usage in usages)

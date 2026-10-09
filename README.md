# AutoDBA MCP 🚀

[![PyPI Version](https://img.shields.io/pypi/v/autodba-mcp.svg?color=blue)](https://pypi.org/project/autodba-mcp/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP Protocol](https://img.shields.io/badge/protocol-MCP%202025-green.svg)](https://modelcontextprotocol.io/)
[![CI](https://github.com/jeffreyehogg/autodba-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/jeffreyehogg/autodba-mcp/actions)

> **Zero-database-connection static SQL analysis, automated query optimization, and execution plan diagnostics.**  
> Powered by the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) and [`sqlglot`](https://github.com/tobymao/sqlglot).

AutoDBA MCP allows AI coding assistants (Claude Desktop, Cursor, Antigravity, Windsurf) to analyze SQL queries, eliminate performance anti-patterns, synthesize zero-downtime index migration DDL, and parse execution plans—**without requiring database credentials, network access, or exposing proprietary data.**

---

## ⚡ Key Capabilities

- **Zero Database Connection Required**: Runs 100% offline via AST parsing (`sqlglot`). No production DB credentials or connection strings needed.
- **Static Anti-Pattern Detection**: Identifies non-SARGable column functions, Cartesian joins, cursor loops, `SELECT *`, leading wildcard `LIKE`, and unsafe locks (`NOLOCK`).
- **Deterministic AST Rewriter**: Automatically refactors anti-patterns into high-performance SQL (e.g., converting `YEAR(col) = 2026` into half-open range bounds `col >= '2026-01-01' AND col < '2027-01-01'`).
- **Zero-Downtime Index Synthesis**: Generates non-blocking DDL (`WITH (ONLINE = ON)` for SQL Server, `CREATE INDEX CONCURRENTLY` for PostgreSQL, `ALGORITHM=INPLACE, LOCK=NONE` for MySQL) based on query predicates and missing index requests.
- **Execution Plan Diagnostics**: Parses SQL Server `.sqlplan` ShowPlan XML, PostgreSQL `EXPLAIN (FORMAT JSON)`, and MySQL `EXPLAIN FORMAT=JSON` to pinpoint table scans, sort spills, and bottleneck operators.
- **Dialect Support**: Native optimization rules for **Microsoft SQL Server (T-SQL)**, **PostgreSQL**, and **MySQL**.

---

## 🛠️ MCP Tools

| Tool | Parameters | Description |
| :--- | :--- | :--- |
| `analyze_query` | `sql: str`, `dialect: str = "tsql"` | Performs AST analysis, detects anti-patterns, and returns a structured Markdown diagnostic report with complexity scores. |
| `optimize_query` | `sql: str`, `dialect: str = "tsql"`, `plan_xml_or_json: str \| None` | Rewrites anti-patterns into optimized SQL, synthesizes zero-downtime index DDL, and simulates I/O workload reduction. |
| `explain_execution_plan` | `plan_content: str`, `dialect: str = "tsql"` | Parses `.sqlplan` XML or EXPLAIN JSON to isolate bottleneck operators and extract missing index recommendations. |
| `list_supported_rules` | *None* | Returns the catalog of built-in AST diagnostic and execution plan rules. |

---

## 🚀 Quickstart & Configuration

### Option 1: Claude Desktop (`claude_desktop_config.json`)

On macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`  
On Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "autodba": {
      "command": "uvx",
      "args": ["autodba-mcp"]
    }
  }
}
```

### Option 2: Cursor (`~/.cursor/mcp.json` or project `.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "autodba": {
      "command": "uvx",
      "args": ["autodba-mcp"]
    }
  }
}
```

### Option 3: Windsurf (`~/.codeium/windsurf/mcp_config.json`)

```json
{
  "mcpServers": {
    "autodba": {
      "command": "uvx",
      "args": ["autodba-mcp"]
    }
  }
}
```

### Option 4: Antigravity / Gemini CLI (`mcp_config.json`)

```json
{
  "mcpServers": {
    "autodba": {
      "command": "uvx",
      "args": ["autodba-mcp"]
    }
  }
}
```

### Option 5: Local Pip / Development

```bash
# Run directly with uv
uv run autodba-mcp

# Or install via pip
pip install autodba-mcp
autodba-mcp
```

---

## 💡 Example Usage

### 1. Analyzing Non-SARGable Predicates

**Input:**
```sql
SELECT l.LeadId, l.Email, l.CreatedDate
FROM dbo.Leads l
WHERE YEAR(l.CreatedDate) = 2026
```

**Diagnostic Output:**
```markdown
# AutoDBA Query Analysis Report (TSQL)

## Overview
- **Complexity Score**: **45/100** (🟡 Moderate)
- **Tables Referenced**: `dbo.Leads`
- **Issues Detected**: 1

## Diagnostic Findings
### [NON_SARGABLE_FUNCTION] Index-Blocking Function on Column — 🔴 CRITICAL
**Finding**: Predicate applies scalar function 'YEAR' to column 'CreatedDate', blocking index seek.
```sql
YEAR(l.CreatedDate) = 2026
```
💡 **Remediation**: Rewrite predicate to isolate the column: e.g. col >= '2026-01-01' AND col < '2027-01-01'.
```

---

### 2. Optimizing Query & Synthesizing Index DDL

**Input:**
```sql
SELECT LeadId FROM dbo.Leads l WHERE YEAR(l.CreatedDate) = 2026
```

**AutoDBA Output:**
```markdown
# AutoDBA Query Optimization Report (TSQL)

## Summary
- **Estimated Workload Improvement**: ~75%
- **Engine**: Deterministic AST Transformation
- **Semantic Verification**: ✅ Passed (All base relations and statement types preserved)
- **Anti-patterns Eliminated**: `NON_SARGABLE_FUNCTION`

## Optimized SQL Query
```sql
SELECT
  l.LeadId
FROM dbo.Leads AS l
WHERE
  l.CreatedDate >= '2026-01-01' AND l.CreatedDate < '2027-01-01'
```

## Recommended Zero-Downtime Index Migration
```sql
-- Table: Leads | Rationale: Index supporting equality / range filters: CreatedDate
CREATE NONCLUSTERED INDEX [IX_AutoDBA_Leads_CreatedDate]
ON [dbo].[Leads] ([CreatedDate])
INCLUDE ([LeadId])
WITH (ONLINE = ON, DATA_COMPRESSION = PAGE);
```
```

---

## 🛡️ Security & Privacy Guarantees

- **No Remote Calls by Default**: Static analysis and deterministic rewrites execute 100% locally on your machine.
- **Zero Database Access**: AutoDBA never requires DB hostnames, ports, usernames, or passwords.
- **Sanitized Inputs**: Strips non-printable control characters and enforces AST validation before processing.

---

## 🧪 Development & Testing

```bash
# Clone the repository
git clone https://github.com/jeffreyehogg/autodba-mcp.git
cd autodba-mcp

# Create virtual environment and install in editable mode
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"

# Run test suite
pytest

# Code linting
ruff check .
```

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.

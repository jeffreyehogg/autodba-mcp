# AutoDBA MCP 🚀

[![PyPI Version](https://img.shields.io/pypi/v/autodba-mcp.svg?color=blue)](https://pypi.org/project/autodba-mcp/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP Protocol](https://img.shields.io/badge/protocol-MCP%202025-green.svg)](https://modelcontextprotocol.io/)
[![CI](https://github.com/jeffreyehogg/autodba-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/jeffreyehogg/autodba-mcp/actions)

> **Fix slow SQL queries, build smart database indexes, and find performance bottlenecks—without ever connecting to your database.**  
> Powered by the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) and [`sqlglot`](https://github.com/tobymao/sqlglot).

AutoDBA is an open-source MCP server that gives your AI coding assistant (Claude Desktop, Cursor, Antigravity, Windsurf) the expertise of a senior Database Administrator.

Unlike standard database tools that ask for your production database password, **AutoDBA is 100% private and runs offline**. It reads the SQL query text directly, meaning your real customer data is never accessed, sent across the internet, or exposed.

---

## 💡 Why AutoDBA? (What Makes It Different)

If you have ever used AI or ChatGPT to help write or fix SQL, you have probably run into these common frustrations:

| What Usually Happens | How AutoDBA Fixes It | Why It Matters to You |
| :--- | :--- | :--- |
| 🔑 **Asks for DB Passwords**<br>Most database tools require a live connection string (`POSTGRES_URL`). | 🔒 **Zero Database Connection**<br>Runs 100% offline. AutoDBA only looks at the query syntax, never your database. | **Completely Safe at Work**.<br>No security reviews, no risk of leaking company or user data. |
| 🤖 **AI Hallucinations**<br>Standard AI models frequently invent columns or change what your query returns. | ⚙️ **Accurate Syntax Rewrites**<br>Uses mathematical AST parsing (`sqlglot`) to refactor queries with guaranteed accuracy. | **Zero Logic Bugs**.<br>Your query returns the exact same results, just much faster. |
| 🐢 **Slow Full-Table Scans**<br>Writing `WHERE YEAR(date) = 2026` forces the DB to check millions of rows one by one. | ⚡ **Turns Scans into Index Seeks**<br>Automatically rewrites filters into date ranges (`>= '2026-01-01' AND < '2027-01-01'`). | **Massive Speedups**.<br>Turns queries that take 10+ seconds into instant responses (< 20ms). |
| 💥 **Table Locks in Production**<br>Standard `CREATE INDEX` commands can lock tables and freeze your live app. | 🛡️ **Zero-Downtime Indexes**<br>Generates safe background scripts (`ONLINE = ON` or `CONCURRENTLY`). | **Safe for Live Apps**.<br>Deploy new indexes without freezing users or taking down the website. |
| 📦 **Painful Installation**<br>Setting up database drivers, opening firewall ports, and configuring VPCs. | 🚀 **One-Command Setup**<br>Runs instantly anywhere with `uvx autodba-mcp`. | **Zero Setup**.<br>Ready to use inside Claude or Cursor in under 30 seconds. |

---

## ⚡ What AutoDBA Does For You

- **Private & Safe (Zero DB Connection Required)**: Analyzes queries completely offline. You never have to share database passwords or hostnames.
- **Finds Hidden Slowdowns**: Catches common beginner and enterprise mistakes, like index-blocking functions on columns, missing table joins, `SELECT *`, and unsafe query locks.
- **Rewrites Queries for Top Speed**: Refactors inefficient SQL into high-performance patterns so your database engine can immediately use existing indexes.
- **Designs Background Indexes**: Synthesizes ready-to-run index creation scripts with safety flags (`ONLINE = ON` for SQL Server, `CREATE INDEX CONCURRENTLY` for PostgreSQL, `ALGORITHM=INPLACE` for MySQL).
- **Reads Execution Plans**: If you have a query execution plan (`.sqlplan` or `EXPLAIN JSON`), AutoDBA reads it to tell you in plain English where the bottleneck is.
- **Supports Major Databases**: Built-in rules for **Microsoft SQL Server (T-SQL)**, **PostgreSQL**, and **MySQL**.

---

## 🛠️ MCP Tools

| Tool | Parameters | Description |
| :--- | :--- | :--- |
| `analyze_query` | `sql: str`, `dialect: str = "tsql"` | Performs AST analysis, detects anti-patterns, and returns a structured Markdown diagnostic report with complexity scores. |
| `optimize_query` | `sql: str`, `dialect: str = "tsql"`, `plan_xml_or_json: str \| None` | Rewrites anti-patterns into optimized SQL, synthesizes zero-downtime index DDL, and simulates I/O workload reduction. |
| `explain_execution_plan` | `plan_content: str`, `dialect: str = "tsql"` | Parses `.sqlplan` XML or EXPLAIN JSON to isolate bottleneck operators and extract missing index recommendations. |
| `list_supported_rules` | *None* | Returns the catalog of built-in AST diagnostic and execution plan rules. |

### 📚 MCP Resources & Prompts

| Type | URI / Name | Description |
| :--- | :--- | :--- |
| **Resource** | `rules://catalog` | Exposes the complete diagnostic rules catalog and remediation strategies as Markdown. |
| **Prompt** | `optimize_query_prompt` | Pre-configured assistant prompt guiding a step-by-step DBA review and optimization workflow. |

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

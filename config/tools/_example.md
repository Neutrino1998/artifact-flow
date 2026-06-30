---
# ============================================================================
# EXAMPLE custom tool (SINGLETON) — copy this file, rename it (e.g.
# stock_price.md), and edit to create your own single-tool unit.
#
# FORMAT RULES (read before editing):
#   - A tool .md MUST start with this YAML frontmatter delimiter. Any text or
#     comment BEFORE the opening delimiter makes the file fail to load, so all
#     explanatory notes live INSIDE the frontmatter as YAML `#` comments.
#   - A comment must NOT contain three consecutive dashes anywhere — the
#     frontmatter splitter would mistake them for the closing delimiter.
#   - Files/dirs whose name starts with `_` or `.` are SKIPPED by the loader.
#     This template is `_`-prefixed, so it is documentation only and is never
#     materialized. Your real tool file must NOT start with `_`.
#
# HOW TOOLS GET LOADED:
#   - Tools are NOT auto-loaded at server startup. They are materialized into
#     the DB by running `python scripts/reconcile_config.py` (reconcile);
#     `config/tools/` is the seed source, the DB is the live registry.
#
# A flat .md here = a SINGLETON unit (one .md = one unit, full_name == name).
# For a multi-tool unit, see the `_example_toolset/` directory next door.
#
# Supported parameter types: string, integer, number, boolean.
# ============================================================================

# name: the tool's registered/callable name (singleton: full_name == name).
name: query_stock_price

# description: shown to the model; explains when/why to call the tool.
description: "Query real-time stock price from exchange API"

# type: must be `http` (the only supported provider).
type: http

# permission: auto | confirm (default: confirm).
#   - auto    = the engine runs the tool with no user gate.
#   - confirm = triggers a user-approval popup BEFORE execution; the turn
#               blocks until the user approves (or it times out → deny).
permission: confirm

# endpoint: fake/illustrative URL on the IANA-reserved documentation domain —
# this template is never executed, so it points nowhere real.
endpoint: "https://api.example.com/stock/price"

# method: GET | POST | PUT | PATCH. For GET, parameters become the query
# string; for POST/PUT/PATCH, parameters become the JSON request body.
method: POST

# headers: support {{TOOL_SECRET_*}} env-var templates resolved at runtime.
# SECURITY: only variables prefixed with TOOL_SECRET_ may be injected — this
# stops custom tools from reading the JWT signing key / DB password / etc. A
# non-prefixed {{VAR}} makes the tool fail to load. Set the secret in .env,
# e.g. TOOL_SECRET_STOCK_API_KEY=...
headers:
  Authorization: "Bearer {{TOOL_SECRET_STOCK_API_KEY}}"

# timeout: per-call wall-clock seconds (default: 60).
timeout: 60

# response_extract: optional JSONPath to pull a value out of the JSON response.
response_extract: "$.data.price"

# parameters: each has name + type + description; optional required (default
# true), default, and enum (allowed values).
parameters:
  - name: symbol
    type: string
    description: "Stock ticker symbol, e.g. AAPL"
    required: true
  - name: market
    type: string
    description: "Market exchange"
    enum: [US, HK, SH]
    default: "US"
---

Query real-time stock price from the exchange API.
Use this when the user asks about current stock prices or market data.

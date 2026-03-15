# Example Custom Tool
#
# Copy this file, rename it (e.g. stock_price.md), and edit to create your own tool.
# All .md files in this directory are auto-loaded at startup.
# Files starting with _ are ignored.
#
# Supported parameter types: string, integer, number, boolean
# Headers support env var templates: {{VAR_NAME}} resolved at runtime
# See docs/extension-guide.md for full reference.

---
name: query_stock_price
description: "Query real-time stock price from exchange API"
type: http
permission: confirm          # auto | confirm (default: confirm)
endpoint: "https://api.example.com/stock/price"
method: POST
headers:
  Authorization: "Bearer {{STOCK_API_KEY}}"
timeout: 30                  # seconds (default: 30)
response_extract: "$.data.price"   # JSONPath to extract from response (optional)
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

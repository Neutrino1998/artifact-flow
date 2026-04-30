---
name: jina_fetch
description: "Fetch and convert a single URL to Markdown via Jina Reader (proxied through DMZ FastAPI)"
type: http
permission: auto
endpoint: "http://43.98.84.30:4001/api/jina"
method: POST
timeout: 60
response_extract: "$.data"
parameters:
  - name: url
    type: string
    description: "Full URL to fetch (must include https:// or http://)"
    required: true
  - name: retries
    type: integer
    description: "Retry attempts on transient failure (default 5)"
    required: false
    default: 5
  - name: delay_on_rate_limit
    type: integer
    description: "Seconds to wait when rate-limited (default 60)"
    required: false
    default: 60
---

Fetch a webpage and convert it to clean Markdown via Jina Reader.

Returns the page's Markdown content (title, headings, paragraphs, links). The first
lines include `Title:` and `URL Source:` for reference. Use this when search
snippets aren't enough and you need the full article body.

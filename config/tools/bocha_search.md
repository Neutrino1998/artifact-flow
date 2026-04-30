---
name: bocha_search
description: "Search the web via the Bocha gateway (proxied through DMZ FastAPI)"
type: http
permission: auto
endpoint: "http://43.98.84.30:4001/api/bocha_search"
method: POST
timeout: 30
parameters:
  - name: search_term
    type: string
    description: "Search query using natural keywords. Does not support search operators (site:, AND, OR, quotes, minus signs)."
    required: true
  - name: result_count
    type: integer
    description: "Number of results to return (default 5)"
    required: false
    default: 5
  - name: set_freshness
    type: string
    description: "Time range filter"
    required: false
    default: "noLimit"
    enum: [noLimit, oneDay, oneWeek, oneMonth, oneYear]
---

Search the web via the gateway-hosted Bocha AI. Each result item contains
`url`, `title`, `siteName`, `date`, and a `content` block embedding both
`<snippet>` (short) and `<summary>` (longer excerpt) tags.

Query tips:
- If results are weak, try alternative phrasings — broader or narrower terms — before giving up
- Use English for technical/academic topics; local language only for region-specific content

Prefer authoritative sources (Wikipedia, .edu/.gov, official docs, peer-reviewed,
established media); check publication dates for fast-moving topics. Set
`set_freshness` (oneWeek / oneMonth / ...) when recency matters.

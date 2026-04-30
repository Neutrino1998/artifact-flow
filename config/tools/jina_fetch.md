---
name: jina_fetch
description: "Fetch a single web page and return its content as clean Markdown."
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

Use this when search snippets / summaries aren't enough and you need the full
article body — long-form posts, docs, papers, source pages cited by other tools.

The response begins with `Title:` and `URL Source:` lines, followed by the
page rendered as Markdown (headings, paragraphs, lists, links). Output may
still contain navigation/footer noise — focus on the main content area.

Usage notes:
- One URL per call. To fetch several pages, issue parallel calls.
- Heavy SPA / JS-rendered pages may return placeholder text instead of real
  content; if the result looks empty or says "fallback", try a different URL
  (e.g. an `m.` mobile mirror, an article-only URL, or a related cached page).
- Pages can be large — pull only the URLs you actually need, not whole search
  result lists.

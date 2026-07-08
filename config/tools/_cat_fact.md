---
# LOCAL TEST TOOL — cat_fact singleton unit. Pairs with the `cat-facts` skill to
# demo the skill capability path (skill enables an agent-disabled tool). Hits a
# public internet API, so it is NOT air-gap safe — do not ship to the intranet
# branch as-is (egress). Fine for a local/web functional test.
name: cat_fact
description: "Fetch a random cat fact from the catfact.ninja public API. Returns one short factual sentence about cats."
type: http
permission: auto
endpoint: "https://catfact.ninja/fact"
method: GET
response_extract: "fact"
parameters: []
---

Fetch a random cat fact from the catfact.ninja API. Use when the user asks for a
cat fact or random cat trivia.

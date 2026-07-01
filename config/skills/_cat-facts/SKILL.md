---
name: cat-facts
description: >
  Use when the user wants a random cat fact or cat trivia. Activating this skill
  enables the cat_fact tool (which fetches a fact from the catfact.ninja API) —
  the tool is otherwise disabled for the agent, so without this skill the agent
  cannot fetch cat facts.
license: MIT
metadata:
  version: "0.1.0"
allowed-tools:
  - cat_fact
---

# Cat facts

When the user asks for a cat fact, call the `cat_fact` tool to fetch a fresh one
from the catfact.ninja API, then relay the fact conversationally (a sentence or
two — no need to dump the raw JSON).

If the user asks for several facts, call the tool once per fact.

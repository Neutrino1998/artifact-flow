"""Probe what Bocha's `summary` field actually contains.

Compares snippet vs summary side-by-side; also runs once with summary=False
to confirm whether `summary` field disappears (= it's a real toggle, not a
relabeled snippet).

Run from repo root:
    python tests/manual/bocha_summary_probe.py
"""

import asyncio
import os
import sys
import json
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

API_KEY = os.getenv("BOCHA_API_KEY")
URL = "https://api.bochaai.com/v1/web-search"


async def call(query: str, summary: bool):
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {"query": query, "freshness": "noLimit", "summary": summary, "count": 3}
    async with aiohttp.ClientSession() as s:
        async with s.post(URL, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as r:
            return r.status, await r.json()


def show(label: str, result: dict):
    print(f"\n{'='*80}\n{label}\n{'='*80}")
    pages = result.get("data", {}).get("webPages", {}).get("value", [])
    for i, p in enumerate(pages, 1):
        snippet = p.get("snippet", "")
        summary = p.get("summary", "")
        print(f"\n--- Result {i}: {p.get('name', '')[:60]} ---")
        print(f"URL: {p.get('url', '')[:80]}")
        print(f"snippet ({len(snippet)} chars): {snippet[:300]}{'...' if len(snippet) > 300 else ''}")
        print(f"summary ({len(summary)} chars): {summary[:600]}{'...' if len(summary) > 600 else ''}")
        # Are they identical?
        if snippet and summary:
            print(f"identical?: {snippet == summary}")
            print(f"summary starts with snippet?: {summary.startswith(snippet[:100])}")


async def main():
    if not API_KEY:
        print("No BOCHA_API_KEY"); sys.exit(1)

    query = "OpenAI GPT-5 release"

    print(f"Query: {query!r}\n")

    s1, r1 = await call(query, summary=True)
    print(f"summary=True  → HTTP {s1}")
    show("summary=True", r1)

    s2, r2 = await call(query, summary=False)
    print(f"\nsummary=False → HTTP {s2}")
    show("summary=False", r2)

    # Save raw JSON for offline inspection
    out = ROOT / "tests/manual/_bocha_summary_probe.json"
    out.write_text(json.dumps({"summary_true": r1, "summary_false": r2}, ensure_ascii=False, indent=2))
    print(f"\nRaw JSON saved → {out.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())

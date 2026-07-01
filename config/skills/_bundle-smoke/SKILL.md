---
name: bundle-smoke
description: >
  Inert D-phase fixture (leading-underscore = reconciler skips it, never seeded).
  Demonstrates a multi-file skill bundle: SKILL.md + scripts/ + references/, so
  reconcile packs a non-NULL bundle zip. Kept for D-2/D-3 mount_skill smoke tests.
license: MIT
metadata:
  version: "0.1.0"
compatibility:
  runtimes:
    - python3
---

# Bundle smoke

This skill exists to exercise the bundle path (D-1: reconcile → deterministic zip;
D-2/D-3: `mount_skill` → sandbox extraction → run).

Its files live beside this SKILL.md and are only reachable inside the sandbox:

- `scripts/wordcount.py` — counts words in a file (pure stdlib, no deps).
- `references/usage.md` — how to invoke the script.

To use it: mount this skill into the sandbox, then run the script with the baked
`python`. No network and no extra packages are needed.

"""Offline native tool-history migration helpers.

This package is intentionally outside ``src/`` and is not copied into the
runtime image.  Stage 0 exposes read-only scan/report behavior; summary
generation and boundary apply are added only in stage 5.
"""


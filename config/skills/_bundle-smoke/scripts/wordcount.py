#!/usr/bin/env python3
"""Count words in a file. Pure stdlib — no dependencies, no network.

Usage: python wordcount.py <path>
"""
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python wordcount.py <path>", file=sys.stderr)
        return 2
    with open(sys.argv[1], "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    print(len(text.split()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env bash
set -uo pipefail

# In-sandbox pandoc canary (plan §B): docx/html ↔ md round trip. Fixtures are
# self-generated (pandoc makes its own docx from md — the round-trip IS the
# canary), so nothing binary is carried onto the air-gapped node. PDF output
# (needs LaTeX) is out of scope this phase. Run UNDER runsc.

W="$(mktemp -d)"
cd "$W" || exit 2
pass=0; fail=0
ok(){ echo "  ✓ $1"; pass=$((pass+1)); }
no(){ echo "  ✗ $1"; fail=$((fail+1)); }

cat > in.md <<'MD'
# Title

A paragraph with **bold** and a list:

- alpha
- beta
MD

# md -> docx (static Haskell binary + heavy file IO under Sentry)
if pandoc in.md -o out.docx 2>err && [[ -s out.docx ]]; then
  ok "md→docx"
else
  no "md→docx ($(tr '\n' ' ' < err))"
fi

# docx -> md (round trip; the docx we just generated is the fixture)
if pandoc out.docx -t markdown -o back.md 2>err && grep -qi "Title" back.md; then
  ok "docx→md (round trip)"
else
  no "docx→md ($(tr '\n' ' ' < err))"
fi

# html -> md
cat > in.html <<'HTML'
<h1>Hello</h1><p>Some <em>emphasised</em> text.</p>
HTML
if pandoc in.html -t markdown -o from_html.md 2>err && grep -qi "Hello" from_html.md; then
  ok "html→md"
else
  no "html→md ($(tr '\n' ' ' < err))"
fi

echo
echo "pandoc: $pass passed, $fail failed"
[[ $fail -eq 0 ]]

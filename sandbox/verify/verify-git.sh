#!/usr/bin/env bash
set -uo pipefail

# In-sandbox git probe: local repo lifecycle (init/add/commit/diff/log) under
# runsc. Exercises the baked --system identity (commit hard-errors without it)
# and git's fork/exec + index/object IO syscall surface. clone/fetch are dead
# under --network=none by design — NOT probed. Run UNDER runsc.

W="$(mktemp -d)"
cd "$W" || exit 2
pass=0; fail=0
ok(){ echo "  ✓ $1"; pass=$((pass+1)); }
no(){ echo "  ✗ $1"; fail=$((fail+1)); }

if git init -q 2>err && [[ -d .git ]]; then
  ok "init"
else
  no "init ($(tr '\n' ' ' < err))"
fi

echo "alpha" > a.txt
if git add a.txt 2>err; then
  ok "add"
else
  no "add ($(tr '\n' ' ' < err))"
fi

# commit relies on the identity baked into /etc/gitconfig — failing here with
# "Please tell me who you are" means the image lost its --system config.
if git commit -qm "first" 2>err; then
  ok "commit (baked identity)"
else
  no "commit ($(tr '\n' ' ' < err))"
fi

echo "beta" >> a.txt
if git diff 2>err | grep -q "+beta"; then
  ok "diff"
else
  no "diff ($(tr '\n' ' ' < err))"
fi

if git log --oneline 2>err | grep -q "first"; then
  ok "log"
else
  no "log ($(tr '\n' ' ' < err))"
fi

echo
echo "git: $pass passed, $fail failed"
[[ $fail -eq 0 ]]

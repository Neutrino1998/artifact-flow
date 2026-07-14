#!/usr/bin/env bash
set -euo pipefail

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

merman-cli --version | grep -Fx 'merman-cli 0.7.0'

cat > "$tmp/flow.mmd" <<'MMD'
flowchart LR
  A["提交任务"] --> B{"校验通过？"}
  B -->|"是"| C["执行"]
  B -->|"否"| D["返回修改"]
MMD

merman-cli -i "$tmp/flow.mmd" -o "$tmp/flow.svg"
merman-cli -i "$tmp/flow.mmd" -o "$tmp/flow.png" \
  --raster-fit-width 1200 -b white

python3 - "$tmp/flow.svg" "$tmp/flow.png" <<'PY'
import sys
from pathlib import Path

from PIL import Image, ImageChops

svg = Path(sys.argv[1]).read_text(encoding="utf-8")
for label in ("提交任务", "校验通过", "返回修改"):
    assert label in svg, f"SVG lost Chinese label: {label}"

with Image.open(sys.argv[2]) as opened:
    image = opened.convert("RGB")
width, height = image.size
assert 1 < width <= 8192 and 1 < height <= 8192, image.size
background = Image.new("RGB", image.size, image.getpixel((0, 0)))
assert ImageChops.difference(image, background).getbbox() is not None, "blank PNG"
PY

cat > "$tmp/invalid.mmd" <<'MMD'
flowchart LR
  A -->
MMD

if merman-cli -i "$tmp/invalid.mmd" -o "$tmp/invalid.svg" \
    >"$tmp/invalid.stdout" 2>"$tmp/invalid.stderr"; then
  echo "invalid Mermaid source unexpectedly rendered" >&2
  exit 1
fi
test ! -s "$tmp/invalid.svg"

echo "merman-cli headless render: OK"

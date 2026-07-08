#!/usr/bin/env python3
"""Validate chart palettes with only the Python standard library."""

from __future__ import annotations

import argparse
import math
import re
import sys
from itertools import combinations


HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_SURFACE = {"light": "#ffffff", "dark": "#111827"}
LIGHTNESS_BAND = {"light": (0.40, 0.76), "dark": (0.55, 0.86)}
CHROMA_FLOOR = 0.075
CONTRAST_MIN = 3.0
CVD_TARGET = 12.0
CVD_FLOOR = 8.0
ORDINAL_MIN_DELTA_L = 0.055
ORDINAL_MIN_CONTRAST = 2.0

MACHADO = {
    "protan": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deutan": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
    "tritan": (
        (1.255528, -0.076749, -0.178779),
        (-0.078411, 0.930809, 0.147602),
        (0.004733, 0.691367, 0.303900),
    ),
}


def cube_root(value: float) -> float:
    return math.copysign(abs(value) ** (1 / 3), value)


def parse_palette(raw: str) -> list[str]:
    colors = [c.strip() for c in raw.split(",") if c.strip()]
    bad = [c for c in colors if not HEX_RE.match(c)]
    if bad:
        raise SystemExit(f"invalid hex color(s): {', '.join(bad)}")
    if not colors:
        raise SystemExit("palette is empty")
    return [c.lower() for c in colors]


def hex_to_srgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear_rgb(hex_color: str) -> tuple[float, float, float]:
    return tuple(srgb_to_linear(c) for c in hex_to_srgb(hex_color))


def luminance(hex_color: str) -> float:
    r, g, b = linear_rgb(hex_color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def oklab(hex_color: str) -> tuple[float, float, float]:
    r, g, b = linear_rgb(hex_color)
    l = cube_root(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m = cube_root(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = cube_root(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (
        0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
    )


def oklch(hex_color: str) -> tuple[float, float, float]:
    l, a, b = oklab(hex_color)
    hue = math.degrees(math.atan2(b, a)) % 360
    return l, math.hypot(a, b), hue


def linear_to_lab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = rgb
    x = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b

    def f(t: float) -> float:
        return cube_root(t) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x / 0.95047), f(y), f(z / 1.08883)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def simulate_cvd(hex_color: str, kind: str) -> tuple[float, float, float]:
    r, g, b = linear_rgb(hex_color)
    matrix = MACHADO[kind]
    out = []
    for row in matrix:
        out.append(max(0.0, min(1.0, row[0] * r + row[1] * g + row[2] * b)))
    return tuple(out)


def delta_e(a: str, b: str, cvd: str | None = None) -> float:
    ar = simulate_cvd(a, cvd) if cvd else linear_rgb(a)
    br = simulate_cvd(b, cvd) if cvd else linear_rgb(b)
    lab_a = linear_to_lab(ar)
    lab_b = linear_to_lab(br)
    return math.dist(lab_a, lab_b)


def hue_spread(colors: list[str]) -> float:
    hues = sorted(oklch(c)[2] for c in colors)
    if len(hues) <= 1:
        return 0.0
    gaps = [hues[i + 1] - hues[i] for i in range(len(hues) - 1)]
    gaps.append(hues[0] + 360 - hues[-1])
    return 360 - max(gaps)


def line(state: str, name: str, detail: str) -> None:
    print(f"  [{state:<4}] {name:<22} {detail}")


def validate_categorical(colors: list[str], mode: str, surface: str, pairs: str) -> bool:
    ok = True
    lo, hi = LIGHTNESS_BAND[mode]
    off_band = [(c, round(oklch(c)[0], 3)) for c in colors if not lo <= oklch(c)[0] <= hi]
    low_chroma = [(c, round(oklch(c)[1], 3)) for c in colors if oklch(c)[1] < CHROMA_FLOOR]
    low_contrast = [(c, round(contrast(c, surface), 2)) for c in colors if contrast(c, surface) < CONTRAST_MIN]

    if off_band:
        ok = False
    line("FAIL" if off_band else "PASS", "lightness band", str(off_band) if off_band else f"L in {lo}-{hi}")

    if low_chroma:
        ok = False
    line("FAIL" if low_chroma else "PASS", "chroma floor", str(low_chroma) if low_chroma else f"C >= {CHROMA_FLOOR}")

    pair_indexes = list(combinations(range(len(colors)), 2)) if pairs == "all" else [(i, i + 1) for i in range(len(colors) - 1)]
    worst: tuple[float, str, str, str] | None = None
    for kind in ("protan", "deutan"):
        for i, j in pair_indexes:
            score = delta_e(colors[i], colors[j], kind)
            if worst is None or score < worst[0]:
                worst = (score, kind, colors[i], colors[j])
    if worst is None:
        cvd_state = "PASS"
        detail = "single color"
    else:
        cvd_state = "PASS" if worst[0] >= CVD_TARGET else "WARN" if worst[0] >= CVD_FLOOR else "FAIL"
        detail = f"worst {worst[2]} vs {worst[3]} = {worst[0]:.1f} ({worst[1]}, {pairs})"
    if cvd_state == "FAIL":
        ok = False
    line(cvd_state, "CVD separation", detail)

    line("WARN" if low_contrast else "PASS", "mark contrast", str(low_contrast) if low_contrast else f">= {CONTRAST_MIN}:1")
    if low_contrast:
        print("        contrast WARN requires direct labels, table view, or another readable channel")

    return ok


def validate_ordinal(colors: list[str], mode: str, surface: str) -> bool:
    ok = True
    lightness = [oklch(c)[0] for c in colors]
    increasing = all(a <= b for a, b in zip(lightness, lightness[1:]))
    decreasing = all(a >= b for a, b in zip(lightness, lightness[1:]))
    monotone = increasing or decreasing
    if not monotone:
        ok = False
    line("PASS" if monotone else "FAIL", "monotone lightness", str([round(v, 3) for v in lightness]))

    gaps = [abs(b - a) for a, b in zip(lightness, lightness[1:])]
    thin = [round(g, 3) for g in gaps if g < ORDINAL_MIN_DELTA_L]
    if thin:
        ok = False
    line("PASS" if not thin else "FAIL", "step separation", str(thin) if thin else f"delta L >= {ORDINAL_MIN_DELTA_L}")

    spread = hue_spread(colors)
    one_hue = spread <= 45
    if not one_hue:
        ok = False
    line("PASS" if one_hue else "FAIL", "single hue", f"spread {spread:.0f} degrees")

    edge = max(colors, key=lambda c: oklch(c)[0]) if mode == "light" else min(colors, key=lambda c: oklch(c)[0])
    edge_contrast = contrast(edge, surface)
    if edge_contrast < ORDINAL_MIN_CONTRAST:
        ok = False
    line("PASS" if edge_contrast >= ORDINAL_MIN_CONTRAST else "FAIL", "edge contrast", f"{edge} = {edge_contrast:.2f}:1")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate categorical or ordinal chart palettes.")
    parser.add_argument("palette", help="comma-separated #rrggbb colors")
    parser.add_argument("--mode", choices=("light", "dark"), default="light")
    parser.add_argument("--surface", help="chart surface hex")
    parser.add_argument("--pairs", choices=("adjacent", "all"), default="adjacent")
    parser.add_argument("--ordinal", action="store_true", help="validate as a one-hue ordered ramp")
    args = parser.parse_args()

    surface = (args.surface or DEFAULT_SURFACE[args.mode]).lower()
    if not HEX_RE.match(surface):
        raise SystemExit(f"invalid surface color: {surface}")
    colors = parse_palette(args.palette)
    kind = "ordinal" if args.ordinal else f"categorical/{args.pairs}"
    print(f"Palette check: {len(colors)} color(s), {args.mode}, surface {surface}, {kind}")
    ok = validate_ordinal(colors, args.mode, surface) if args.ordinal else validate_categorical(colors, args.mode, surface, args.pairs)
    print("Result:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

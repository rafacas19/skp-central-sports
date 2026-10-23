"""Shirt colours: what each team wears on the dashboard pitch.

The scout picks any colour per side; the dorsal printed on it is black or white,
whichever reads better on that colour (WCAG contrast). A side with no colour
keeps the pitch's original kit — white for the home side, navy for the away
side — which is set in the stylesheet, so nothing is stored for it.

Stdlib only.
"""

from __future__ import annotations

import re

DEFAULT_COLORS = {"home": "#ffffff", "away": "#1e3a8a"}
_HEX_RE = re.compile(r"^#[0-9a-f]{6}$")
_INK_DARK = "#111111"
_INK_LIGHT = "#ffffff"


def clean_color(raw: str | None) -> str | None:
    """`#rrggbb` (lower case) or None for anything else."""
    value = (raw or "").strip().lower()
    return value if _HEX_RE.match(value) else None


def _luminance(color: str) -> float:
    def channel(i: int) -> float:
        c = int(color[i:i + 2], 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5)


def ink(color: str) -> str:
    """The dorsal colour with the higher contrast on this shirt."""
    lum = _luminance(color)
    on_dark = (1.05) / (lum + 0.05)
    on_light = (lum + 0.05) / 0.05
    return _INK_LIGHT if on_dark >= on_light else _INK_DARK


def kit_style(color: str | None) -> str:
    """Inline CSS variables for a side's shirts; empty keeps the default kit."""
    if color is None:
        return ""
    return f"--shirt: {color}; --ink: {ink(color)};"


def too_similar(a: str, b: str) -> bool:
    """Two shirt colours a scout could mix up at a glance on the pitch."""
    diff = sum((int(a[i:i + 2], 16) - int(b[i:i + 2], 16)) ** 2 for i in (1, 3, 5))
    return diff < 60 ** 2

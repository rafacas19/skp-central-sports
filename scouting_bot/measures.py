"""Height as the scout reads it: metres with a decimal comma («1,75 m»).

Stored as whole centimetres (`Prospect.height_cm`), so nothing that compares or
sorts heights changes; only what is typed and what is shown speaks metres. A
typed value is read as metres when it is small (1,75 · 1.75) and as centimetres
otherwise (175), so both habits land on the same number.

Stdlib only.
"""

from __future__ import annotations

HEIGHT_MIN_CM = 100
HEIGHT_MAX_CM = 250
HEIGHT_ERROR = "La estatura en metros, p. ej. 1,75."


def parse_height(raw: str | None) -> tuple[int | None, str | None]:
    """`(centimetres, error)` from what the scout typed; empty clears it."""
    text = (raw or "").strip().lower().replace(" ", "").removesuffix("m").removesuffix("c")
    if not text:
        return None, None
    try:
        value = float(text.replace(",", "."))
    except ValueError:
        return None, HEIGHT_ERROR
    cm = round(value * 100) if value < 3 else round(value)
    if not HEIGHT_MIN_CM <= cm <= HEIGHT_MAX_CM:
        return None, HEIGHT_ERROR
    return cm, None


def height_m(cm: int | None) -> float | None:
    """1.75 for 175 — the number a spreadsheet cell holds."""
    return None if cm is None else round(cm / 100, 2)


def height_input(cm: int | None) -> str:
    """«1,75» — what the form shows back, so opening and saving keeps the value."""
    return "" if cm is None else f"{cm / 100:.2f}".replace(".", ",")


def format_height(cm: int | None) -> str:
    """«1,75 m» for display; empty when unknown."""
    return f"{height_input(cm)} m" if cm is not None else ""

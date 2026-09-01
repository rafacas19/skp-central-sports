"""Validation for the player edit form.

HTML forms only ever submit strings, and an empty one means "clear this field".
`parse_player_form` turns a raw form mapping into typed prospect updates plus
per-field Spanish error messages, so the router stays about routing and the
template can re-render exactly what the client typed.

Ranges are deliberately loose — they exist to catch a slipped digit (an 18-year-old
entered as 180), not to argue with the scout about what a footballer looks like.
"""

from __future__ import annotations

from datetime import date

from ..categories import split_category
from ..models import CONTACT_NONE, CONTACT_STATUSES, FEET, RATING_DECISIONS
from ..positions import ROLES
from ..taxonomy import normalize_identity, normalize_name

# Editable text fields: form name → (model field, max length). `nombre` is not
# here — it is the identity key, is never NULL, and is handled on its own below.
TEXT_FIELDS: dict[str, tuple[str, int]] = {
    "equipo": ("team", 120),
    "nacionalidad": ("nationality", 60),
    "procedencia": ("origin_club", 120),
    "agente": ("agent_name", 120),
    "telefono_agente": ("agent_phone", 40),
    "notas": ("notes", 4000),
    "notas_contacto": ("contact_notes", 4000),
}

# Editable whole-number fields: form name → (model field, min, max, label).
INT_FIELDS: dict[str, tuple[str, int, int, str]] = {
    "dorsal": ("shirt_number", 1, 99, "El dorsal"),
    "anio_nacimiento": ("birth_year", 1950, date.today().year, "El año de nacimiento"),
    "edad": ("age", 10, 60, "La edad"),
    "estatura": ("height_cm", 100, 250, "La estatura en cm"),
    "peso": ("weight_kg", 30, 150, "El peso en kg"),
    "valor": ("market_value_usd", 0, 999_000_000, "El valor de mercado"),
    "contrato_hasta": ("contract_year", 2000, date.today().year + 20, "El año de contrato"),
}

ROLE_NAMES = tuple(p.role for p in ROLES)
DECISION_LABELS = tuple(RATING_DECISIONS[r] for r in (5, 4, 3, 2, 1))
# Ratings the form offers: whole and half steps across the 1–5 scale.
RATING_CHOICES = ("1", "1.5", "2", "2.5", "3", "3.5", "4", "4.5", "5")


def _digits(raw: str) -> str:
    """Keep only digits, so «$250.000» and «250,000» both read as 250000."""
    return "".join(c for c in raw if c.isdigit())


def parse_player_form(
    form: dict[str, str], *, current_position: str | None = None
) -> tuple[dict, dict[str, str]]:
    """Validate a submitted edit form.

    Returns `(updates, errors)`: the model fields to write (empty inputs map to
    None, i.e. "clear it") and any per-field error messages. When `errors` is
    non-empty nothing should be written.
    """
    updates: dict = {}
    errors: dict[str, str] = {}

    # The name is the identity key: it is a string, never NULL, and an empty one
    # is meaningful (the player stays an unidentified profile). Re-keying the
    # prospect is the router's job — see `apply_identity`.
    name = (form.get("nombre") or "").strip()
    if len(name) > 120:
        errors["nombre"] = "Máximo 120 caracteres."
    else:
        updates["name"] = name

    for form_name, (field, max_len) in TEXT_FIELDS.items():
        value = (form.get(form_name) or "").strip()
        if len(value) > max_len:
            errors[form_name] = f"Máximo {max_len} caracteres."
            continue
        updates[field] = value or None

    for form_name, (field, low, high, label) in INT_FIELDS.items():
        raw = (form.get(form_name) or "").strip()
        if not raw:
            updates[field] = None
            continue
        digits = _digits(raw) if field == "market_value_usd" else raw
        try:
            number = int(digits)
        except ValueError:
            errors[form_name] = f"{label} debe ser un número."
            continue
        if not low <= number <= high:
            errors[form_name] = f"{label} debe estar entre {low} y {high}."
            continue
        updates[field] = number

    # Position: a canonical role, or the scout's existing free text left as-is
    # (the form offers it as an option so opening and saving never rewrites it).
    position = (form.get("posicion") or "").strip()
    if not position:
        updates["position"] = None
    elif position in ROLE_NAMES or position == (current_position or "").strip():
        updates["position"] = position
    else:
        errors["posicion"] = "Elige una posición de la lista."

    foot = (form.get("pie") or "").strip().lower()
    if not foot:
        updates["preferred_foot"] = None
    elif foot in FEET:
        updates["preferred_foot"] = foot
    else:
        errors["pie"] = "Elige un pie de la lista."

    # RATING_CHOICES is what the form offers; parsing stays permissive so a
    # rating the bot captured off-step ("valoración 4,2") survives a save.
    rating_raw = (form.get("valoracion") or "").strip().replace(",", ".")
    if not rating_raw:
        updates["latest_rating"] = None
    else:
        try:
            rating = float(rating_raw)
        except ValueError:
            errors["valoracion"] = "La valoración debe ser un número de 1 a 5."
        else:
            if 1 <= rating <= 5:
                updates["latest_rating"] = rating
            else:
                errors["valoracion"] = "La valoración debe estar entre 1 y 5."

    # An explicit decision overrides; "" means derive it from the rating, which
    # is exactly what the bot does when a rating arrives in a note.
    decision = (form.get("decision") or "").strip()
    if decision and decision not in DECISION_LABELS:
        errors["decision"] = "Elige una decisión de la lista."
    else:
        updates["decision_status"] = decision or None

    # Contact follow-up. "Sin contactar" is the absence of a status, so it is
    # stored as NULL — the column then means "a conversation happened", and the
    # list/profile render the label.
    contact = (form.get("estado_contacto") or "").strip()
    if contact and contact not in CONTACT_STATUSES:
        errors["estado_contacto"] = "Elige un estado de la lista."
    else:
        updates["contact_status"] = None if contact in ("", CONTACT_NONE) else contact

    updates["last_contact_at"], date_error = _parse_contact_date(
        form.get("fecha_contacto")
    )
    if date_error:
        errors["fecha_contacto"] = date_error

    return updates, errors


def _parse_contact_date(raw: str | None) -> tuple[date | None, str | None]:
    """The date input's `YYYY-MM-DD` (what every browser submits), or an error.

    A future date is rejected: the field records when a conversation happened,
    not when one is planned."""
    value = (raw or "").strip()
    if not value:
        return None, None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None, "Usa una fecha válida (AAAA-MM-DD)."
    if parsed > date.today():
        return None, "La fecha de contacto no puede ser futura."
    return parsed, None


def apply_identity(updates: dict) -> dict:
    """Fill in the derived identity columns whenever the name or team changed.

    Uses the same normalization the bot keys prospects on, so a player renamed
    here collapses onto exactly the record the bot would have reached, and naming
    an unidentified profile clears its temporary flag.

    A team typed with its category ("Santa Fe U18") is split the same way the bot
    splits it, so the form and the capture path agree on what the club is. The
    category is only written when the typed name carries one — a club typed
    without it ("Santa Fe") leaves an already-stored category alone rather than
    clearing it, since the form has no field to re-enter it with.

    Blanking an existing name is ignored on purpose: a temporary profile's
    normalized name is a synthetic per-match key that the bot looks numbers up
    by, and overwriting it with "" would orphan it mid-match. Fixing a wrong name
    means typing the right one (or merging), not clearing it.
    """
    name = updates.get("name")
    if name:
        updates["normalized_name"] = normalize_identity(name)
        updates["is_temporary"] = False
    else:
        updates.pop("name", None)
    if "team" in updates:
        club, category = split_category(updates["team"])
        updates["team"] = club
        updates["normalized_team"] = normalize_name(club or "")
        if category:
            updates["category"] = category
    return updates

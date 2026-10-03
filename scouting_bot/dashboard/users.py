"""Dashboard accounts: password hashing, the first-admin seed, user management.

Passwords are hashed with stdlib scrypt — no extra dependency — and stored as
`scrypt$<n>$<r>$<p>$<salt-hex>$<hash-hex>`, so the cost can be raised later
without breaking stored hashes. Temporary passwords are hashed the same way:
the admin sees one exactly once, when it is created.

There is no email service, so recovery is an admin task: an admin resets a
user's password to a new temporary one, and the user must choose a new
password on their next sign-in.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets

from ..config import settings
from ..models import ROLE_ADMIN, ROLES, DashboardUser

logger = logging.getLogger(__name__)

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
MIN_PASSWORD = 10
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,40}$")
# Readable temporary passwords: no 0/O, 1/l/I to misread over the phone.
_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"


class UserError(ValueError):
    """A user-management request that breaks a rule; the message is Spanish
    and safe to show on the page."""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        scheme, n, r, p, salt, digest = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt),
            n=int(n), r=int(r), p=int(p), dklen=len(digest) // 2,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate.hex(), digest)


# A hash of a random string, to spend the same scrypt time on unknown usernames:
# the response time must not reveal whether a username exists.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def burn_time(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


def temporary_password() -> str:
    """Readable, 12 characters, grouped "xxxx-xxxx-xxxx" for dictating."""
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def normalize_username(raw: str | None) -> str:
    return (raw or "").strip().lower()


def password_problem(new: str, confirm: str, *, old_hash: str | None = None) -> str | None:
    """Why a chosen password is not acceptable, or None."""
    if len(new) < MIN_PASSWORD:
        return f"La contraseña debe tener al menos {MIN_PASSWORD} caracteres."
    if new != confirm:
        return "Las dos contraseñas no coinciden."
    if old_hash and verify_password(new, old_hash):
        return "Elige una contraseña distinta de la anterior."
    return None


async def seed_admin_from_env() -> DashboardUser | None:
    """Create the first admin from ADMIN_USERNAME + ADMIN_TEMP_PASSWORD.

    Only when that username does not exist yet: a redeploy must never reset an
    account someone has already taken over. Returns the user created, if any."""
    username = normalize_username(settings.admin_username)
    if not username or not settings.admin_temp_password:
        return None
    if not USERNAME_RE.match(username):
        logger.error("ADMIN_USERNAME %r is not a valid username; no admin seeded", username)
        return None
    if await DashboardUser.filter(username=username).exists():
        return None
    user = await DashboardUser.create(
        username=username,
        display_name=username,
        role=ROLE_ADMIN,
        password_hash=hash_password(settings.admin_temp_password),
        must_change_password=True,
    )
    logger.info("Seeded dashboard admin %r from ADMIN_USERNAME", username)
    return user


async def _active_admins(excluding: int | None = None) -> int:
    q = DashboardUser.filter(role=ROLE_ADMIN, is_active=True)
    if excluding is not None:
        q = q.exclude(id=excluding)
    return await q.count()


async def create_user(username: str, display_name: str, role: str, password: str | None = None
                      ) -> tuple[DashboardUser, str]:
    """A new user with a temporary password; returns (user, the password)."""
    username = normalize_username(username)
    display_name = (display_name or "").strip()[:80] or username
    if not USERNAME_RE.match(username):
        raise UserError("El usuario debe tener de 3 a 40 caracteres: letras, números, «.», «_» o «-».")
    if role not in ROLES:
        raise UserError("Elige un rol de la lista.")
    if await DashboardUser.filter(username=username).exists():
        raise UserError("Ya existe un usuario con ese nombre.")
    temp = (password or "").strip() or temporary_password()
    if len(temp) < 8:
        raise UserError("La contraseña temporal debe tener al menos 8 caracteres.")
    user = await DashboardUser.create(
        username=username, display_name=display_name, role=role,
        password_hash=hash_password(temp), must_change_password=True,
    )
    return user, temp


async def reset_password(user: DashboardUser) -> str:
    """Give the user a new temporary password and sign them out everywhere."""
    temp = temporary_password()
    user.password_hash = hash_password(temp)
    user.must_change_password = True
    user.session_version += 1
    await user.save()
    return temp


async def set_role(user: DashboardUser, role: str, *, acting: DashboardUser) -> None:
    if role not in ROLES:
        raise UserError("Elige un rol de la lista.")
    if user.role == ROLE_ADMIN and role != ROLE_ADMIN and user.is_active \
            and await _active_admins(excluding=user.id) == 0:
        raise UserError("Es el último administrador activo: no se le puede quitar el rol.")
    if role != user.role:
        user.role = role
        user.session_version += 1
        await user.save()


async def set_active(user: DashboardUser, active: bool, *, acting: DashboardUser) -> None:
    if not active and user.id == acting.id:
        raise UserError("No puedes desactivar tu propio usuario.")
    if not active and user.role == ROLE_ADMIN and await _active_admins(excluding=user.id) == 0:
        raise UserError("Es el último administrador activo: no se puede desactivar.")
    if active != user.is_active:
        user.is_active = active
        user.session_version += 1
        await user.save()


async def change_password(user: DashboardUser, new: str) -> None:
    """The user's own new password; signs out their other sessions."""
    user.password_hash = hash_password(new)
    user.must_change_password = False
    user.session_version += 1
    await user.save()

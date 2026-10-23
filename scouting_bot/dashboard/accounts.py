"""Signed-in account pages and user management.

· /cuenta/contrasena — choose a new password. A user signed in with a temporary
  password is held here until they do (auth.require_dashboard redirects every
  other page); later, anyone can change theirs from the same page.
· /usuarios — admins only (enforced in auth.require_dashboard): create users
  with a temporary password, reset passwords, change roles, disable accounts.

There is no email service, so a temporary password is shown to the admin once,
on the page that created it, for them to hand over in person.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from ..models import ROLE_LABELS, ROLES, DashboardUser
from . import auth, users
from .router import _render, _set_session

router = APIRouter(prefix="/dashboard", include_in_schema=False)


# ── Own password ─────────────────────────────────────────────────────────
def _password_context(user: DashboardUser, error: str | None = None) -> dict:
    return {"forced": user.must_change_password, "error": error, "min_length": users.MIN_PASSWORD}


@router.get(
    "/cuenta/contrasena", response_class=HTMLResponse, dependencies=[Depends(auth.require_dashboard)]
)
async def password_page(request: Request):
    user = await auth.current_user(request)
    return _render(request, "password.html", _password_context(user))


@router.post("/cuenta/contrasena", dependencies=[Depends(auth.require_dashboard)])
async def password_submit(
    request: Request,
    actual: str = Form(default=""),
    nueva: str = Form(default=""),
    repetir: str = Form(default=""),
    csrf: str = Form(default=""),
):
    """Set a new password. The current one is asked for, except right after
    signing in with a temporary password (it was just typed). Other sessions
    of this user are signed out; this one gets a fresh cookie."""
    auth.require_csrf(csrf)
    user = await auth.current_user(request)
    if not user.must_change_password and not users.verify_password(actual, user.password_hash):
        return _render(request, "password.html",
                       _password_context(user, "La contraseña actual no es correcta."),
                       status_code=status.HTTP_400_BAD_REQUEST)
    problem = users.password_problem(nueva, repetir, old_hash=user.password_hash)
    if problem:
        return _render(request, "password.html", _password_context(user, problem),
                       status_code=status.HTTP_400_BAD_REQUEST)
    await users.change_password(user, nueva)
    response = RedirectResponse("/dashboard?aviso=contrasena", status_code=status.HTTP_303_SEE_OTHER)
    _set_session(response, user)
    return response


# ── User management (admins) ─────────────────────────────────────────────
async def _users_context(request: Request, **extra) -> dict:
    me = await auth.current_user(request)
    rows = await DashboardUser.all().order_by("username")
    return {
        "users": [
            {
                "id": u.id, "username": u.username, "display_name": u.display_name,
                "role": u.role, "role_label": ROLE_LABELS[u.role], "active": u.is_active,
                "pending": u.must_change_password, "last_login": u.last_login_at,
                "is_me": u.id == me.id,
            }
            for u in rows
        ],
        "roles": [(r, ROLE_LABELS[r]) for r in ROLES],
        "values": extra.pop("values", {}),
        **extra,
    }


@router.get("/usuarios", response_class=HTMLResponse, dependencies=[Depends(auth.require_dashboard)])
async def users_page(request: Request):
    return _render(request, "users.html", await _users_context(request))


@router.post("/usuarios", dependencies=[Depends(auth.require_dashboard)])
async def user_create(
    request: Request,
    usuario: str = Form(default=""),
    nombre: str = Form(default=""),
    rol: str = Form(default=""),
    temporal: str = Form(default=""),
    csrf: str = Form(default=""),
):
    auth.require_csrf(csrf)
    try:
        user, temp = await users.create_user(usuario, nombre, rol, temporal or None)
    except users.UserError as exc:
        context = await _users_context(
            request, error=str(exc), values={"usuario": usuario, "nombre": nombre, "rol": rol}
        )
        return _render(request, "users.html", context, status_code=status.HTTP_400_BAD_REQUEST)
    # Shown once, here — the page is not a redirect, so the password never
    # lands in a URL or the browser history.
    context = await _users_context(
        request, issued={"username": user.username, "password": temp, "new": True}
    )
    return _render(request, "users.html", context)


async def _target(user_id: int) -> DashboardUser | None:
    return await DashboardUser.get_or_none(id=user_id)


@router.post("/usuarios/{user_id}/restablecer", dependencies=[Depends(auth.require_dashboard)])
async def user_reset(request: Request, user_id: int, csrf: str = Form(default="")):
    auth.require_csrf(csrf)
    target = await _target(user_id)
    if target is None:
        return RedirectResponse("/dashboard/usuarios", status_code=status.HTTP_303_SEE_OTHER)
    temp = await users.reset_password(target)
    context = await _users_context(
        request, issued={"username": target.username, "password": temp, "new": False}
    )
    return _render(request, "users.html", context)


@router.post("/usuarios/{user_id}/rol", dependencies=[Depends(auth.require_dashboard)])
async def user_role(request: Request, user_id: int, rol: str = Form(default=""), csrf: str = Form(default="")):
    auth.require_csrf(csrf)
    target, me = await _target(user_id), await auth.current_user(request)
    if target is not None:
        try:
            await users.set_role(target, rol, acting=me)
        except users.UserError as exc:
            return _render(request, "users.html", await _users_context(request, error=str(exc)),
                           status_code=status.HTTP_400_BAD_REQUEST)
    return RedirectResponse("/dashboard/usuarios", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/usuarios/{user_id}/estado", dependencies=[Depends(auth.require_dashboard)])
async def user_active(request: Request, user_id: int, activo: str = Form(default=""), csrf: str = Form(default="")):
    auth.require_csrf(csrf)
    target, me = await _target(user_id), await auth.current_user(request)
    if target is not None:
        try:
            await users.set_active(target, activo == "1", acting=me)
        except users.UserError as exc:
            return _render(request, "users.html", await _users_context(request, error=str(exc)),
                           status_code=status.HTTP_400_BAD_REQUEST)
    return RedirectResponse("/dashboard/usuarios", status_code=status.HTTP_303_SEE_OTHER)

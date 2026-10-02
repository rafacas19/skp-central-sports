"""Dashboard accounts and roles: seeding, sign-in, first-login password change,
user management, and the read / write / admin permission matrix."""

import re
import time

import httpx
import pytest
import pytest_asyncio

from scouting_bot.config import settings
from scouting_bot.dashboard import auth, users
from scouting_bot.models import DashboardUser, Prospect, Session
from tests.conftest import make_user

PASSWORD = "prueba-scouting"


def _set(field: str, value) -> object:
    old = getattr(settings, field)
    object.__setattr__(settings, field, value)
    return old


@pytest_asyncio.fixture
async def client(storage):
    from scouting_bot.app import app

    old = {f: _set(f, v) for f, v in
           [("dashboard_password", "viejo-compartido"), ("dashboard_secret", "clave-de-firma"),
            ("use_mock_ai", True), ("admin_username", ""), ("admin_temp_password", "")]}
    auth._attempts.clear()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    for f, v in old.items():
        _set(f, v)
    auth._attempts.clear()


def _csrf(html: str) -> str:
    marker = f'name="{auth.CSRF_FIELD}" value="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


async def _login(c, username: str, password: str = PASSWORD, ip: str = "10.0.0.1") -> httpx.Response:
    return await c.post("/dashboard/login", data={"username": username, "password": password},
                        headers={"x-forwarded-for": ip})


async def _signed_in(c, role: str, username: str | None = None) -> DashboardUser:
    user = await make_user(username or role, PASSWORD, role)
    c.cookies.clear()
    resp = await _login(c, user.username)
    assert resp.status_code == 303
    return user


# ── 1. Seeding the first admin ───────────────────────────────────────────
async def test_first_admin_is_seeded_once_and_never_overwritten(client):
    _set("admin_username", "Admin")
    _set("admin_temp_password", "Temp-12345")
    user = await users.seed_admin_from_env()
    assert (user.username, user.role, user.must_change_password) == ("admin", "admin", True)
    assert users.verify_password("Temp-12345", user.password_hash)

    await users.change_password(user, "mi-clave-propia")
    assert await users.seed_admin_from_env() is None  # second boot: nothing
    again = await DashboardUser.get(username="admin")
    assert users.verify_password("mi-clave-propia", again.password_hash)
    assert await DashboardUser.all().count() == 1


async def test_no_users_and_no_seed_explains_what_to_set(client):
    page = await client.get("/dashboard/login")
    assert "ADMIN_USERNAME" in page.text and "ADMIN_TEMP_PASSWORD" in page.text


# ── 2. First sign-in with a temporary password ───────────────────────────
async def test_temporary_password_forces_a_new_one(client):
    await make_user("admin", "Temp-12345", "admin", must_change=True)
    resp = await _login(client, "admin", "Temp-12345")
    assert resp.headers["location"] == "/dashboard/cuenta/contrasena"
    for path in ("/dashboard", "/dashboard/jugadores", "/dashboard/usuarios"):
        r = await client.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/dashboard/cuenta/contrasena"

    page = await client.get("/dashboard/cuenta/contrasena")
    assert "Elige tu contraseña" in page.text and 'name="actual"' not in page.text
    token = _csrf(page.text)
    bad = await client.post("/dashboard/cuenta/contrasena",
                            data={"csrf": token, "nueva": "corta", "repetir": "corta"})
    assert bad.status_code == 400 and "al menos 10" in bad.text
    same = await client.post("/dashboard/cuenta/contrasena",
                             data={"csrf": token, "nueva": "Temp-12345", "repetir": "Temp-12345"})
    assert same.status_code == 400 and "distinta" in same.text
    ok = await client.post("/dashboard/cuenta/contrasena",
                           data={"csrf": token, "nueva": "nueva-clave-segura", "repetir": "nueva-clave-segura"})
    assert ok.status_code == 303
    assert (await client.get("/dashboard")).status_code == 200  # the fresh cookie works

    client.cookies.clear()
    assert (await _login(client, "admin", "Temp-12345")).status_code == 401
    assert (await _login(client, "admin", "nueva-clave-segura")).headers["location"] == "/dashboard"


async def test_changing_ones_own_password_needs_the_current_one(client):
    await _signed_in(client, "read", "invitado")
    page = await client.get("/dashboard/cuenta/contrasena")
    assert 'name="actual"' in page.text  # read users may change their own password
    resp = await client.post("/dashboard/cuenta/contrasena", data={
        "csrf": _csrf(page.text), "actual": "no-es", "nueva": "otra-clave-larga", "repetir": "otra-clave-larga"})
    assert resp.status_code == 400 and "actual no es correcta" in resp.text
    resp = await client.post("/dashboard/cuenta/contrasena", data={
        "csrf": _csrf(page.text), "actual": PASSWORD, "nueva": "otra-clave-larga", "repetir": "otra-clave-larga"})
    assert resp.status_code == 303


# ── 3. The shared password is retired ────────────────────────────────────
async def test_the_shared_password_and_old_cookies_no_longer_work(client):
    await make_user("admin", PASSWORD, "admin")
    assert (await _login(client, "", "viejo-compartido")).status_code == 401
    assert (await _login(client, "admin", "viejo-compartido")).status_code == 401
    legacy = auth._sign(int(time.time()) + 3600)  # the old "<expiry>.<hmac>" cookie
    client.cookies.set(auth.COOKIE_NAME, legacy, path="/dashboard")
    resp = await client.get("/dashboard")
    assert resp.status_code == 303 and resp.headers["location"] == "/dashboard/login"


# ── 4. Admin creates users ───────────────────────────────────────────────
async def test_admin_creates_users_with_a_one_time_temporary_password(client):
    await _signed_in(client, "admin")
    page = await client.get("/dashboard/usuarios")
    resp = await client.post("/dashboard/usuarios", data={
        "csrf": _csrf(page.text), "usuario": "Scout1", "nombre": "Scout Uno", "rol": "write"})
    assert resp.status_code == 200
    temp = re.search(r'id="temp-password">([^<]+)<', resp.text).group(1)
    assert re.fullmatch(r"[A-Za-z2-9]{4}-[A-Za-z2-9]{4}-[A-Za-z2-9]{4}", temp)
    scout = await DashboardUser.get(username="scout1")
    assert (scout.role, scout.must_change_password, scout.display_name) == ("write", True, "Scout Uno")
    assert temp not in scout.password_hash
    assert temp not in (await client.get("/dashboard/usuarios")).text  # shown once

    resp = await client.post("/dashboard/usuarios", data={
        "csrf": _csrf(page.text), "usuario": "invitado", "nombre": "", "rol": "read", "temporal": "Invitado-2026"})
    assert "Invitado-2026" in resp.text
    dup = await client.post("/dashboard/usuarios", data={
        "csrf": _csrf(page.text), "usuario": "SCOUT1", "rol": "read"})
    assert dup.status_code == 400 and "Ya existe" in dup.text

    client.cookies.clear()
    assert (await _login(client, "scout1", temp)).headers["location"] == "/dashboard/cuenta/contrasena"


# ── 5. The permission matrix, over every route ───────────────────────────
def _dashboard_routes():
    from scouting_bot.app import app

    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/dashboard") or path.startswith("/dashboard/static"):
            continue
        for method in getattr(route, "methods", set()) & {"GET", "POST"}:
            yield method, path


def _concrete(path: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: "home" if m.group(1) == "side" else "1", path)


OPEN = {("GET", "/dashboard/login"), ("POST", "/dashboard/login"), ("POST", "/dashboard/logout")}
ANY_ROLE = {("GET", "/dashboard/cuenta/contrasena"), ("POST", "/dashboard/cuenta/contrasena")}


async def test_read_users_can_look_at_everything_and_change_nothing(client):
    await _signed_in(client, "read")
    token = _csrf((await client.get("/dashboard/cuenta/contrasena")).text)
    posts = gets_blocked = 0
    for method, path in sorted(set(_dashboard_routes())):
        if (method, path) in OPEN | ANY_ROLE:
            continue
        url = _concrete(path)
        if method == "POST":
            resp = await client.post(url, data={"csrf": token})
            assert resp.status_code == 403, (method, path, resp.status_code)
            posts += 1
        else:
            resp = await client.get(url)
            if auth._WRITE_PAGES.match(url) or path.startswith("/dashboard/usuarios"):
                assert resp.status_code == 403, (method, path, resp.status_code)
                gets_blocked += 1
            else:
                assert resp.status_code != 403, (method, path, resp.status_code)
    assert posts == 26 + 4  # every data route plus the four user-management ones
    assert gets_blocked >= 10
    refused = await client.post("/dashboard/selecciones", data={"csrf": token, "nombre": "Colombia"})
    assert "No tienes permiso" in refused.text  # an HTML page, not JSON


async def test_write_users_can_change_data_but_not_manage_users(client):
    await _signed_in(client, "write")
    token = _csrf((await client.get("/dashboard/cuenta/contrasena")).text)
    for method, path in sorted(set(_dashboard_routes())):
        if (method, path) in OPEN | ANY_ROLE:
            continue
        url = _concrete(path)
        resp = await (client.post(url, data={"csrf": token}) if method == "POST" else client.get(url))
        if path.startswith("/dashboard/usuarios"):
            assert resp.status_code == 403, (method, path)
        else:
            assert resp.status_code != 403, (method, path, resp.status_code)
    page = await client.get("/dashboard")
    assert 'href="/dashboard/usuarios"' not in page.text


async def test_only_admins_see_and_open_user_management(client):
    await _signed_in(client, "admin")
    page = await client.get("/dashboard")
    assert 'href="/dashboard/usuarios"' in page.text
    assert (await client.get("/dashboard/usuarios")).status_code == 200


# ── 6. Read-only pages show no write controls ────────────────────────────
async def test_read_users_see_no_write_controls(client, storage):
    from scouting_bot.evaluations import save_evaluation
    from scouting_bot.models import ORIGIN_WEB
    from scouting_bot.profiles import get_profile

    match = await storage.create_session(1, "Junior", "Nacional", None, origin=ORIGIN_WEB)
    player = await storage.get_or_create_prospect(1, "Camilo Restrepo", "Junior", position="Lateral izquierdo")
    await save_evaluation(match, player, profile=get_profile("lateral"), raw_scores={"1.1": 4})
    await _signed_in(client, "read")

    assert "+ Nuevo partido" not in (await client.get("/dashboard/partidos")).text
    assert "+ Nuevo jugador" not in (await client.get("/dashboard/jugadores")).text
    detail = (await client.get(f"/dashboard/partidos/{match.id}")).text
    assert f"/dashboard/partidos/{match.id}/editar" not in detail and "Evaluar otro jugador" not in detail
    assert "Ver evaluación" in detail and "Camilo Restrepo" in detail  # the data is there
    pitch = (await client.get(f"/dashboard/partidos/{match.id}/campo")).text
    assert "/puesto/" not in pitch and "Cambio ·" not in pitch and "Finalizar partido" not in pitch
    sheet = (await client.get(f"/dashboard/partidos/{match.id}/jugadores/{player.id}/evaluar")).text
    assert 'fieldset class="sheet-body" disabled' in sheet and "data-readonly" in sheet
    assert "Identificar jugador" not in sheet and "Borrar evaluación" not in sheet
    assert 'value="4" checked' in sheet
    profile = (await client.get(f"/dashboard/jugadores/{player.id}")).text
    assert f"/dashboard/jugadores/{player.id}/editar" not in profile and "Informe de perfil" in profile


# ── 7. Reset, role change and disabling sign the user out ────────────────
async def test_reset_role_change_and_disable_end_the_users_sessions(client):
    async with httpx.AsyncClient(transport=client._transport, base_url="http://test") as other:
        scout = await make_user("scout", PASSWORD, "write")
        assert (await _login(other, "scout")).status_code == 303
        assert (await other.get("/dashboard")).status_code == 200

        await _signed_in(client, "admin")
        token = _csrf((await client.get("/dashboard/usuarios")).text)
        resp = await client.post(f"/dashboard/usuarios/{scout.id}/rol", data={"csrf": token, "rol": "read"})
        assert resp.status_code == 303
        assert (await other.get("/dashboard")).status_code == 303  # signed out

        assert (await _login(other, "scout")).status_code == 303
        reset = await client.post(f"/dashboard/usuarios/{scout.id}/restablecer", data={"csrf": token})
        assert "Contraseña restablecida" in reset.text
        assert '<dialog class="temp-dialog"' in reset.text and "Copiar" in reset.text
        assert (await other.get("/dashboard")).status_code == 303
        assert (await _login(other, "scout")).status_code == 401  # old password is gone

        await client.post(f"/dashboard/usuarios/{scout.id}/estado", data={"csrf": token, "activo": "0"})
        assert not (await DashboardUser.get(id=scout.id)).is_active
        temp = re.search(r'id="temp-password">([^<]+)<', reset.text).group(1)
        assert (await _login(other, "scout", temp)).status_code == 401  # disabled


# ── 8. The last admin is protected ───────────────────────────────────────
async def test_the_last_admin_cannot_be_demoted_or_disabled(client):
    me = await _signed_in(client, "admin")
    token = _csrf((await client.get("/dashboard/usuarios")).text)
    resp = await client.post(f"/dashboard/usuarios/{me.id}/rol", data={"csrf": token, "rol": "write"})
    assert resp.status_code == 400 and "último administrador" in resp.text
    resp = await client.post(f"/dashboard/usuarios/{me.id}/estado", data={"csrf": token, "activo": "0"})
    assert resp.status_code == 400 and "tu propio usuario" in resp.text
    other = await make_user("segundo", PASSWORD, "admin")
    resp = await client.post(f"/dashboard/usuarios/{other.id}/estado", data={"csrf": token, "activo": "0"})
    assert resp.status_code == 303 and not (await DashboardUser.get(id=other.id)).is_active
    assert (await DashboardUser.get(id=me.id)).role == "admin"


# ── 9. One error message; per-IP and per-username limits ─────────────────
async def test_login_errors_reveal_nothing_and_are_rate_limited(client):
    await make_user("admin", PASSWORD, "admin")
    unknown = await _login(client, "nadie", "loquesea")
    wrong = await _login(client, "admin", "loquesea", ip="10.0.0.2")
    assert unknown.status_code == wrong.status_code == 401
    assert "Usuario o contraseña incorrectos" in unknown.text and "Usuario o contraseña incorrectos" in wrong.text

    auth._attempts.clear()
    for i in range(auth.MAX_ATTEMPTS):  # spread over many IPs: the username limit holds
        await _login(client, "admin", "mal", ip=f"10.1.0.{i}")
    locked = await _login(client, "admin", PASSWORD, ip="10.9.9.9")
    assert "Demasiados intentos" in locked.text and auth.COOKIE_NAME not in client.cookies


# ── 10. Passwords are hashed ─────────────────────────────────────────────
def test_passwords_are_stored_as_scrypt_hashes():
    stored = users.hash_password("una-clave-larga")
    assert stored.startswith("scrypt$16384$8$1$") and "una-clave-larga" not in stored
    assert users.verify_password("una-clave-larga", stored)
    assert not users.verify_password("otra", stored)
    assert not users.verify_password("una-clave-larga", "texto-plano")
    assert users.hash_password("x" * 10) != users.hash_password("x" * 10)  # salted


async def test_a_new_web_match_records_who_created_it(client):
    await _signed_in(client, "write", "wilmer")
    page = await client.get("/dashboard/partidos/nuevo")
    await client.post("/dashboard/partidos/nuevo", data={
        "csrf": _csrf(page.text), "local": "Junior", "visitante": "Nacional", "fecha": "2026-10-01"})
    assert (await Session.get()).scout_name == "Wilmer"
    assert await Prospect.all().count() == 0

"""End-to-end test harness for the Telegram bot.

Drives real Telegram `Update` JSON through the real FastAPI webhook
(`POST /telegram/{token}`) → real PTB Application → real handlers → real
ScoutingService → real Postgres. The ONLY thing faked is the Telegram network
boundary: a `FakeBot` stands in for `telegram.Bot`, recording every outbound
call (replies, documents, callback answers) into an `Outbox` the tests assert
on, and serving canned bytes for file downloads.

Why this works (verified against PTB 21.6):
  - `Application.process_update` needs only `_initialized == True`; it does no
    network I/O. `await application.initialize()` with a no-op `FakeBot` flips
    that flag without calling Telegram.
  - Every reply path (`Message.reply_text/reply_document`,
    `CallbackQuery.answer/edit_message_text`, `*.get_file`) delegates to
    `get_bot().<method>`, so the FakeBot is the single seam.
  - `httpx.ASGITransport` runs the app on the test's own event loop (so Tortoise
    connections stay on the loop that owns them) and does NOT run the FastAPI
    lifespan (so the real init/webhook/network code never fires).
"""

from __future__ import annotations

CHAT_ID = 555
USER_ID = 555  # must be stable across updates so context.user_data persists
BOT_ID = 42
_DATE = 1700000000


# ── Fake Telegram boundary ────────────────────────────────────────────────────
class FakeFile:
    """Stands in for telegram.File: yields canned bytes instead of downloading."""

    def __init__(self, file_id: str, content: bytes) -> None:
        self.file_id = file_id
        self._content = content

    async def download_as_bytearray(self, buf=None) -> bytearray:
        return bytearray(self._content)


class Outbox:
    """Records every outbound bot call, with a small assertion API."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record(self, method: str, **fields) -> None:
        self.calls.append({"method": method, **fields})

    # — queries —
    def texts(self) -> list[str]:
        """All text sent to the user, in order (send_message + edit_message_text)."""
        return [
            c["text"]
            for c in self.calls
            if c["method"] in ("send_message", "edit_message_text")
            and c.get("text") is not None
        ]

    def last_text(self) -> str | None:
        ts = self.texts()
        return ts[-1] if ts else None

    def texts_containing(self, substr: str) -> list[str]:
        return [t for t in self.texts() if substr in t]

    def count_text(self, substr: str) -> int:
        return len(self.texts_containing(substr))

    def documents_sent(self) -> list[tuple[str | None, bytes]]:
        return [
            (c.get("filename"), c.get("content"))
            for c in self.calls
            if c["method"] == "send_document"
        ]

    def keyboards(self) -> list:
        """Every reply_markup attached to a sent/edited message (non-None)."""
        return [
            c["reply_markup"]
            for c in self.calls
            if c["method"] in ("send_message", "edit_message_text")
            and c.get("reply_markup") is not None
        ]

    def callback_data(self) -> list[str]:
        """Flatten every inline-button callback_data across all keyboards sent."""
        data: list[str] = []
        for kb in self.keyboards():
            for row in kb.inline_keyboard:
                for btn in row:
                    if btn.callback_data is not None:
                        data.append(btn.callback_data)
        return data

    def answered_callbacks(self) -> int:
        return sum(1 for c in self.calls if c["method"] == "answer_callback_query")

    @staticmethod
    def format_calls(calls: list[dict]) -> list[str]:
        """Render a list of recorded outbound calls as chat-style transcript lines."""
        lines: list[str] = []
        for c in calls:
            m = c["method"]
            if m in ("send_message", "edit_message_text"):
                tag = "BOT" if m == "send_message" else "BOT (edit)"
                lines.append(f"  {tag}: {c['text']!r}")
                kb = c.get("reply_markup")
                if kb is not None:
                    for row in kb.inline_keyboard:
                        btns = " | ".join(
                            f"[{b.text} → {b.callback_data}]" for b in row
                        )
                        lines.append(f"       buttons: {btns}")
            elif m == "send_document":
                content = c.get("content") or b""
                name = c.get("filename")
                lines.append(f"  BOT: 📎 document {name} ({len(content)} bytes)")
                # For CSVs, show the contents so the report is reviewable inline.
                if name and name.endswith(".csv"):
                    for row in content.decode("utf-8-sig").splitlines():
                        lines.append(f"       | {row}")
            elif m == "send_chat_action":
                lines.append(f"  BOT: …{c.get('action')}")
            elif m == "answer_callback_query":
                lines.append("  BOT: (ack button tap)")
        return lines

    def clear(self) -> None:
        self.calls.clear()


class FakeBot:
    """Duck-typed stand-in for telegram.Bot. Records outbound calls; no network.

    Not a Bot subclass on purpose — handlers only reach it via `get_bot()`, and
    PTB's `process_update` does no isinstance(Bot) work that matters here. Avoids
    Bot.__init__'s token validation.
    """

    def __init__(self, outbox: Outbox) -> None:
        self.outbox = outbox
        self.id = BOT_ID
        self.username = "scouting_test_bot"  # CommandHandler reads bot.username
        self.name = "@scouting_test_bot"
        self.defaults = None  # Message._quote checks hasattr(bot, "defaults")
        self._files: dict[str, bytes] = {}

    def stage_file(self, file_id: str, content: bytes) -> None:
        self._files[file_id] = content

    # — lifecycle (no-ops: this is what keeps initialize() off the network) —
    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    # — outbound —
    async def send_message(self, chat_id, text, *, reply_markup=None, **kw):
        self.outbox.record(
            "send_message", chat_id=chat_id, text=text, reply_markup=reply_markup
        )
        return _CannedMessage()

    async def send_chat_action(self, chat_id, action, **kw):
        self.outbox.record("send_chat_action", chat_id=chat_id, action=action)
        return True

    async def send_document(self, chat_id, document, *, filename=None, **kw):
        # `document` is a telegram.InputFile; its bytes live in input_file_content.
        content = getattr(document, "input_file_content", None)
        name = filename or getattr(document, "filename", None)
        self.outbox.record(
            "send_document", chat_id=chat_id, filename=name, content=content
        )
        return _CannedMessage()

    async def answer_callback_query(self, callback_query_id, **kw):
        self.outbox.record("answer_callback_query", callback_query_id=callback_query_id)
        return True

    async def edit_message_text(self, text, *, reply_markup=None, **kw):
        self.outbox.record(
            "edit_message_text", text=text, reply_markup=reply_markup
        )
        return _CannedMessage()

    async def get_file(self, file_id, **kw):
        return FakeFile(file_id, self._files.get(file_id, b"\xff\xd8fake"))


class _CannedMessage:
    """Minimal object returned by send_* — nothing in the bot reads its fields."""

    message_id = 9999


# ── Update-dict builders (dict → Update.de_json, exactly like app.py) ───────────
def message_update(
    *,
    update_id: int,
    message_id: int,
    text: str | None = None,
    photo: bool = False,
    voice: bool = False,
) -> dict:
    msg: dict = {
        "message_id": message_id,
        "date": _DATE,
        "chat": {"id": CHAT_ID, "type": "private"},
        "from": {"id": USER_ID, "is_bot": False, "first_name": "Scout"},
    }
    if text is not None:
        msg["text"] = text
        if text.startswith("/"):
            cmd_len = len(text.split()[0])
            msg["entities"] = [{"type": "bot_command", "offset": 0, "length": cmd_len}]
    if photo:
        msg["photo"] = [
            {"file_id": "ph_s", "file_unique_id": "u_s", "width": 90, "height": 60},
            {"file_id": "ph_l", "file_unique_id": "u_l", "width": 1280, "height": 720},
        ]
    if voice:
        msg["voice"] = {
            "file_id": "vo_1",
            "file_unique_id": "u_v",
            "duration": 3,
            "mime_type": "audio/ogg",
        }
    return {"update_id": update_id, "message": msg}


def callback_update(
    *, update_id: int, callback_id: str, message_id: int, data: str
) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": callback_id,
            "from": {"id": USER_ID, "is_bot": False, "first_name": "Scout"},
            "chat_instance": "ci_1",
            "data": data,
            "message": {
                "message_id": message_id,
                "date": _DATE,
                "chat": {"id": CHAT_ID, "type": "private"},
                "from": {"id": BOT_ID, "is_bot": True, "first_name": "Bot"},
            },
        },
    }


class Harness:
    """Drives the webhook and exposes the outbox. Auto-increments ids.

    Transcript mode: when `transcript=True` (auto-enabled under `pytest -s`), each
    inbound action prints a `USER:` line and the bot's responses, so a human can
    read the conversation. It's purely a side-channel print — assertions are
    unaffected, and it stays silent under a plain `pytest` run.
    """

    def __init__(
        self,
        client,
        outbox: Outbox,
        fake_bot: FakeBot,
        token: str,
        *,
        transcript: bool = False,
    ) -> None:
        self.client = client
        self.outbox = outbox
        self.bot = fake_bot
        self.token = token
        self.transcript = transcript
        self._uid = 0
        self._mid = 1000
        self._cid = 0
        self._printed = 0  # how many outbox calls already printed

    def _print_responses(self, inbound: str) -> None:
        if not self.transcript:
            return
        print(f"\nUSER: {inbound}")
        # The tests call outbox.clear() between steps; if that happened, the list
        # is shorter than our cursor, so reset and print whatever is there now.
        if len(self.outbox.calls) < self._printed:
            self._printed = 0
        new = self.outbox.calls[self._printed:]
        for line in Outbox.format_calls(new):
            print(line)
        self._printed = len(self.outbox.calls)

    async def _post(self, update: dict):
        resp = await self.client.post(f"/telegram/{self.token}", json=update)
        assert resp.status_code == 200, (resp.status_code, resp.text)
        return resp

    async def send_text(self, text: str):
        self._uid += 1
        self._mid += 1
        await self._post(
            message_update(update_id=self._uid, message_id=self._mid, text=text)
        )
        self._print_responses(text)

    async def send_photo(self):
        self._uid += 1
        self._mid += 1
        await self._post(
            message_update(update_id=self._uid, message_id=self._mid, photo=True)
        )
        self._print_responses("[photo of lineup]")

    async def send_voice(self):
        self._uid += 1
        self._mid += 1
        await self._post(
            message_update(update_id=self._uid, message_id=self._mid, voice=True)
        )
        self._print_responses("[voice note]")

    async def tap_button(self, data: str, *, message_id: int | None = None):
        self._uid += 1
        self._cid += 1
        await self._post(
            callback_update(
                update_id=self._uid,
                callback_id=f"cb_{self._cid}",
                message_id=message_id or self._mid,
                data=data,
            )
        )
        self._print_responses(f"[taps button → {data}]")

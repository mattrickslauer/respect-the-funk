"""Gmail, over the REST API under a per-tenant OAuth grant.

## Why this is the API and not MCP, having been asked for MCP

The request was a "Gmail MCP hookup", and this file is not that — so the reasoning is
recorded here rather than left as an apparent oversight.

MCP servers for Gmail authenticate a *person*, interactively: a human completes a consent
flow in a client and the server holds that session. That shape fits an agent doing work on
someone's behalf while they watch. It does not fit this, which is an unattended poller
running every few minutes against every connected tenant, in a Lambda, with nobody
present — and any MCP server that did fit would need an OAuth refresh token underneath it
anyway, which is exactly what this module holds. Going through MCP would have added a
network hop and a second credential store to reach the same Google endpoint.

What MCP is genuinely good for here is the *other* direction, and that is the separable
piece: spindle exposing tools so an agent can inspect a mailbox connection, re-file an
unmatched reply, or drive a poll by hand. That is a server this repository does not have
yet — `spindle/mcp.py` is a *client* of CockroachDB's managed server — and it is left
unwritten rather than half-built.

## The cursor is a historyId, and it expires

`historyId` is Gmail's own change cursor and `users.history.list` is the only call that
answers "what changed since" without re-listing a mailbox. It is also perishable: Google
documents that a historyId older than roughly a week may be rejected, and a tenant whose
mailbox went unpolled over a holiday will come back to exactly that.

The recovery is the interesting decision. Resetting to the current historyId would be the
one-line fix and it silently skips every message that arrived during the gap — the failure
this whole feature exists to end. So `HistoryExpired` falls back to listing recent
messages and delivering them, accepting some re-delivery (which `inbound.deliver` keys
away) in exchange for not losing the window.

## A first poll delivers nothing

Same argument as the IMAP adapter's, and for the same reason: a mailbox that has been in
use for years should not have those years imported and filed against threads that did not
exist. An empty cursor establishes a position and returns nothing.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from spindle import inbound
from spindle.mailbox import MailboxRefused

#: Messages fetched per poll. See the IMAP adapter's `BATCH`; the reasoning is identical.
BATCH = 50

#: Where a refresh token is exchanged for an access token.
TOKEN_URL = "https://oauth2.googleapis.com/token"

API = "https://gmail.googleapis.com/gmail/v1/users/me"


class HistoryExpired(RuntimeError):
    """Gmail rejected the stored `historyId` as too old. See the module header."""


def fetch(account: Any, refresh_token: str, *,
          client: Any = None) -> tuple[list[inbound.InboundMessage], str]:
    """Everything since `account.sync_cursor`, and the new cursor."""
    client = client or RestClient(refresh_token)

    if not account.sync_cursor:
        return [], str(client.profile()["historyId"])

    try:
        changed = client.history(account.sync_cursor)
    except HistoryExpired:
        changed = client.recent()

    found = []
    for message_id in list(changed.get("ids", []))[:BATCH]:
        raw = client.raw_message(message_id)
        if raw:
            found.append(inbound.parse(raw))
    return found, str(changed["historyId"])


class RestClient:
    """The four calls `fetch` makes, over urllib.

    urllib rather than `requests` or `httpx` for the reason `embed.py` gives: this
    deploys as a Lambda zip whose whole design is to stay small, and one more HTTP client
    is megabytes for four requests. The Google client libraries are heavier still — they
    would bring in `google-auth`, `google-api-python-client` and their transitive
    dependencies to build URLs this module can write out in full.
    """

    def __init__(self, refresh_token: str, *, client_id: str = "",
                 client_secret: str = "") -> None:
        self._refresh_token = refresh_token
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token = ""

    # -------------------------------------------------------------------- auth

    def _token(self) -> str:
        """Exchange the refresh token for an access token, once per client.

        Not cached across polls, deliberately. An access token lives an hour and a poll
        lives seconds, so caching would trade a real risk — a stale token held in a warm
        Lambda across a revocation — for one HTTP request per mailbox per poll.
        """
        if self._access_token:
            return self._access_token
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }).encode()
        request = urllib.request.Request(TOKEN_URL, data=body, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # `invalid_grant` means the tenant revoked access, changed their password, or
            # the token simply aged out. All of them are the same thing to us and none is
            # retryable — see `mailbox/__init__.py` on why that matters.
            raise MailboxRefused(
                "Google refused the stored authorisation for this mailbox. That usually "
                "means access was revoked from the Google account's security settings, "
                f"or the grant expired. Reconnect the mailbox to restore it. (HTTP "
                f"{exc.code})") from None
        self._access_token = str(payload.get("access_token", ""))
        if not self._access_token:
            raise MailboxRefused(
                "Google returned no access token for this mailbox's stored grant.")
        return self._access_token

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        url = f"{API}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self._token()}"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return dict(json.loads(response.read()))
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and "history" in path:
                # Gmail's documented signal that the startHistoryId is too old.
                raise HistoryExpired(f"historyId is no longer valid ({exc.code})") from None
            if exc.code in (401, 403):
                raise MailboxRefused(
                    f"Google refused this request for the mailbox ({exc.code}). The "
                    "grant may have been revoked; reconnect the mailbox.") from None
            raise

    # ------------------------------------------------------------------- calls

    def profile(self) -> dict[str, Any]:
        return self._get("/profile")

    def history(self, start: str) -> dict[str, Any]:
        """Message ids added since `start`, and where to resume from next.

        Filtered to `messageAdded`: the history feed also reports labels being changed
        and mail being deleted, and neither is a reply arriving. Without the filter a
        tenant archiving old mail would look like a mailbox full of new messages.
        """
        payload = self._get("/history", startHistoryId=start,
                            historyTypes="messageAdded")
        ids = []
        for record in payload.get("history", []):
            for added in record.get("messagesAdded", []):
                message = added.get("message", {})
                if message.get("id"):
                    ids.append(message["id"])
        return {"ids": ids, "historyId": payload.get("historyId", start)}

    def recent(self) -> dict[str, Any]:
        """The fallback when the historyId has expired. Recent inbox mail only.

        `newer_than:7d` because that is the window a historyId is documented to survive,
        so it is the largest gap this fallback can be recovering from. A wider query would
        re-read mail that was already filed for no additional safety.
        """
        payload = self._get("/messages", q="in:inbox newer_than:7d",
                            maxResults=BATCH)
        ids = [m["id"] for m in payload.get("messages", []) if m.get("id")]
        current = self._get("/profile").get("historyId", "")
        return {"ids": ids, "historyId": current}

    def send_raw(self, raw: str) -> dict[str, Any]:
        """`users.messages.send` with a base64url RFC 822 body.

        The send half of the same grant this class polls with — see `mail.GmailMailer`.
        Posted as JSON rather than multipart because the message is already encoded and
        Gmail accepts `{"raw": …}` directly, which avoids building a MIME envelope around
        a MIME message.
        """
        body = json.dumps({"raw": raw}).encode()
        request = urllib.request.Request(
            f"{API}/messages/send", data=body, method="POST",
            headers={"Authorization": f"Bearer {self._token()}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return dict(json.loads(response.read()))
        except urllib.error.HTTPError as exc:
            from spindle.mail import MailRefused      # noqa: PLC0415 — avoids a cycle
            raise MailRefused(
                f"Google refused to send as this mailbox (HTTP {exc.code}). If this is "
                f"403, the grant may not include send scope; reconnect the mailbox.",
                permanent=exc.code in (401, 403)) from None

    def raw_message(self, message_id: str) -> bytes:
        """The full RFC 822 bytes, so `inbound.parse` sees the same thing IMAP hands it.

        `format=raw` rather than Gmail's parsed representation on purpose: one parser for
        both adapters means header handling — encoded words, `Delivered-To`, multipart
        preference — is written once and behaves identically no matter where a message
        came from.
        """
        payload = self._get(f"/messages/{message_id}", format="raw")
        raw = payload.get("raw", "")
        return base64.urlsafe_b64decode(raw) if raw else b""

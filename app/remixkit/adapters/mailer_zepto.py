"""ZeptoMail over SMTP.

Chosen because the label already sends from `agfarms.dev` through ZeptoMail, so the
sending domain is verified and the SPF/DKIM records exist — a sign-in code from a fresh
domain lands in spam, which looks exactly like a broken login.

`smtplib` from the standard library rather than a client package: this sends one short
message, and adding a dependency to the API container to do it would be the wrong
trade. Note that ZeptoMail's SMTP password *is* the "send mail token" — the same secret
authenticates the HTTP API (`Authorization: Zoho-enczapikey <token>`) if a deploy target
ever blocks outbound 465.

The connection is opened per send. That is fine at sign-in volume and avoids holding a
socket open across a Lambda freeze, where a pooled transport would be resumed dead.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

log = logging.getLogger(__name__)


class ZeptoMailer:
    name = "zeptomail"

    def __init__(
        self,
        *,
        token: str,
        sender: str,
        sender_name: str = "",
        host: str = "smtp.zeptomail.com",
        port: int = 465,
        user: str = "emailapikey",
        timeout_s: float = 15.0,
    ) -> None:
        if not token:
            raise RuntimeError("mail_backend=zeptomail requires RK_ZEPTOMAIL_TOKEN")
        if not sender:
            raise RuntimeError("mail_backend=zeptomail requires RK_MAIL_FROM")
        self._token = token
        self._sender = sender
        self._sender_name = sender_name
        self._host = host
        self._port = port
        self._user = user
        self._timeout_s = timeout_s

    def send(self, *, to: str, subject: str, text: str, html: str | None = None) -> bool:
        message = EmailMessage()
        message["From"] = formataddr((self._sender_name or None, self._sender))
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        if html:
            message.add_alternative(html, subtype="html")

        try:
            # 465 is implicit TLS; 587 would need starttls() on a plain SMTP session.
            if self._port == 465:
                with smtplib.SMTP_SSL(self._host, self._port, timeout=self._timeout_s) as smtp:
                    smtp.login(self._user, self._token)
                    smtp.send_message(message)
            else:
                with smtplib.SMTP(self._host, self._port, timeout=self._timeout_s) as smtp:
                    smtp.starttls()
                    smtp.login(self._user, self._token)
                    smtp.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            # Not fatal, and deliberately not re-raised: a refused send is an outcome of
            # a sign-in attempt. The caller reports "we could not send it" rather than
            # a 500, and the code is still in the log for a developer to read.
            log.error("ZeptoMail send to %s failed: %s", to, exc)
            return False

        # Recipient only. The subject line carries the sign-in code — "088814 is your
        # RemixKit sign-in code" — so logging it would put every live code into
        # CloudWatch, where it outlives its ten-minute window and is readable by anyone
        # with log access. The console fallback in `services.accounts` logs codes on
        # purpose and only when nothing was sent; this path must not.
        log.info("sent mail to %s via ZeptoMail", to)
        return True

    def verify(self) -> tuple[bool, str]:
        """Log in without sending — the check `scripts`/ops want before trusting a deploy."""
        try:
            if self._port == 465:
                with smtplib.SMTP_SSL(self._host, self._port, timeout=self._timeout_s) as smtp:
                    smtp.login(self._user, self._token)
            else:
                with smtplib.SMTP(self._host, self._port, timeout=self._timeout_s) as smtp:
                    smtp.starttls()
                    smtp.login(self._user, self._token)
        except (smtplib.SMTPException, OSError) as exc:
            return False, f"{type(exc).__name__}: {exc}"
        return True, f"SMTP login ok as {self._sender} via {self._host}:{self._port}"

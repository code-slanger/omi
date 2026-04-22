"""
IMAP email reader — fetches recent unread emails for the daily digest.

Reads from configured IMAP server (same credentials as SMTP by default).
Set IMAP_HOST / IMAP_PORT in .env if different from SMTP.
"""
from __future__ import annotations

import asyncio
import email
import imaplib
import logging
from dataclasses import dataclass
from email.header import decode_header

from ..config import settings
from ..llm.client import get_client
from .config import EmailConfig

logger = logging.getLogger(__name__)

_SUMMARIZE_SYSTEM = (
    "Summarize the following emails in a concise bullet list. "
    "Each bullet should be one line: sender name, subject, and the key point or action needed. "
    "Group by urgency if obvious. Be direct — no preamble."
)


@dataclass
class EmailItem:
    subject: str
    sender: str
    snippet: str
    uid: str


async def fetch_emails(cfg: EmailConfig) -> list[EmailItem]:
    """Fetch recent unread emails from IMAP and return them."""
    if not (settings.imap_host and settings.smtp_user and settings.smtp_password):
        logger.debug("IMAP not configured — skipping email fetch")
        return []

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_sync, cfg)


async def summarize_emails(items: list[EmailItem]) -> str:
    """Produce a single LLM summary of all fetched emails."""
    if not items:
        return ""
    text = "\n\n".join(
        f"From: {e.sender}\nSubject: {e.subject}\n{e.snippet}" for e in items
    )
    client = get_client("summarize")
    try:
        return await client.complete(_SUMMARIZE_SYSTEM, text[:12000], max_tokens=512)
    except Exception as exc:
        logger.warning(f"Email summarize failed: {exc}")
        return "\n".join(f"- {e.sender}: {e.subject}" for e in items)


def _fetch_sync(cfg: EmailConfig) -> list[EmailItem]:
    host = settings.imap_host or settings.smtp_host
    port = settings.imap_port

    try:
        mail = imaplib.IMAP4_SSL(host, port)
        mail.login(settings.smtp_user, settings.smtp_password)
    except Exception as e:
        logger.error(f"IMAP login failed: {e}")
        return []

    items: list[EmailItem] = []
    try:
        for folder in cfg.folders:
            try:
                mail.select(folder)
                _, data = mail.search(None, "UNSEEN")
                uids = (data[0] or b"").split()[-cfg.max_emails :]
                for uid in uids:
                    item = _fetch_message(mail, uid.decode())
                    if item:
                        items.append(item)
            except Exception as e:
                logger.warning(f"IMAP folder {folder} error: {e}")
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    return items


def _fetch_message(mail: imaplib.IMAP4_SSL, uid: str) -> EmailItem | None:
    try:
        _, data = mail.fetch(uid, "(RFC822)")
        raw = data[0][1] if data and data[0] else None
        if not raw:
            return None
        msg = email.message_from_bytes(raw)

        subject = _decode_header_str(msg.get("Subject", ""))
        sender = _decode_header_str(msg.get("From", ""))
        body = _extract_body(msg)

        return EmailItem(subject=subject, sender=sender, snippet=body[:500], uid=uid)
    except Exception as e:
        logger.warning(f"Failed to fetch message {uid}: {e}")
        return None


def _decode_header_str(value: str) -> str:
    parts = decode_header(value)
    result = []
    for b, enc in parts:
        if isinstance(b, bytes):
            result.append(b.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(b)
    return " ".join(result)


def _extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    return payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            return payload.decode("utf-8", errors="replace")
    return ""

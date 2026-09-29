"""Optional WhatsApp alerting.

Best-effort, fire-and-forget push of critical alerts to WhatsApp. Fully disabled
unless QT_WHATSAPP_ENABLED=true AND the selected provider's secrets are present
in the environment. Secrets are read from the environment only — never hard-coded
or logged.

Providers (QT_WHATSAPP_PROVIDER):
  meta   — Meta WhatsApp Cloud API. Needs WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID,
           WHATSAPP_TO (recipient in international format, e.g. 919876543210).
  twilio — Twilio WhatsApp. Needs TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
           TWILIO_WHATSAPP_FROM (e.g. whatsapp:+14155238886), WHATSAPP_TO.
"""
from __future__ import annotations

import os
import threading

import httpx

from app.config import settings


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _meta_credentials() -> tuple[str, str, str] | None:
    token = _env("WHATSAPP_TOKEN")
    phone_id = _env("WHATSAPP_PHONE_NUMBER_ID")
    to = _env("WHATSAPP_TO")
    if not token or not phone_id or not to:
        return None
    return token, phone_id, to


def _twilio_credentials() -> tuple[str, str, str, str] | None:
    sid = _env("TWILIO_ACCOUNT_SID")
    auth = _env("TWILIO_AUTH_TOKEN")
    sender = _env("TWILIO_WHATSAPP_FROM")
    to = _env("WHATSAPP_TO")
    if not sid or not auth or not sender or not to:
        return None
    return sid, auth, sender, to


def enabled() -> bool:
    if not settings.whatsapp_enabled:
        return False
    if settings.whatsapp_provider.lower() == "twilio":
        return _twilio_credentials() is not None
    return _meta_credentials() is not None


def _send_meta(token: str, phone_id: str, to: str, text: str) -> None:
    try:
        httpx.post(
            f"https://graph.facebook.com/v20.0/{phone_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": text},
            },
            timeout=6.0,
        )
    except Exception:
        # Alerting must never break the trading loop.
        pass


def _send_twilio(sid: str, auth: str, sender: str, to: str, text: str) -> None:
    recipient = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
    try:
        httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            auth=(sid, auth),
            data={"From": sender, "To": recipient, "Body": text},
            timeout=6.0,
        )
    except Exception:
        pass


def push(text: str) -> None:
    if not settings.whatsapp_enabled:
        return
    if settings.whatsapp_provider.lower() == "twilio":
        creds = _twilio_credentials()
        if creds is None:
            return
        sid, auth, sender, to = creds
        threading.Thread(target=_send_twilio, args=(sid, auth, sender, to, text), daemon=True).start()
        return
    creds = _meta_credentials()
    if creds is None:
        return
    token, phone_id, to = creds
    threading.Thread(target=_send_meta, args=(token, phone_id, to, text), daemon=True).start()

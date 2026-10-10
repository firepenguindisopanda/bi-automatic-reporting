"""HTTP-API providers: SendGrid and Resend.

Both are plain REST calls over the shared httpx client - no vendor SDK, so
neither is a hard dependency of the image. SendGrid stays available while its
trial lasts; Resend is here as a drop-in alternative.
"""

import base64
import logging
from email.utils import parseaddr
from typing import Any

import httpx

from app.config import settings
from app.email.message import DeliveryResult, EmailMessage, ProviderHealth
from app.email.providers.base import EmailProvider

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(30.0)

# 4xx other than 408/429 means the request itself is wrong; retrying repeats it.
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _sendgrid_address(from_email: str) -> dict[str, str]:
    """SendGrid takes the display name as its own field, not as `Name <addr>`."""
    name, address = parseaddr(from_email)
    return {"email": address or from_email, **({"name": name} if name else {})}


class SendGridProvider(EmailProvider):
    name = "sendgrid"
    endpoint = "https://api.sendgrid.com/v3/mail/send"

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key or settings.sendgrid_api_key

    def _payload(self, msg: EmailMessage, from_email: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "personalizations": [{"to": [{"email": addr} for addr in msg.to]}],
            "from": _sendgrid_address(from_email),
            "subject": msg.subject,
            "content": [
                {"type": "text/plain", "value": msg.text},
                {"type": "text/html", "value": msg.html},
            ],
        }
        if msg.reply_to:
            payload["reply_to"] = {"email": msg.reply_to}
        if msg.attachments:
            payload["attachments"] = [
                {
                    "content": _b64(a.content),
                    "filename": a.filename,
                    "type": a.mime_type,
                    "disposition": "attachment",
                }
                for a in msg.attachments
            ]
        return payload

    async def send(self, msg: EmailMessage, from_email: str) -> DeliveryResult:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.post(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=self._payload(msg, from_email),
                )
        except httpx.HTTPError as e:
            return DeliveryResult.failure(self.name, f"SendGrid request failed: {e}", retryable=True)

        if 200 <= resp.status_code < 300:
            return DeliveryResult.success(
                self.name, resp.headers.get("X-Message-Id"), resp.status_code
            )
        return DeliveryResult.failure(
            self.name,
            f"SendGrid returned {resp.status_code}: {resp.text[:300]}",
            retryable=resp.status_code in RETRYABLE_STATUS,
            status_code=resp.status_code,
        )

    async def verify(self) -> ProviderHealth:
        if not self.api_key:
            return ProviderHealth(ok=False, detail="SENDGRID_API_KEY is not set")
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.get(
                    "https://api.sendgrid.com/v3/scopes",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            if resp.status_code == 401:
                return ProviderHealth(ok=False, detail="SendGrid rejected the API key")
            return ProviderHealth(ok=resp.status_code < 500, detail=f"HTTP {resp.status_code}")
        except httpx.HTTPError as e:
            return ProviderHealth(ok=False, detail=str(e))


class ResendProvider(EmailProvider):
    name = "resend"
    endpoint = "https://api.resend.com/emails"

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key or settings.resend_api_key

    async def send(self, msg: EmailMessage, from_email: str) -> DeliveryResult:
        payload: dict[str, Any] = {
            "from": from_email,
            "to": msg.to,
            "subject": msg.subject,
            "html": msg.html,
            "text": msg.text,
        }
        if msg.reply_to:
            payload["reply_to"] = msg.reply_to
        if msg.attachments:
            payload["attachments"] = [
                {"filename": a.filename, "content": _b64(a.content)} for a in msg.attachments
            ]

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.post(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
        except httpx.HTTPError as e:
            return DeliveryResult.failure(self.name, f"Resend request failed: {e}", retryable=True)

        if 200 <= resp.status_code < 300:
            message_id = None
            try:
                message_id = resp.json().get("id")
            except ValueError:
                pass
            return DeliveryResult.success(self.name, message_id, resp.status_code)
        return DeliveryResult.failure(
            self.name,
            f"Resend returned {resp.status_code}: {resp.text[:300]}",
            retryable=resp.status_code in RETRYABLE_STATUS,
            status_code=resp.status_code,
        )

    async def verify(self) -> ProviderHealth:
        if not self.api_key:
            return ProviderHealth(ok=False, detail="RESEND_API_KEY is not set")
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.get(
                    "https://api.resend.com/domains",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            if resp.status_code == 401:
                return ProviderHealth(ok=False, detail="Resend rejected the API key")
            return ProviderHealth(ok=resp.status_code < 500, detail=f"HTTP {resp.status_code}")
        except httpx.HTTPError as e:
            return ProviderHealth(ok=False, detail=str(e))

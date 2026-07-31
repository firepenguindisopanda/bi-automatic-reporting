"""Providers that need no credentials.

`console` logs the message; `file` writes a .eml you can open in a mail client.
Both let the full pipeline - including delivery - run in development and in CI
without a mail account, and without silently pretending mail was sent.
"""

import logging
from pathlib import Path

from app.config import settings
from app.email.message import DeliveryResult, EmailMessage, ProviderHealth
from app.email.providers.base import EmailProvider, build_mime

logger = logging.getLogger(__name__)


class ConsoleProvider(EmailProvider):
    name = "console"

    async def send(self, msg: EmailMessage, from_email: str) -> DeliveryResult:
        logger.info(
            "[console email] from=%s to=%s subject=%r attachments=%s",
            from_email,
            msg.to,
            msg.subject,
            [f"{a.filename} ({a.size_bytes} bytes)" for a in msg.attachments],
        )
        return DeliveryResult.success(self.name, message_id="console")

    async def verify(self) -> ProviderHealth:
        return ProviderHealth(ok=True, detail="logs messages instead of sending")


class FileProvider(EmailProvider):
    """Writes each message as .eml under REPORT_OUTPUT_DIR/outbox."""

    name = "file"

    def __init__(self, outbox: Path | None = None) -> None:
        self.outbox = outbox or Path(settings.report_output_dir) / "outbox"

    async def send(self, msg: EmailMessage, from_email: str) -> DeliveryResult:
        try:
            self.outbox.mkdir(parents=True, exist_ok=True)
            # Content-addressed so repeated sends in one test do not collide.
            stem = "".join(c if c.isalnum() else "-" for c in msg.subject)[:60]
            path = self.outbox / f"{stem}-{abs(hash((tuple(msg.to), msg.subject)))}.eml"
            path.write_bytes(bytes(build_mime(msg, from_email)))
            logger.info("Wrote message to %s", path)
            return DeliveryResult.success(self.name, message_id=str(path))
        except OSError as e:
            return DeliveryResult.failure(self.name, f"Could not write message: {e}")

    async def verify(self) -> ProviderHealth:
        try:
            self.outbox.mkdir(parents=True, exist_ok=True)
            return ProviderHealth(ok=True, detail=f"writing to {self.outbox}")
        except OSError as e:
            return ProviderHealth(ok=False, detail=str(e))

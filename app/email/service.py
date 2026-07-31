"""Report delivery.

Builds the message, hands it to whichever provider is configured, and retries
transient failures with backoff. Which provider that is comes entirely from
EMAIL_PROVIDER - no code path here knows or cares.
"""

import asyncio
import logging
import mimetypes
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.config import settings
from app.email import templates
from app.email.message import DeliveryResult, EmailAttachment, EmailMessage
from app.email.providers.base import EmailProvider
from app.email.registry import build_provider

logger = logging.getLogger(__name__)

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

BACKOFF_SECONDS = (2.0, 8.0)
"""Delay before retry 2 and retry 3. Only used for retryable failures."""


def _attachment(path: Path) -> EmailAttachment:
    guessed, _ = mimetypes.guess_type(path.name)
    mime = guessed or (PDF_MIME if path.suffix == ".pdf" else DOCX_MIME)
    return EmailAttachment(filename=path.name, content=path.read_bytes(), mime_type=mime)


class EmailService:
    def __init__(self, provider: EmailProvider | None = None) -> None:
        self._from_email = settings.from_email
        self._provider = provider or build_provider()

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def build_report_message(
        self, to_email: str, company_name: str, pdf_path: Path, docx_path: Path | None = None
    ) -> EmailMessage:
        attachments = [_attachment(pdf_path)]
        if docx_path is not None and docx_path.exists():
            attachments.append(_attachment(docx_path))
        return EmailMessage(
            to=[to_email],
            subject=templates.report_subject(company_name),
            html=templates.report_html(company_name),
            text=templates.report_text(company_name),
            attachments=attachments,
        )

    async def send(self, msg: EmailMessage, max_attempts: int | None = None) -> DeliveryResult:
        """Send with backoff on retryable failures.

        A permanent failure - bad credentials, refused recipient, oversized
        attachment - returns immediately rather than repeating itself.
        """
        attempts = max_attempts or settings.email_max_attempts
        result = DeliveryResult.failure(self._provider.name, "not attempted")

        for attempt in range(1, attempts + 1):
            try:
                result = await self._provider.send(msg, self._from_email)
            except Exception as e:  # noqa: BLE001 - a provider bug is still a delivery failure
                logger.exception("Provider %s raised", self._provider.name)
                result = DeliveryResult.failure(self._provider.name, str(e), retryable=True)

            if result.ok or not result.retryable or attempt == attempts:
                return replace(result, attempts=attempt)

            delay = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
            logger.warning(
                "Delivery attempt %d/%d failed (%s); retrying in %.0fs",
                attempt, attempts, result.error, delay,
            )
            await asyncio.sleep(delay)

        return replace(result, attempts=attempts)

    async def send_report(
        self,
        to_email: str,
        company_name: str,
        pdf_path: Path,
        docx_path: Path | None = None,
    ) -> DeliveryResult:
        return await self.send(
            self.build_report_message(to_email, company_name, pdf_path, docx_path)
        )

    async def send_selftest(self, to_email: str) -> DeliveryResult:
        """One test message, so a provider switch can be verified in a minute."""
        return await self.send(
            EmailMessage(
                to=[to_email],
                subject=templates.selftest_subject(),
                html=templates.selftest_html(self._provider.name),
                text=templates.selftest_text(self._provider.name),
            ),
            max_attempts=1,
        )

    async def verify(self) -> dict[str, Any]:
        """Whether the provider looks usable. Sends nothing."""
        try:
            health = await self._provider.verify()
            return {"ok": health.ok, "provider": self._provider.name, "detail": health.detail}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "provider": self._provider.name, "detail": str(e)}

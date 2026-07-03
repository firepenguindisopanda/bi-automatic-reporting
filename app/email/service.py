"""Email service for delivering BI reports.

Selects the email provider via EMAIL_PROVIDER in .env:
  - "sendgrid" uses SendGrid API (requires SENDGRID_API_KEY)
  - "smtp" uses localhost:25 (development only)
  - any other value defaults to "smtp"
"""

import logging
from pathlib import Path

from app.config import settings
from app.email.providers.base import ProviderBase
from app.email.providers.smtp_provider import SMTPProvider

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(self) -> None:
        self._from_email = settings.from_email
        self._provider = self._load_provider()

    def _load_provider(self) -> ProviderBase:
        name = settings.email_provider
        if name == "sendgrid":
            try:
                from app.email.providers.sendgrid_provider import SendGridProvider
                if not settings.sendgrid_api_key:
                    logger.warning("EMAIL_PROVIDER=sendgrid but SENDGRID_API_KEY is empty; falling back to SMTP")
                    return SMTPProvider()
                logger.info("Using SendGrid email provider")
                return SendGridProvider(settings.sendgrid_api_key)
            except ImportError:
                logger.warning("sendgrid package not installed; falling back to SMTP")
                return SMTPProvider()
        logger.info("Using SMTP email provider")
        return SMTPProvider()

    async def send_report(
        self,
        to_email: str,
        company_name: str,
        pdf_path: Path,
        docx_path: Path | None = None,
    ) -> bool:
        return await self._provider.send_report(
            to_email=to_email,
            from_email=self._from_email,
            company_name=company_name,
            pdf_path=pdf_path,
            docx_path=docx_path,
        )

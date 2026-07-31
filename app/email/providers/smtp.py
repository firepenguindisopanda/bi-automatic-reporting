"""SMTP with authentication and TLS. The default provider.

Works against anything that speaks SMTP - Gmail app passwords, Zoho, Fastmail,
Mailgun, Brevo, Amazon SES - so switching mail vendors never requires a code
change. This replaces an unauthenticated localhost:25 stub that silently
dropped mail on any host without a local MTA.
"""

import logging

import aiosmtplib

from app.config import settings
from app.email.message import DeliveryResult, EmailMessage, ProviderHealth
from app.email.providers.base import EmailProvider, build_mime

logger = logging.getLogger(__name__)

# Permanent SMTP failures: retrying sends the same message to the same refusal.
PERMANENT_CODES = frozenset({500, 501, 503, 504, 550, 551, 552, 553, 554})


class SMTPProvider(EmailProvider):
    name = "smtp"

    def __init__(
        self,
        host: str = "",
        port: int = 0,
        username: str = "",
        password: str = "",
        security: str = "",
        timeout: int = 0,
    ) -> None:
        self.host = host or settings.smtp_host
        self.port = port or settings.smtp_port
        self.username = username or settings.smtp_username
        self.password = password or settings.smtp_password
        self.security = (security or settings.smtp_security).lower()
        self.timeout = timeout or settings.smtp_timeout

    async def send(self, msg: EmailMessage, from_email: str) -> DeliveryResult:
        limit = settings.email_max_attachment_mb * 1024 * 1024
        if msg.total_attachment_bytes > limit:
            return DeliveryResult.failure(
                self.name,
                f"Attachments total {msg.total_attachment_bytes / 1_048_576:.1f} MB, "
                f"over the {settings.email_max_attachment_mb} MB limit",
            )

        try:
            await aiosmtplib.send(
                build_mime(msg, from_email),
                hostname=self.host,
                port=self.port,
                username=self.username or None,
                password=self.password or None,
                use_tls=self.security == "ssl",
                start_tls=self.security == "starttls",
                timeout=self.timeout,
            )
            logger.info("SMTP delivered to %s via %s:%s", msg.to, self.host, self.port)
            return DeliveryResult.success(self.name)

        except aiosmtplib.SMTPAuthenticationError as e:
            # Bad credentials will not fix themselves on a retry.
            return DeliveryResult.failure(
                self.name, f"SMTP authentication failed: {e}", retryable=False
            )
        except aiosmtplib.SMTPResponseException as e:
            retryable = e.code not in PERMANENT_CODES
            return DeliveryResult.failure(
                self.name, f"SMTP error {e.code}: {e.message}",
                retryable=retryable, status_code=e.code,
            )
        except (aiosmtplib.SMTPException, OSError, TimeoutError) as e:
            return DeliveryResult.failure(
                self.name, f"SMTP connection failed: {e}", retryable=True
            )

    async def verify(self) -> ProviderHealth:
        if not self.host:
            return ProviderHealth(ok=False, detail="SMTP_HOST is not set")
        try:
            client = aiosmtplib.SMTP(
                hostname=self.host,
                port=self.port,
                use_tls=self.security == "ssl",
                start_tls=self.security == "starttls",
                timeout=self.timeout,
            )
            await client.connect()
            if self.username:
                await client.login(self.username, self.password)
            await client.quit()
            return ProviderHealth(ok=True, detail=f"{self.host}:{self.port} ({self.security})")
        except Exception as e:  # noqa: BLE001 - any failure means not ready
            return ProviderHealth(ok=False, detail=str(e))

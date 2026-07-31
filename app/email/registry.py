"""Provider selection.

A misconfigured provider raises at startup rather than degrading silently.
The old behaviour - falling back to unauthenticated localhost:25 whenever
SendGrid was selected without a key - meant mail vanished with one log line
nobody read, which is the worst possible failure mode for delivery.
"""

import logging
from collections.abc import Callable

from app.config import settings
from app.email.message import EmailConfigError
from app.email.providers.base import EmailProvider
from app.email.providers.http_api import ResendProvider, SendGridProvider
from app.email.providers.local import ConsoleProvider, FileProvider
from app.email.providers.smtp import SMTPProvider

logger = logging.getLogger(__name__)

PROVIDERS: dict[str, Callable[[], EmailProvider]] = {
    "smtp": SMTPProvider,
    "sendgrid": SendGridProvider,
    "resend": ResendProvider,
    "console": ConsoleProvider,
    "file": FileProvider,
}

# What each provider cannot work without. Checked once, at construction.
REQUIRED_SETTINGS: dict[str, tuple[str, ...]] = {
    "smtp": ("smtp_host",),
    "sendgrid": ("sendgrid_api_key",),
    "resend": ("resend_api_key",),
}


def build_provider(name: str | None = None) -> EmailProvider:
    """The configured provider. Raises EmailConfigError if it cannot work."""
    key = (name or settings.email_provider).strip().lower()

    factory = PROVIDERS.get(key)
    if factory is None:
        raise EmailConfigError(
            f"Unknown EMAIL_PROVIDER {key!r}. Valid options: {', '.join(sorted(PROVIDERS))}"
        )

    missing = [
        field.upper()
        for field in REQUIRED_SETTINGS.get(key, ())
        if not getattr(settings, field, "")
    ]
    if missing:
        raise EmailConfigError(
            f"EMAIL_PROVIDER={key} requires {', '.join(missing)} to be set. "
            "Set it, or use EMAIL_PROVIDER=console (logs) or file (writes .eml) "
            "for local development."
        )

    logger.info("Using %s email provider", key)
    return factory()

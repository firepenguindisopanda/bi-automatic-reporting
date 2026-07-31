"""Provider-neutral email types.

The previous interface took report-specific arguments, which meant every
provider knew what a BI report was. These types describe an email and nothing
else, so swapping providers is a configuration change.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class EmailAttachment:
    filename: str
    content: bytes
    mime_type: str

    @property
    def size_bytes(self) -> int:
        return len(self.content)


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: list[str]
    subject: str
    html: str
    text: str
    attachments: list[EmailAttachment] = field(default_factory=list)
    reply_to: str | None = None

    @property
    def total_attachment_bytes(self) -> int:
        return sum(a.size_bytes for a in self.attachments)


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    ok: bool
    provider: str
    message_id: str | None = None
    status_code: int | None = None
    error: str | None = None
    retryable: bool = False
    """Whether trying again could plausibly succeed.

    A refused recipient or an oversized attachment will fail identically on
    every attempt; a timeout or a 5xx will not.
    """
    attempts: int = 1
    """How many sends were actually made. Stamped by EmailService.send.

    A permanent failure returns after one attempt, so this is the real count
    rather than the configured maximum.
    """

    @classmethod
    def success(
        cls, provider: str, message_id: str | None = None, status_code: int | None = None
    ) -> "DeliveryResult":
        return cls(ok=True, provider=provider, message_id=message_id, status_code=status_code)

    @classmethod
    def failure(
        cls,
        provider: str,
        error: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> "DeliveryResult":
        return cls(
            ok=False,
            provider=provider,
            error=error,
            retryable=retryable,
            status_code=status_code,
        )


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    ok: bool
    detail: str = ""


class EmailConfigError(RuntimeError):
    """A provider is selected but not usable. Raised at startup, not at send time."""

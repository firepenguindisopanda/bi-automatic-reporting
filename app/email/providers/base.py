"""What every email provider must implement."""

from abc import ABC, abstractmethod
from email.message import EmailMessage as MIMEMessage
from email.utils import formatdate, make_msgid, parseaddr

from app.email.message import DeliveryResult, EmailMessage, ProviderHealth


def _sender_domain(from_email: str) -> str | None:
    """The domain to stamp Message-IDs with, or None to use the hostname."""
    _, address = parseaddr(from_email)
    return address.rpartition("@")[2] or None


def build_mime(msg: EmailMessage, from_email: str) -> MIMEMessage:
    """Render a message as MIME, for providers that speak RFC 5322.

    ``Date`` is mandatory under RFC 5322 and ``Message-ID`` is expected; a
    message missing both scores badly with spam filters and cannot be
    threaded or traced through mail logs. Neither was set previously, which
    only mattered for the SMTP path - the HTTP API providers build their own
    envelope and never come through here.
    """
    mime = MIMEMessage()
    mime["From"] = from_email
    mime["To"] = ", ".join(msg.to)
    mime["Subject"] = msg.subject
    mime["Date"] = formatdate(localtime=True)
    mime["Message-ID"] = make_msgid(domain=_sender_domain(from_email))
    if msg.reply_to:
        mime["Reply-To"] = msg.reply_to
    mime.set_content(msg.text)
    mime.add_alternative(msg.html, subtype="html")
    for attachment in msg.attachments:
        maintype, _, subtype = attachment.mime_type.partition("/")
        mime.add_attachment(
            attachment.content,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=attachment.filename,
        )
    return mime


class EmailProvider(ABC):
    """One way of getting an email out of the building."""

    name: str = "provider"

    @abstractmethod
    async def send(self, msg: EmailMessage, from_email: str) -> DeliveryResult:
        """Deliver the message. Must not raise - failures come back as a result."""

    async def verify(self) -> ProviderHealth:
        """Check credentials and connectivity without sending anything.

        Used by the readiness probe and by the selftest command, so a
        misconfiguration is visible before someone waits five minutes for a
        report that never arrives.
        """
        return ProviderHealth(ok=True, detail="no verification implemented")

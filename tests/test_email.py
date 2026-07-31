"""Tests for the email provider layer.

All of these run without credentials - that is the point of the `file` and
`console` providers.
"""

import email
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import settings
from app.email.message import DeliveryResult, EmailAttachment, EmailConfigError, EmailMessage
from app.email.providers.base import build_mime
from app.email.providers.http_api import ResendProvider, SendGridProvider
from app.email.providers.local import ConsoleProvider, FileProvider
from app.email.providers.smtp import SMTPProvider
from app.email.registry import PROVIDERS, build_provider
from app.email.service import EmailService


@pytest.fixture
def report_files(tmp_path: Path) -> tuple[Path, Path]:
    pdf = tmp_path / "Acme-report.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    docx = tmp_path / "Acme-report.docx"
    docx.write_bytes(b"fake docx")
    return pdf, docx


class TestRegistry:
    def test_all_providers_registered(self) -> None:
        assert set(PROVIDERS) == {"smtp", "sendgrid", "resend", "console", "file"}

    def test_unknown_provider_is_a_startup_error(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "email_provider", "carrier-pigeon")
        with pytest.raises(EmailConfigError, match="Unknown EMAIL_PROVIDER"):
            build_provider()

    def test_sendgrid_without_key_fails_loudly(self, monkeypatch) -> None:
        """The old code silently fell back to unauthenticated localhost:25."""
        monkeypatch.setattr(settings, "email_provider", "sendgrid")
        monkeypatch.setattr(settings, "sendgrid_api_key", "")
        with pytest.raises(EmailConfigError, match="SENDGRID_API_KEY"):
            build_provider()

    def test_smtp_without_host_fails_loudly(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "email_provider", "smtp")
        monkeypatch.setattr(settings, "smtp_host", "")
        with pytest.raises(EmailConfigError, match="SMTP_HOST"):
            build_provider()

    def test_error_suggests_a_credential_free_option(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "email_provider", "resend")
        monkeypatch.setattr(settings, "resend_api_key", "")
        with pytest.raises(EmailConfigError, match="console"):
            build_provider()

    def test_console_needs_no_configuration(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "email_provider", "console")
        assert build_provider().name == "console"

    def test_provider_name_is_case_insensitive(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "email_provider", "  Console ")
        assert build_provider().name == "console"

    def test_switching_provider_is_config_only(self, monkeypatch) -> None:
        """The September migration must not require a code change."""
        monkeypatch.setattr(settings, "email_provider", "sendgrid")
        monkeypatch.setattr(settings, "sendgrid_api_key", "sg-key")
        assert EmailService().provider_name == "sendgrid"

        monkeypatch.setattr(settings, "email_provider", "smtp")
        monkeypatch.setattr(settings, "smtp_host", "smtp.gmail.com")
        assert EmailService().provider_name == "smtp"


class TestFileProvider:
    async def test_writes_a_readable_eml(self, tmp_path: Path, report_files) -> None:
        pdf, docx = report_files
        service = EmailService(provider=FileProvider(outbox=tmp_path / "outbox"))
        result = await service.send_report("user@example.com", "Acme", pdf, docx)

        assert result.ok
        written = list((tmp_path / "outbox").glob("*.eml"))
        assert len(written) == 1

        parsed = email.message_from_bytes(written[0].read_bytes())
        assert parsed["To"] == "user@example.com"
        assert "Acme" in parsed["Subject"]

    async def test_attaches_both_documents(self, tmp_path: Path, report_files) -> None:
        pdf, docx = report_files
        service = EmailService(provider=FileProvider(outbox=tmp_path / "outbox"))
        await service.send_report("user@example.com", "Acme", pdf, docx)

        parsed = email.message_from_bytes(next((tmp_path / "outbox").glob("*.eml")).read_bytes())
        names = {p.get_filename() for p in parsed.walk() if p.get_filename()}
        assert names == {"Acme-report.pdf", "Acme-report.docx"}

    async def test_missing_docx_is_not_fatal(self, tmp_path: Path, report_files) -> None:
        pdf, _ = report_files
        service = EmailService(provider=FileProvider(outbox=tmp_path / "outbox"))
        result = await service.send_report("user@example.com", "Acme", pdf, tmp_path / "nope.docx")
        assert result.ok

    async def test_carries_plain_text_alternative(self, tmp_path: Path, report_files) -> None:
        """A missing text/plain part hurts deliverability."""
        pdf, _ = report_files
        service = EmailService(provider=FileProvider(outbox=tmp_path / "outbox"))
        await service.send_report("user@example.com", "Acme", pdf)

        parsed = email.message_from_bytes(next((tmp_path / "outbox").glob("*.eml")).read_bytes())
        types = {p.get_content_type() for p in parsed.walk()}
        assert "text/plain" in types
        assert "text/html" in types

    async def test_carries_the_required_rfc5322_headers(
        self, tmp_path: Path, report_files
    ) -> None:
        """Date is mandatory and Message-ID expected; without them mail scores
        as spam and cannot be traced through a mail log."""
        pdf, _ = report_files
        service = EmailService(provider=FileProvider(outbox=tmp_path / "outbox"))
        await service.send_report("user@example.com", "Acme", pdf)

        parsed = email.message_from_bytes(
            next((tmp_path / "outbox").glob("*.eml")).read_bytes()
        )
        assert parsed["Date"]
        assert parsedate_to_datetime(parsed["Date"])  # parses as a real date
        assert parsed["Message-ID"].startswith("<")
        assert parsed["Message-ID"].endswith(">")

    async def test_message_id_uses_the_sender_domain(
        self, tmp_path: Path, report_files, monkeypatch
    ) -> None:
        pdf, _ = report_files
        monkeypatch.setattr(settings, "from_email", "reports@bisystem.com")
        service = EmailService(provider=FileProvider(outbox=tmp_path / "outbox"))
        await service.send_report("user@example.com", "Acme", pdf)

        parsed = email.message_from_bytes(
            next((tmp_path / "outbox").glob("*.eml")).read_bytes()
        )
        assert parsed["Message-ID"].endswith("@bisystem.com>")

    async def test_every_message_gets_a_distinct_id(
        self, tmp_path: Path, report_files
    ) -> None:
        pdf, _ = report_files
        seen = {
            build_mime(
                EmailMessage(to=["u@e.com"], subject="s", html="h", text="t"),
                "reports@bisystem.com",
            )["Message-ID"]
            for _ in range(5)
        }
        assert len(seen) == 5

    async def test_verify_reports_the_outbox(self, tmp_path: Path) -> None:
        health = await FileProvider(outbox=tmp_path / "outbox").verify()
        assert health.ok


class TestConsoleProvider:
    async def test_send_succeeds_without_credentials(self, report_files) -> None:
        pdf, _ = report_files
        result = await EmailService(provider=ConsoleProvider()).send_report("u@e.com", "Acme", pdf)
        assert result.ok
        assert result.provider == "console"


class TestSMTPProvider:
    def test_builds_a_multipart_message(self) -> None:
        msg = EmailMessage(
            to=["user@example.com"], subject="Test", html="<p>hi</p>", text="hi",
            attachments=[EmailAttachment("r.pdf", b"%PDF", "application/pdf")],
        )
        mime = build_mime(msg, "from@example.com")
        assert mime["To"] == "user@example.com"
        assert {p.get_filename() for p in mime.walk() if p.get_filename()} == {"r.pdf"}

    async def test_oversized_attachment_is_refused_without_sending(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "email_max_attachment_mb", 1)
        provider = SMTPProvider(host="smtp.example.com")
        msg = EmailMessage(
            to=["u@e.com"], subject="s", html="h", text="t",
            attachments=[EmailAttachment("big.pdf", b"x" * (2 * 1024 * 1024), "application/pdf")],
        )
        result = await provider.send(msg, "from@example.com")
        assert not result.ok
        assert "over the 1 MB limit" in result.error

    async def test_connection_failure_is_retryable(self) -> None:
        provider = SMTPProvider(host="127.0.0.1", port=1, security="none", timeout=1)
        msg = EmailMessage(to=["u@e.com"], subject="s", html="h", text="t")
        result = await provider.send(msg, "from@example.com")
        assert not result.ok
        assert result.retryable

    async def test_verify_fails_without_a_host(self) -> None:
        health = await SMTPProvider(host="").verify()
        assert not health.ok
        assert "SMTP_HOST" in health.detail


class MockTransportProvider:
    """Drives the HTTP providers through httpx.MockTransport."""

    @staticmethod
    def patch(monkeypatch, handler) -> None:
        original = httpx.AsyncClient

        def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(handler)
            return original(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)


class TestHTTPProviders:
    async def test_sendgrid_success(self, monkeypatch) -> None:
        MockTransportProvider.patch(
            monkeypatch,
            lambda r: httpx.Response(202, headers={"X-Message-Id": "abc123"}),
        )
        result = await SendGridProvider(api_key="k").send(
            EmailMessage(to=["u@e.com"], subject="s", html="h", text="t"), "from@e.com"
        )
        assert result.ok
        assert result.message_id == "abc123"

    async def test_sendgrid_401_is_not_retryable(self, monkeypatch) -> None:
        MockTransportProvider.patch(monkeypatch, lambda r: httpx.Response(401, text="bad key"))
        result = await SendGridProvider(api_key="k").send(
            EmailMessage(to=["u@e.com"], subject="s", html="h", text="t"), "from@e.com"
        )
        assert not result.ok
        assert not result.retryable

    async def test_sendgrid_503_is_retryable(self, monkeypatch) -> None:
        MockTransportProvider.patch(monkeypatch, lambda r: httpx.Response(503, text="busy"))
        result = await SendGridProvider(api_key="k").send(
            EmailMessage(to=["u@e.com"], subject="s", html="h", text="t"), "from@e.com"
        )
        assert result.retryable

    async def test_resend_success(self, monkeypatch) -> None:
        MockTransportProvider.patch(monkeypatch, lambda r: httpx.Response(200, json={"id": "re_1"}))
        result = await ResendProvider(api_key="k").send(
            EmailMessage(to=["u@e.com"], subject="s", html="h", text="t"), "from@e.com"
        )
        assert result.ok
        assert result.message_id == "re_1"

    async def test_attachments_are_base64_encoded(self, monkeypatch) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen.update(json.loads(request.content))
            return httpx.Response(202)

        MockTransportProvider.patch(monkeypatch, handler)
        await SendGridProvider(api_key="k").send(
            EmailMessage(
                to=["u@e.com"], subject="s", html="h", text="t",
                attachments=[EmailAttachment("r.pdf", b"%PDF-1.4", "application/pdf")],
            ),
            "from@e.com",
        )
        assert seen["attachments"][0]["content"] == "JVBERi0xLjQ="


class FlakyProvider:
    """Fails a set number of times before succeeding."""

    name = "flaky"

    def __init__(self, failures: int, retryable: bool = True) -> None:
        self.failures = failures
        self.retryable = retryable
        self.attempts = 0

    async def send(self, msg: EmailMessage, from_email: str) -> DeliveryResult:
        self.attempts += 1
        if self.attempts <= self.failures:
            return DeliveryResult.failure(self.name, "temporary", retryable=self.retryable)
        return DeliveryResult.success(self.name, "ok")


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    import app.email.service as service_mod

    monkeypatch.setattr(service_mod, "BACKOFF_SECONDS", (0.0, 0.0))


class TestRetry:
    async def test_retryable_failure_is_retried(self) -> None:
        provider = FlakyProvider(failures=2)
        result = await EmailService(provider=provider).send(
            EmailMessage(to=["u@e.com"], subject="s", html="h", text="t")
        )
        assert result.ok
        assert provider.attempts == 3

    async def test_permanent_failure_is_not_retried(self) -> None:
        """Bad credentials fail identically every time; repeating wastes minutes."""
        provider = FlakyProvider(failures=99, retryable=False)
        result = await EmailService(provider=provider).send(
            EmailMessage(to=["u@e.com"], subject="s", html="h", text="t")
        )
        assert not result.ok
        assert provider.attempts == 1

    async def test_attempts_are_capped(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "email_max_attempts", 3)
        provider = FlakyProvider(failures=99)
        result = await EmailService(provider=provider).send(
            EmailMessage(to=["u@e.com"], subject="s", html="h", text="t")
        )
        assert not result.ok
        assert provider.attempts == 3

    async def test_provider_exception_becomes_a_failure(self) -> None:
        class Exploding:
            name = "boom"

            async def send(self, msg: Any, from_email: str) -> DeliveryResult:
                raise RuntimeError("provider bug")

        result = await EmailService(provider=Exploding()).send(  # type: ignore[arg-type]
            EmailMessage(to=["u@e.com"], subject="s", html="h", text="t")
        )
        assert not result.ok
        assert "provider bug" in result.error


class TestReportedAttempts:
    """The result carries how many sends were made, not how many were allowed.

    The delivery record used to store the configured maximum on every failure,
    so a permanent failure that gave up after one try was logged as three.
    """

    async def test_success_first_time_reports_one(self) -> None:
        result = await EmailService(provider=FlakyProvider(failures=0)).send(
            EmailMessage(to=["u@e.com"], subject="s", html="h", text="t")
        )
        assert result.attempts == 1

    async def test_success_after_retries_counts_them(self) -> None:
        result = await EmailService(provider=FlakyProvider(failures=2)).send(
            EmailMessage(to=["u@e.com"], subject="s", html="h", text="t")
        )
        assert result.ok
        assert result.attempts == 3

    async def test_permanent_failure_reports_the_single_attempt(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(settings, "email_max_attempts", 3)
        result = await EmailService(
            provider=FlakyProvider(failures=99, retryable=False)
        ).send(EmailMessage(to=["u@e.com"], subject="s", html="h", text="t"))
        assert not result.ok
        assert result.attempts == 1

    async def test_exhausted_retries_report_the_cap(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "email_max_attempts", 3)
        result = await EmailService(provider=FlakyProvider(failures=99)).send(
            EmailMessage(to=["u@e.com"], subject="s", html="h", text="t")
        )
        assert not result.ok
        assert result.attempts == 3


class TestSelftest:
    async def test_selftest_sends_one_message(self, tmp_path: Path) -> None:
        service = EmailService(provider=FileProvider(outbox=tmp_path / "outbox"))
        result = await service.send_selftest("user@example.com")
        assert result.ok
        assert len(list((tmp_path / "outbox").glob("*.eml"))) == 1

    def test_cli_requires_a_target(self) -> None:
        from app.email.selftest import main

        with pytest.raises(SystemExit):
            main([])

    def test_cli_verify_only_succeeds_for_console(self, monkeypatch) -> None:
        from app.email.selftest import main

        monkeypatch.setattr(settings, "email_provider", "console")
        assert main(["--verify-only"]) == 0

    def test_cli_reports_misconfiguration(self, monkeypatch) -> None:
        from app.email.selftest import main

        monkeypatch.setattr(settings, "email_provider", "sendgrid")
        monkeypatch.setattr(settings, "sendgrid_api_key", "")
        assert main(["--verify-only"]) == 2

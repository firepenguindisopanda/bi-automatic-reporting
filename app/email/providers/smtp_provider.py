import asyncio
import logging
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from app.email.providers.base import ProviderBase

logger = logging.getLogger(__name__)


class SMTPProvider(ProviderBase):
    async def send_report(
        self,
        to_email: str,
        from_email: str,
        company_name: str,
        pdf_path: Path,
        docx_path: Path | None = None,
    ) -> bool:
        try:
            msg = MIMEMultipart()
            msg["Subject"] = f"Business Intelligence Report: {company_name}"
            msg["From"] = from_email
            msg["To"] = to_email
            msg.attach(MIMEText(self._build_html_body(company_name), "html"))

            with open(pdf_path, "rb") as f:
                pdf_attachment = MIMEApplication(f.read(), _subtype="pdf")
                pdf_attachment.add_header("Content-Disposition", "attachment", filename=pdf_path.name)
                msg.attach(pdf_attachment)

            if docx_path and docx_path.exists():
                with open(docx_path, "rb") as f:
                    docx_attachment = MIMEApplication(
                        f.read(),
                        _subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                    docx_attachment.add_header("Content-Disposition", "attachment", filename=docx_path.name)
                    msg.attach(docx_attachment)

            def _smtp_send() -> None:
                with smtplib.SMTP("localhost", 25) as server:
                    server.sendmail(from_email, [to_email], msg.as_string())

            await asyncio.to_thread(_smtp_send)

            logger.info("SMTP email sent to %s", to_email)
            return True

        except Exception as e:
            logger.exception("SMTP send failed: %s", e)
            return False

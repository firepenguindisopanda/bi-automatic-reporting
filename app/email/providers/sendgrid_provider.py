import asyncio
import base64
import logging
from pathlib import Path

from app.email.providers.base import ProviderBase

logger = logging.getLogger(__name__)


class SendGridProvider(ProviderBase):
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def send_report(
        self,
        to_email: str,
        from_email: str,
        company_name: str,
        pdf_path: Path,
        docx_path: Path | None = None,
    ) -> bool:
        try:
            from sendgrid import SendGridAPIClient
            from sendgrid.helpers.mail import (
                Attachment,
                Email,
                FileContent,
                FileName,
                FileType,
                Mail,
            )

            message = Mail(
                from_email=Email(from_email),
                to_emails=to_email,
                subject=f"Business Intelligence Report: {company_name}",
                html_content=self._build_html_body(company_name),
            )

            with open(pdf_path, "rb") as f:
                pdf_data = f.read()
            attachment = Attachment(
                file_content=FileContent(base64.b64encode(pdf_data).decode()),
                file_type=FileType("application/pdf"),
                file_name=FileName(pdf_path.name),
                disposition="attachment",
            )
            message.add_attachment(attachment)

            if docx_path and docx_path.exists():
                with open(docx_path, "rb") as f:
                    docx_data = f.read()
                docx_attachment = Attachment(
                    file_content=FileContent(base64.b64encode(docx_data).decode()),
                    file_type=FileType("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                    file_name=FileName(docx_path.name),
                    disposition="attachment",
                )
                message.add_attachment(docx_attachment)

            sg = SendGridAPIClient(self._api_key)
            response = await asyncio.to_thread(sg.send, message)
            logger.info(
                "SendGrid email sent to %s (status=%s)",
                to_email,
                response.status_code,
            )
            return 200 <= response.status_code < 300

        except Exception as e:
            logger.exception("SendGrid send failed: %s", e)
            return False

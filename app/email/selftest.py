"""Verify email configuration without running a job.

    uv run python -m app.email.selftest you@example.com
    uv run python -m app.email.selftest --verify-only

Turns switching providers into a two-minute verified change instead of
discovering the problem when a report fails to arrive.
"""

import argparse
import asyncio
import logging
import sys

from app.config import settings
from app.email.message import EmailConfigError
from app.email.service import EmailService


async def run(to_email: str | None, verify_only: bool) -> int:
    print(f"EMAIL_PROVIDER = {settings.email_provider}")
    print(f"FROM_EMAIL     = {settings.from_email}")

    try:
        service = EmailService()
    except EmailConfigError as e:
        print(f"\nConfiguration error: {e}")
        return 2

    health = await service.verify()
    status = "ok" if health["ok"] else "FAILED"
    print(f"\nProvider check [{status}]: {health['detail'] or health['provider']}")
    if not health["ok"]:
        return 1
    if verify_only or not to_email:
        return 0

    print(f"\nSending a test message to {to_email} ...")
    result = await service.send_selftest(to_email)
    if result.ok:
        print(f"Sent via {result.provider}" + (f" (id {result.message_id})" if result.message_id else ""))
        return 0

    print(f"Send FAILED via {result.provider}: {result.error}")
    print(f"Retryable: {result.retryable}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Test the configured email provider.")
    parser.add_argument("to_email", nargs="?", help="address to send a test message to")
    parser.add_argument(
        "--verify-only", action="store_true", help="check configuration without sending"
    )
    args = parser.parse_args(argv)

    if not args.to_email and not args.verify_only:
        parser.error("give an address to send to, or pass --verify-only")

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    return asyncio.run(run(args.to_email, args.verify_only))


if __name__ == "__main__":
    sys.exit(main())

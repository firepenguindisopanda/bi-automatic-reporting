"""Who the service will send reports to, and how often.

The submitter names the recipient and nothing authenticates the submitter, so
every send is a message from this service's domain to an address a stranger
chose. Left open, that is a relay: point it at a victim, and point the scraper
at a page that steers the model's output, and the victim receives mail with
attacker-shaped content under this domain's reputation.

Three limits close it down without adding accounts:
  - an optional allowlist of addresses or domains,
  - a cap on reports per recipient per day, counted across all jobs,
  - a cap on delivery attempts per job, so resend cannot be looped.

OWNER_EMAILS names the people who run the deployment. They are always allowed
and neither cap applies to them: the caps protect strangers' inboxes, and an
owner testing reports against their own inbox is not that.
"""

import re
from dataclasses import dataclass

from app.config import settings

DAY_HOURS = 24


class DeliveryRefusedError(Exception):
    """A send the policy will not allow. Carries the HTTP status to answer with."""

    def __init__(self, message: str, status_code: int = 429) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class Allowlist:
    addresses: frozenset[str]
    domains: frozenset[str]

    @property
    def open(self) -> bool:
        """True when no allowlist is configured, i.e. anyone may receive."""
        return not self.addresses and not self.domains

    def permits(self, email: str) -> bool:
        if self.open:
            return True
        address = email.strip().lower()
        domain = address.rpartition("@")[2]
        return address in self.addresses or domain in self.domains


_SEPARATORS = re.compile(r"[,;\s]+")


def parse_allowlist(raw: str) -> Allowlist:
    """``"me@example.com, @example.org; example.net"`` -> addresses and domains.

    Commas, semicolons and whitespace all separate entries. Splitting on commas
    alone turned ``a@x.com;b@y.com`` into one address that matched nobody, so a
    natural way to write the list silently refused every recipient.
    """
    addresses: set[str] = set()
    domains: set[str] = set()
    for entry in (e.strip().lower() for e in _SEPARATORS.split(raw)):
        if not entry:
            continue
        if entry.startswith("@"):
            domains.add(entry[1:])
        elif "@" in entry:
            addresses.add(entry)
        else:
            domains.add(entry)
    return Allowlist(frozenset(addresses), frozenset(domains))


def is_owner(email: str) -> bool:
    """Whether ``email`` is in OWNER_EMAILS. An empty setting names nobody."""
    owners = parse_allowlist(settings.owner_emails)
    return not owners.open and owners.permits(email)


def check_recipient_allowed(email: str) -> None:
    """Raise DeliveryRefusedError (403) when an allowlist is set and excludes ``email``."""
    if is_owner(email):
        return
    if not parse_allowlist(settings.recipient_allowlist).permits(email):
        raise DeliveryRefusedError("This service is not accepting reports for that address.", 403)

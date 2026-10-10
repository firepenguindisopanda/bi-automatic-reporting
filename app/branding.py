"""Who the reports come from, and how they look.

One place, so the email, the PDF and the From line agree. The palette is the
web app's editorial palette (frontend/src/index.css): warm paper, ink text, one
rust accent. The rust rule is the brand's one device: a rubric was the heading
a scribe wrote in red ink, and it is the marked line in the app's page logo.
"""

import re
from dataclasses import dataclass
from email.utils import formataddr, parseaddr
from urllib.parse import urlparse

from app.config import settings

# Light theme of the app, plus the dark-surface accents the cover and the email
# masthead need. SWOT and status colours are the app's semantic tokens.
PALETTE = {
    "paper": "#FBFAF7",
    "surface": "#FFFFFF",
    "sunk": "#F5F2EC",
    "frame": "#EFEBE3",
    "ink": "#1A1815",
    "ink_muted": "#6B655C",
    "ink_faint": "#8B8478",
    "rule": "#E4DFD5",
    "rule_strong": "#D2CABB",
    "accent": "#9A3F1F",
    "accent_soft": "#F3E7E0",
    "accent_bright": "#C25A30",
    "on_ink": "#F2EFE8",
    "on_ink_muted": "#A29B8F",
    "on_ink_rule": "#3A362F",
    "on_ink_accent": "#E08A62",
    "positive": "#3E6B4A",
    "positive_soft": "#EBF1EC",
    "warning": "#7D5F19",
    "warning_soft": "#F5EFE0",
    "slate": "#3D5468",
    "slate_soft": "#E9EEF2",
    "negative": "#8C2F26",
    "negative_soft": "#F7E8E5",
}

_EM_DASH = re.compile(r"\s*—\s*")


def without_em_dashes(text: str) -> str:
    """Em dashes become spaced hyphens. House style: the brand uses none."""
    return _EM_DASH.sub(" - ", text)


def display_host(url: str) -> str:
    """The site a report is about, as a reader would write it: riomed.com."""
    host = urlparse(url or "").netloc.lower().rpartition("@")[2].partition(":")[0]
    return host.removeprefix("www.")[:60]


@dataclass(frozen=True, slots=True)
class Brand:
    name: str
    tagline: str
    author: str
    author_email: str
    author_url: str

    @property
    def author_url_label(self) -> str:
        """The portfolio address as people say it: no scheme, no trailing slash."""
        return re.sub(r"^https?://", "", self.author_url).rstrip("/")


def brand() -> Brand:
    return Brand(
        name=settings.brand_name.strip() or "Rubric",
        tagline=settings.brand_tagline.strip(),
        author=settings.brand_author.strip(),
        author_email=settings.brand_author_email.strip(),
        author_url=settings.brand_author_url.strip(),
    )


def sender() -> str:
    """The From line: the brand's name on the configured address.

    A FROM_EMAIL that already carries a display name keeps it unless FROM_NAME
    is set.
    """
    configured_name, address = parseaddr(settings.from_email)
    name = settings.from_name.strip() or configured_name or brand().name
    return formataddr((name, address or settings.from_email))

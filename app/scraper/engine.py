import logging
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.models.bi import ScrapedContent

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; BIAnalyzer/1.0; +https://bisystem.com/bot)"


class ScraperEngine:
    def __init__(self, timeout: float = 30.0, max_text_length: int = 50_000):
        self._timeout = timeout
        self._max_text_length = max_text_length

    async def scrape(self, url: str) -> ScrapedContent:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text

            soup = BeautifulSoup(html, "lxml")

            title = self._extract_title(soup)
            description = self._extract_meta(soup, "description")
            meta_keywords = self._extract_keywords(soup)
            headings = self._extract_headings(soup)
            text_content = self._extract_text(soup)
            links = self._extract_links(soup, url)

            return ScrapedContent(
                url=url,
                title=title,
                description=description,
                headings=headings,
                text_content=text_content[: self._max_text_length],
                meta_keywords=meta_keywords,
                links=links[:100],
            )

        except httpx.HTTPStatusError as e:
            return ScrapedContent(
                url=url,
                title="",
                description="",
                text_content="",
                error=f"HTTP {e.response.status_code}",
            )
        except httpx.TimeoutException:
            return ScrapedContent(
                url=url,
                title="",
                description="",
                text_content="",
                error="Request timed out",
            )
        except Exception as e:
            logger.exception("Scrape failed for %s", url)
            return ScrapedContent(url=url, title="", description="", text_content="", error=str(e))

    def _extract_title(self, soup: BeautifulSoup) -> str:
        title = soup.title.string if soup.title else ""
        return title.strip() if title else ""

    def _extract_meta(self, soup: BeautifulSoup, name: str) -> str:
        tag = soup.find("meta", attrs={"name": re.compile(name, re.I)})
        if tag and tag.get("content"):
            return tag["content"].strip()
        tag = soup.find("meta", attrs={"property": re.compile(name, re.I)})
        if tag and tag.get("content"):
            return tag["content"].strip()
        return ""

    def _extract_keywords(self, soup: BeautifulSoup) -> list[str]:
        content = self._extract_meta(soup, "keywords")
        if content:
            return [k.strip() for k in content.split(",") if k.strip()]
        return []

    def _extract_headings(self, soup: BeautifulSoup) -> list[str]:
        headings = []
        for tag in ["h1", "h2", "h3"]:
            for el in soup.find_all(tag):
                text = el.get_text(strip=True)
                if text:
                    headings.append(f"{tag.upper()}: {text}")
        return headings

    def _extract_text(self, soup: BeautifulSoup) -> str:
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)

    def _extract_links(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/"):
                parsed = urlparse(base_url)
                href = f"{parsed.scheme}://{parsed.netloc}{href}"
            if href.startswith("http"):
                links.append(href)
        return links

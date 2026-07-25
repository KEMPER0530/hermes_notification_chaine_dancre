from __future__ import annotations

import logging
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from hermes_notification_chaine_dancre.application.config import MonitorConfig
from hermes_notification_chaine_dancre.domain.models import ProductSnapshot
from hermes_notification_chaine_dancre.infrastructure.hermes.parser import (
    extract_product_links,
    normalize_url,
    parse_product_page,
)


logger = logging.getLogger(__name__)


class HermesProductCrawler:
    def crawl(self, config: MonitorConfig) -> list[ProductSnapshot]:
        queue = [
            url
            for url in dict.fromkeys(config.seed_urls)
            if is_allowed_https_url(url, config.allowed_hosts)
        ]
        visited: set[str] = set()
        snapshots: dict[str, ProductSnapshot] = {}

        while queue and len(visited) < config.page_limit:
            url = queue.pop(0)
            normalized_url = normalize_url(url)
            if normalized_url in visited:
                continue

            visited.add(normalized_url)
            try:
                html = self._fetch_url(
                    normalized_url,
                    user_agent=config.user_agent,
                    timeout_seconds=config.timeout_seconds,
                )
            except (HTTPError, URLError, TimeoutError, UnicodeError) as exc:
                logger.warning("Failed to fetch %s: %s", normalized_url, exc)
                continue

            snapshot = parse_product_page(
                normalized_url,
                html,
                target_keywords=config.target_keywords,
                target_sizes=config.target_sizes,
            )
            if snapshot:
                snapshots[snapshot.product_id] = snapshot

            for link in extract_product_links(
                html,
                normalized_url,
                target_keywords=config.target_keywords,
            ):
                parsed_link = urlparse(link)
                if not is_allowed_host(parsed_link.netloc, config.allowed_hosts):
                    continue
                if link not in visited and link not in queue:
                    queue.append(link)

            if queue and config.fetch_delay_ms > 0:
                time.sleep(config.fetch_delay_ms / 1000)

        return list(snapshots.values())

    def _fetch_url(self, url: str, user_agent: str, timeout_seconds: int) -> str:
        request = Request(
            url,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )
        with urlopen(request, timeout=timeout_seconds) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read(2_500_000).decode(charset, errors="replace")


def is_allowed_https_url(url: str, allowed_hosts: tuple[str, ...]) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and is_allowed_host(parsed.netloc, allowed_hosts)


def is_allowed_host(netloc: str, allowed_hosts: tuple[str, ...]) -> bool:
    host = netloc.split("@")[-1].split(":")[0].strip(".").lower()
    normalized_allowed_hosts = tuple(item.strip(".").lower() for item in allowed_hosts if item)
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in normalized_allowed_hosts)

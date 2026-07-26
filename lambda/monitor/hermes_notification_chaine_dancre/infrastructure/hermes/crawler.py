"""Hermes 公式サイトを控えめに巡回する crawler adapter。"""

from __future__ import annotations

import logging
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from hermes_notification_chaine_dancre.application.config import MonitorConfig
from hermes_notification_chaine_dancre.domain.models import ProductSnapshot
from hermes_notification_chaine_dancre.infrastructure.hermes.parser import (
    extract_product_links,
    normalize_url,
    parse_embedded_product_list,
    parse_product_page,
    parse_product_seed_url,
)


logger = logging.getLogger(__name__)


class HermesProductCrawler:
    """seed URL から対象商品ページを辿り、現在の商品状態を返す。"""

    def crawl(self, config: MonitorConfig) -> list[ProductSnapshot]:
        # seed URL は HTTPS かつ許可ホストだけを開始点にする。
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
            except (HTTPError, URLError, TimeoutError, UnicodeError, ValueError) as exc:
                logger.warning("Failed to fetch %s: %s", normalized_url, exc)
                fallback_snapshot = parse_product_seed_url(
                    normalized_url,
                    target_keywords=config.target_keywords,
                    target_sizes=config.target_sizes,
                )
                if fallback_snapshot:
                    snapshots.setdefault(fallback_snapshot.product_id, fallback_snapshot)
                continue

            # 商品ページとして解析できたものだけ snapshot として採用する。
            snapshot = parse_product_page(
                normalized_url,
                html,
                target_keywords=config.target_keywords,
                target_sizes=config.target_sizes,
            )
            if snapshot:
                snapshots[snapshot.product_id] = snapshot

            # カテゴリページは商品ページURLを直接辿れない場合があるため、SSR済み商品JSONも読む。
            for embedded_snapshot in parse_embedded_product_list(
                normalized_url,
                html,
                target_keywords=config.target_keywords,
                target_sizes=config.target_sizes,
            ):
                snapshots.setdefault(embedded_snapshot.product_id, embedded_snapshot)

            # ページ内リンクは商品候補だけを次の巡回対象にする。
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

            # 短時間の連続アクセスを避けるため、次の取得前に待機する。
            if queue and config.fetch_delay_ms > 0:
                time.sleep(config.fetch_delay_ms / 1000)

        return list(snapshots.values())

    def _fetch_url(self, url: str, user_agent: str, timeout_seconds: int) -> str:
        """Hermes ページを取得し、最大読み込み量を制限して文字列化する。"""
        request = Request(
            encode_url_for_http_request(url),
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )
        with urlopen(request, timeout=timeout_seconds) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read(2_500_000).decode(charset, errors="replace")


def encode_url_for_http_request(url: str) -> str:
    """urllib が送信できるよう、URL の path/query だけを ASCII 安全にする。"""
    parts = urlsplit(url)
    # 既に percent-encoded 済みの URL を二重エンコードしないよう % は safe に残す。
    encoded_path = quote(parts.path, safe="/%:@")
    encoded_query = quote(parts.query, safe="=&%:+,;/?@")
    # fragment は HTTP リクエストに送らないため、ここでも落としておく。
    return urlunsplit((parts.scheme, parts.netloc, encoded_path, encoded_query, ""))


def is_allowed_https_url(url: str, allowed_hosts: tuple[str, ...]) -> bool:
    """クロール開始 URL が HTTPS かつ許可ホストかを判定する。"""
    parsed = urlparse(url)
    return parsed.scheme == "https" and is_allowed_host(parsed.netloc, allowed_hosts)


def is_allowed_host(netloc: str, allowed_hosts: tuple[str, ...]) -> bool:
    """サブドメインを許容しつつ、外部ホストへの逸脱を防ぐ。"""
    host = netloc.split("@")[-1].split(":")[0].strip(".").lower()
    normalized_allowed_hosts = tuple(item.strip(".").lower() for item in allowed_hosts if item)
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in normalized_allowed_hosts)

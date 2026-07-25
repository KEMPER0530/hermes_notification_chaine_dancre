from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path


sys.path.append(str(Path(__file__).resolve().parents[1] / "lambda" / "monitor"))

from hermes_notification_chaine_dancre.application.config import MonitorConfig
from hermes_notification_chaine_dancre.application.use_cases import CheckRestocksUseCase
from hermes_notification_chaine_dancre.domain.models import (
    ProductSnapshot,
    ProductState,
    RestockEvent,
)
from hermes_notification_chaine_dancre.domain.services import RestockPolicy
from hermes_notification_chaine_dancre.infrastructure.hermes.parser import (
    extract_product_links,
    parse_product_page,
)
from hermes_notification_chaine_dancre.infrastructure.hermes.crawler import (
    is_allowed_https_url,
)


def test_parse_jsonld_in_stock_product() -> None:
    html = """
    <html>
      <head>
        <link rel="canonical" href="https://www.hermes.com/jp/ja/product/bracelet-chaine-d-ancre-gm-H123456/">
        <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": "Bracelet Chaîne d'ancre GM",
            "sku": "H123456",
            "offers": {
              "@type": "Offer",
              "availability": "https://schema.org/InStock"
            }
          }
        </script>
      </head>
      <body><h1>Bracelet Chaîne d'ancre GM</h1></body>
    </html>
    """

    snapshot = parse_product_page(
        "https://www.hermes.com/jp/ja/product/bracelet-chaine-d-ancre-gm-H123456/",
        html,
        target_keywords=["chaine d'ancre", "シェーヌダンクル"],
        target_sizes=["GM", "TGM"],
    )

    assert snapshot is not None
    assert snapshot.available is True
    assert snapshot.availability_source == "json-ld"
    assert snapshot.sku == "H123456"
    assert snapshot.size == "GM"
    assert snapshot.product_id == "sku#H123456"


def test_parse_japanese_sold_out_fallback() -> None:
    html = """
    <html>
      <head><title>シェーヌ・ダンクル TGM | Hermès</title></head>
      <body>
        <h1>シェーヌ・ダンクル TGM</h1>
        <p>現在在庫がありません</p>
      </body>
    </html>
    """

    snapshot = parse_product_page(
        "https://www.hermes.com/jp/ja/product/chaine-d-ancre-tgm-H999999/",
        html,
        target_keywords=["シェーヌダンクル", "chaine d'ancre"],
        target_sizes=["GM", "TGM"],
    )

    assert snapshot is not None
    assert snapshot.available is False
    assert snapshot.availability_source == "text-out-of-stock"
    assert snapshot.size == "TGM"


def test_extract_product_links_filters_assets_and_keeps_product_urls() -> None:
    html = """
    <a href="/jp/ja/product/bracelet-chaine-d-ancre-tgm-H222222/">TGM</a>
    <a href="/content/image.jpg">image</a>
    <a href="https://www.example.com/other">other</a>
    """

    links = extract_product_links(
        html,
        "https://www.hermes.com/jp/ja/category/jewelry/",
        target_keywords=["chaine d'ancre"],
    )

    assert links == [
        "https://www.hermes.com/jp/ja/product/bracelet-chaine-d-ancre-tgm-H222222/"
    ]


def test_allowed_hosts_rejects_non_hermes_and_non_https_urls() -> None:
    assert is_allowed_https_url("https://www.hermes.com/jp/ja/", ("hermes.com",)) is True
    assert is_allowed_https_url("http://www.hermes.com/jp/ja/", ("hermes.com",)) is False
    assert is_allowed_https_url("https://example.com/jp/ja/", ("hermes.com",)) is False


def test_use_case_notifies_when_product_becomes_available() -> None:
    snapshot = ProductSnapshot(
        product_id="sku#H123456",
        name="Bracelet Chaîne d'ancre GM",
        size="GM",
        url="https://www.hermes.com/jp/ja/product/bracelet-chaine-d-ancre-gm-H123456/",
        sku="H123456",
        available=True,
        availability_source="json-ld",
    )
    repository = InMemoryRepository(
        {
            snapshot.product_id: ProductState(
                product_id=snapshot.product_id,
                name=snapshot.name,
                size=snapshot.size,
                url=snapshot.url,
                sku=snapshot.sku,
                available=False,
                availability_source="text-out-of-stock",
                last_seen_at="2026-01-01T00:00:00+00:00",
            )
        }
    )
    notifier = CollectingNotifier()

    result = build_use_case([snapshot], repository, notifier).execute(make_config())

    assert result.notifications == 1
    assert result.checked_at == "2026-01-02T12:04:05+09:00"
    assert notifier.events[0].previous_available is False
    assert repository.items[snapshot.product_id].available is True
    assert repository.items[snapshot.product_id].last_notification_at == result.checked_at


def test_use_case_can_skip_initial_available_notification() -> None:
    snapshot = ProductSnapshot(
        product_id="sku#H123456",
        name="Bracelet Chaîne d'ancre GM",
        size="GM",
        url="https://www.hermes.com/jp/ja/product/bracelet-chaine-d-ancre-gm-H123456/",
        sku="H123456",
        available=True,
        availability_source="json-ld",
    )
    repository = InMemoryRepository({})
    notifier = CollectingNotifier()

    result = build_use_case(
        [snapshot],
        repository,
        notifier,
        notify_on_first_available=False,
    ).execute(make_config(notify_on_first_available=False))

    assert result.notifications == 0
    assert notifier.events == []
    assert repository.items[snapshot.product_id].available is True


def make_config(notify_on_first_available: bool = True) -> MonitorConfig:
    return MonitorConfig(
        seed_urls=("https://www.hermes.com/jp/ja/",),
        allowed_hosts=("hermes.com",),
        notification_timezone="Asia/Tokyo",
        target_keywords=("chaine d'ancre",),
        target_sizes=("GM", "TGM"),
        notify_on_first_available=notify_on_first_available,
        page_limit=1,
        fetch_delay_ms=0,
        timeout_seconds=1,
        user_agent="test-agent",
    )


def build_use_case(
    snapshots: list[ProductSnapshot],
    repository: "InMemoryRepository",
    notifier: "CollectingNotifier",
    notify_on_first_available: bool = True,
) -> CheckRestocksUseCase:
    return CheckRestocksUseCase(
        crawler=StaticCrawler(snapshots),
        repository=repository,
        notifier=notifier,
        clock=FixedClock(),
        policy=RestockPolicy(notify_on_first_available),
    )


class StaticCrawler:
    def __init__(self, snapshots: list[ProductSnapshot]) -> None:
        self._snapshots = snapshots

    def crawl(self, config: MonitorConfig) -> list[ProductSnapshot]:
        return self._snapshots


class InMemoryRepository:
    def __init__(self, items: dict[str, ProductState]) -> None:
        self.items = dict(items)

    def get(self, product_id: str) -> ProductState | None:
        return self.items.get(product_id)

    def save(self, state: ProductState) -> None:
        self.items[state.product_id] = state


class CollectingNotifier:
    def __init__(self) -> None:
        self.events: list[RestockEvent] = []

    def publish(self, event: RestockEvent) -> None:
        self.events.append(event)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

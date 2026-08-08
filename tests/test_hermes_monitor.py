"""HTML 解析と入荷通知ユースケースの回帰テスト。"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote


sys.path.append(str(Path(__file__).resolve().parents[1] / "lambda" / "monitor"))

# Lambda 配下の package を、リポジトリルートから pytest 実行できるようにする。
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
    parse_embedded_product_list,
    parse_product_page,
)
from hermes_notification_chaine_dancre.infrastructure.hermes.crawler import (
    HermesProductCrawler,
    encode_url_for_http_request,
    is_allowed_https_url,
)
from hermes_notification_chaine_dancre.infrastructure.sns_restock_notifier import (
    SnsRestockNotifier,
    to_linkable_url,
)


def test_parse_jsonld_in_stock_product() -> None:
    """JSON-LD の InStock を購入可能として扱えることを確認する。"""
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
    """構造化データがない場合に、日本語本文から在庫なしを判定する。"""
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
    """画像などの asset を除外し、商品 URL だけを巡回候補に残す。"""
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


def test_extract_product_links_keeps_only_target_product_urls() -> None:
    """content/legal/category ページや対象外商品を巡回候補から外す。"""
    japanese_target_slug = quote("ブレスレット-《シェーヌ・ダンクル》-gm-H333333", safe="")
    html = f"""
    <a href="/jp/ja/content/402251-page-plage-hermes/">content</a>
    <a href="/jp/ja/legal/6597-apple-watch-hermes%E5%8F%96%E6%89%B1%E5%BA%97/">legal</a>
    <a href="/jp/ja/category/jewelry/collections/chaine-d-ancre/?page=2">page 2</a>
    <a href="/jp/ja/product/ブレスレット-《コリエ・ド・シアン》-pm-H115424Bv00LG/">other product</a>
    <a href="/jp/ja/product/{japanese_target_slug}/">target</a>
    """

    links = extract_product_links(
        html,
        "https://www.hermes.com/jp/ja/category/jewelry/silver-jewelry/bracelets/",
        target_keywords=["シェーヌダンクル", "chaine d'ancre"],
    )

    assert links == [f"https://www.hermes.com/jp/ja/product/{japanese_target_slug}/"]


def test_encode_url_for_http_request_percent_encodes_japanese_url() -> None:
    """urllib に渡す URL は日本語 path/query を percent-encode する。"""
    encoded = encode_url_for_http_request(
        "https://www.hermes.com/jp/ja/product/ブレスレット-《シェーヌ・ダンクル》-gm-H333333/"
        "?q=入荷&page=1#ignored"
    )

    assert all(ord(character) < 128 for character in encoded)
    assert "%E3%83%96" in encoded
    assert "q=%E5%85%A5%E8%8D%B7" in encoded
    assert "page=1" in encoded
    assert "#ignored" not in encoded


def test_sns_notification_percent_encodes_japanese_product_url() -> None:
    """通知本文のURLは、SMSで途中切れしないようASCIIのURLにする。"""
    raw_url = (
        "https://www.hermes.com/jp/ja/product/"
        "ブレスレット-《シェーヌ・ダンクル》-gm-H101672Bv00011/"
    )
    sns_client = CapturingSnsClient()
    notifier = SnsRestockNotifier(sns_client, "arn:aws:sns:ap-northeast-1:123456789012:test")

    notifier.publish(
        RestockEvent(
            snapshot=ProductSnapshot(
                product_id="sku#H101672B00011",
                name="ブレスレット 《シェーヌ・ダンクル》 GM",
                size="GM",
                url=raw_url,
                sku="H101672B 00011",
                available=True,
                availability_source="json-ld",
            ),
            previous_available=None,
            checked_at="2026-07-26T10:11:46+09:00",
        )
    )

    expected_url = to_linkable_url(raw_url)
    message = sns_client.published_messages[0]["Message"]

    assert expected_url in message
    assert "URL: https://www.hermes.com/jp/ja/product/%E3%83%96" in message
    assert "《シェーヌ・ダンクル》" not in message.split("URL: ", maxsplit=1)[1].splitlines()[0]
    assert all(ord(character) < 128 for character in expected_url)


def test_ci_workflow_includes_all_chaine_dancre_tgm_direct_seed_urls() -> None:
    """短時間入荷を拾うため、TGM候補の直URLをCDK deploy seedへ含める。"""
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml").read_text()

    for link_count in ("09", "10", "11", "12", "13", "14"):
        assert f"tgm-H101995Bv000{link_count}/" in workflow


def test_parse_embedded_product_list_detects_available_chaine_dancre_gm() -> None:
    """カテゴリHTMLのhermes-stateから対象GM商品を検知できることを確認する。"""
    html = """
    <script id="hermes-state" type="application/json">
      {
        "788659288": {
          "b": {
            "products": {
              "items": [
                {
                  "sku": "H101672B 00011",
                  "title": "ブレスレット 《シェーヌ・ダンクル》 GM",
                  "url": "/product/ブレスレット-《シェーヌ・ダンクル》-gm-H101672Bv00011/",
                  "slug": "ブレスレット-《シェーヌ・ダンクル》-gm",
                  "stock": {
                    "ecom": true,
                    "hasVariantInEcomStock": false,
                    "displayOnly": false
                  }
                },
                {
                  "sku": "H115424B 00LG",
                  "title": "ブレスレット 《コリエ・ド・シアン》 PM",
                  "url": "/product/ブレスレット-《コリエ・ド・シアン》-pm-H115424Bv00LG/",
                  "stock": {"ecom": true, "displayOnly": false}
                }
              ]
            }
          }
        }
      }
    </script>
    """

    snapshots = parse_embedded_product_list(
        "https://www.hermes.com/jp/ja/category/jewelry/silver-jewelry/bracelets/",
        html,
        target_keywords=["シェーヌダンクル", "chaine d'ancre"],
        target_sizes=["GM", "TGM"],
    )

    assert len(snapshots) == 1
    assert snapshots[0].name == "ブレスレット 《シェーヌ・ダンクル》 GM"
    assert snapshots[0].sku == "H101672B 00011"
    assert snapshots[0].size == "GM"
    assert snapshots[0].available is True
    assert snapshots[0].availability_source == "hermes-state"
    assert snapshots[0].url == (
        "https://www.hermes.com/jp/ja/product/"
        "ブレスレット-《シェーヌ・ダンクル》-gm-H101672Bv00011/"
    )


def test_crawler_uses_embedded_product_list_from_category_page() -> None:
    """商品ページが403でもカテゴリHTMLだけで対象商品を返せることを確認する。"""
    html = """
    <script id="hermes-state" type="application/json">
      {
        "788659288": {
          "b": {
            "products": {
              "items": [
                {
                  "sku": "H101672B 00011",
                  "title": "ブレスレット 《シェーヌ・ダンクル》 GM",
                  "url": "/product/ブレスレット-《シェーヌ・ダンクル》-gm-H101672Bv00011/",
                  "stock": {"ecom": true, "displayOnly": false}
                }
              ]
            }
          }
        }
      }
    </script>
    """
    crawler = StaticHtmlHermesCrawler({make_config().seed_urls[0]: html})

    snapshots = crawler.crawl(make_config())

    assert len(snapshots) == 1
    assert snapshots[0].product_id == "sku#H101672B 00011"
    assert snapshots[0].availability_source == "hermes-state"


def test_crawler_records_unreachable_direct_seed_as_unavailable_snapshot() -> None:
    """直seed商品URLが403以外で失敗した場合は、購入不可状態へ戻す。"""
    direct_url = (
        "https://www.hermes.com/jp/ja/product/"
        "%E3%83%96%E3%83%AC%E3%82%B9%E3%83%AC%E3%83%83%E3%83%88-"
        "%E3%80%8A%E3%82%B7%E3%82%A7%E3%83%BC%E3%83%8C%E3%83%BB"
        "%E3%83%80%E3%83%B3%E3%82%AF%E3%83%AB%E3%80%8B-gm-H101672Bv00011/"
    )
    crawler = FailingHermesCrawler(status_code=500)

    snapshots = crawler.crawl(make_config(seed_urls=(direct_url,)))

    assert len(snapshots) == 1
    assert snapshots[0].product_id == "sku#H101672B 00011"
    assert snapshots[0].sku == "H101672B 00011"
    assert snapshots[0].size == "GM"
    assert snapshots[0].available is False
    assert snapshots[0].availability_source == "seed-url-unreachable"


def test_crawler_ignores_forbidden_direct_seed_without_warning(caplog) -> None:
    """Hermes の403は想定内として商品状態にもWARNINGログにも採用しない。"""
    direct_url = (
        "https://www.hermes.com/jp/ja/product/"
        "%E3%83%96%E3%83%AC%E3%82%B9%E3%83%AC%E3%83%83%E3%83%88-"
        "%E3%80%8A%E3%82%B7%E3%82%A7%E3%83%BC%E3%83%8C%E3%83%BB"
        "%E3%83%80%E3%83%B3%E3%82%AF%E3%83%AB%E3%80%8B-gm-H101672Bv00011/"
    )
    caplog.set_level(
        logging.WARNING,
        logger="hermes_notification_chaine_dancre.infrastructure.hermes.crawler",
    )
    crawler = FailingHermesCrawler(status_code=403)

    snapshots = crawler.crawl(make_config(seed_urls=(direct_url,)))

    assert snapshots == []
    assert "Failed to fetch" not in caplog.text


def test_allowed_hosts_rejects_non_hermes_and_non_https_urls() -> None:
    """クロール先が HTTPS の Hermes ドメインに制限されることを確認する。"""
    assert is_allowed_https_url("https://www.hermes.com/jp/ja/", ("hermes.com",)) is True
    assert is_allowed_https_url("http://www.hermes.com/jp/ja/", ("hermes.com",)) is False
    assert is_allowed_https_url("https://example.com/jp/ja/", ("hermes.com",)) is False


def test_use_case_notifies_when_product_becomes_available() -> None:
    """前回 unavailable、今回 available の遷移で通知されることを確認する。"""
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
    """初回から available の商品を通知しない設定が効くことを確認する。"""
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


def make_config(
    notify_on_first_available: bool = True,
    seed_urls: tuple[str, ...] = ("https://www.hermes.com/jp/ja/",),
) -> MonitorConfig:
    """テスト用の最小 MonitorConfig を作る。"""
    return MonitorConfig(
        seed_urls=seed_urls,
        allowed_hosts=("hermes.com",),
        notification_timezone="Asia/Tokyo",
        target_keywords=("chaine d'ancre", "シェーヌダンクル"),
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
    """外部I/Oをすべて fake に差し替えた use case を作る。"""
    return CheckRestocksUseCase(
        crawler=StaticCrawler(snapshots),
        repository=repository,
        notifier=notifier,
        clock=FixedClock(),
        policy=RestockPolicy(notify_on_first_available),
    )


class StaticCrawler:
    """固定 snapshot を返す crawler fake。"""

    def __init__(self, snapshots: list[ProductSnapshot]) -> None:
        self._snapshots = snapshots

    def crawl(self, config: MonitorConfig) -> list[ProductSnapshot]:
        return self._snapshots


class StaticHtmlHermesCrawler(HermesProductCrawler):
    """HTTP取得を固定HTMLで置き換える Hermes crawler fake。"""

    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses

    def _fetch_url(self, url: str, user_agent: str, timeout_seconds: int) -> str:
        return self._responses[url]


class FailingHermesCrawler(HermesProductCrawler):
    """HTTP取得が403になる Hermes crawler fake。"""

    def __init__(self, status_code: int) -> None:
        self._status_code = status_code

    def _fetch_url(self, url: str, user_agent: str, timeout_seconds: int) -> str:
        reason = "Forbidden" if self._status_code == 403 else "Server Error"
        raise HTTPError(url, self._status_code, reason, hdrs=None, fp=None)


class InMemoryRepository:
    """DynamoDB の代わりに dict で状態を保持する repository fake。"""

    def __init__(self, items: dict[str, ProductState]) -> None:
        self.items = dict(items)

    def get(self, product_id: str) -> ProductState | None:
        return self.items.get(product_id)

    def save(self, state: ProductState) -> None:
        self.items[state.product_id] = state


class CollectingNotifier:
    """SNS の代わりに通知イベントを list に保存する notifier fake。"""

    def __init__(self) -> None:
        self.events: list[RestockEvent] = []

    def publish(self, event: RestockEvent) -> None:
        self.events.append(event)


class CapturingSnsClient:
    """SNS publish の入力値を保持する client fake。"""

    def __init__(self) -> None:
        self.published_messages: list[dict[str, str]] = []

    def publish(self, **kwargs: str) -> None:
        self.published_messages.append(kwargs)


class FixedClock:
    """JST 変換を検証しやすい固定時刻を返す clock fake。"""

    def now(self) -> datetime:
        return datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

"""Hermes ページの HTML から商品情報と購入可否を抽出する parser。"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from html import unescape
from html.parser import HTMLParser
from typing import Any, Sequence
from urllib.parse import unquote, urldefrag, urljoin, urlparse

from hermes_notification_chaine_dancre.domain.models import ProductSnapshot


DEFAULT_IN_STOCK_PHRASES = (
    # JSON-LD が取れないページ向けに、購入可能を示す文言でも fallback 判定する。
    "add to cart",
    "add to bag",
    "add to shopping bag",
    "カートに追加",
    "バッグに追加",
    "ショッピングバッグに追加",
    "購入する",
)

DEFAULT_OUT_OF_STOCK_PHRASES = (
    # Hermes 側の表記揺れに備え、英語と日本語の在庫なし文言を持つ。
    "out of stock",
    "sold out",
    "currently unavailable",
    "not available",
    "在庫なし",
    "品切れ",
    "現在ご利用いただけません",
    "現在在庫がありません",
    "入荷待ち",
)


class LinkExtractor(HTMLParser):
    """HTML 内の a タグから href だけを収集する軽量 parser。"""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attrs_dict = {key.lower(): value for key, value in attrs if value}
        href = attrs_dict.get("href")
        if href:
            self.links.append(href)


class MetadataExtractor(HTMLParser):
    """title、h1、meta、canonical URL を HTML から抽出する。"""

    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.h1 = ""
        self.meta: dict[str, str] = {}
        self.canonical = ""
        self._in_title = False
        self._in_h1 = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_dict = {key.lower(): value for key, value in attrs if value}

        if tag == "title":
            self._in_title = True
        elif tag == "h1":
            self._in_h1 = True
        elif tag == "meta":
            content = attrs_dict.get("content")
            key = attrs_dict.get("property") or attrs_dict.get("name")
            if content and key:
                self.meta[key.lower()] = unescape(content.strip())
        elif tag == "link" and attrs_dict.get("rel", "").lower() == "canonical":
            self.canonical = attrs_dict.get("href", "")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False

    def handle_data(self, data: str) -> None:
        text = normalize_space(data)
        if not text:
            return
        if self._in_title:
            self.title = normalize_space(f"{self.title} {text}")
        elif self._in_h1:
            self.h1 = normalize_space(f"{self.h1} {text}")


def parse_product_page(
    url: str,
    html: str,
    target_keywords: Sequence[str],
    target_sizes: Sequence[str],
) -> ProductSnapshot | None:
    """HTML 1 ページを対象商品の snapshot へ変換する。対象外なら None を返す。"""
    metadata = extract_metadata(html)
    json_products = list(iter_jsonld_products(html))
    json_product = json_products[0] if json_products else {}

    # 商品名は構造化データを最優先し、取れない場合は HTML メタ情報で補完する。
    name = first_non_empty(
        as_text(json_product.get("name")),
        metadata.meta.get("og:title", ""),
        metadata.h1,
        metadata.title,
        slug_name_from_url(url),
    )
    canonical_url = normalize_url(urljoin(url, metadata.canonical or url))
    sku = first_non_empty_or_none(
        as_text(json_product.get("sku")),
        as_text(json_product.get("mpn")),
        as_text(json_product.get("productID")),
        extract_sku_from_text(f"{name} {url}"),
    )

    # キーワードとサイズの両方に一致したページだけを監視対象にする。
    visible = visible_text(html)
    combined_for_match = " ".join([name, sku or "", canonical_url, visible[:5000]])
    if target_keywords and not matches_any_keyword(combined_for_match, target_keywords):
        return None

    size = detect_size(target_sizes, name, sku or "", canonical_url, visible[:3000])
    if target_sizes and size not in {item.upper() for item in target_sizes}:
        return None

    schema_available = availability_from_jsonld(json_product)
    if schema_available is not None:
        # schema.org の availability は最も信頼できるため、本文文言より優先する。
        available = schema_available
        availability_source = "json-ld"
    else:
        available, availability_source = availability_from_text(visible)

    return ProductSnapshot(
        product_id=stable_product_id(sku, canonical_url),
        name=name,
        size=size,
        url=canonical_url,
        sku=sku,
        available=available,
        availability_source=availability_source,
    )


def parse_embedded_product_list(
    base_url: str,
    html: str,
    target_keywords: Sequence[str],
    target_sizes: Sequence[str],
) -> list[ProductSnapshot]:
    """カテゴリHTML内の hermes-state から商品一覧 snapshot を抽出する。"""
    state = extract_hermes_state(html)
    if state is None:
        return []

    snapshots: dict[str, ProductSnapshot] = {}
    for item in find_hermes_state_product_items(state):
        snapshot = product_snapshot_from_hermes_state_item(
            base_url,
            item,
            target_keywords=target_keywords,
            target_sizes=target_sizes,
        )
        if snapshot:
            snapshots[snapshot.product_id] = snapshot

    return list(snapshots.values())


def parse_product_seed_url(
    url: str,
    target_keywords: Sequence[str],
    target_sizes: Sequence[str],
) -> ProductSnapshot | None:
    """取得不能な直seed商品URLを、購入可能未確認の snapshot として扱う。"""
    if not looks_like_product_url(url):
        return None

    name = slug_name_from_url(url)
    sku = extract_sku_from_text(url)
    combined_for_match = " ".join([name, sku, url])
    if target_keywords and not matches_any_keyword(combined_for_match, target_keywords):
        return None

    size = detect_size(target_sizes, name, sku, url)
    if target_sizes and size not in {target_size.upper() for target_size in target_sizes}:
        return None

    return ProductSnapshot(
        product_id=stable_product_id(sku, url),
        name=name,
        size=size,
        url=url,
        sku=sku or None,
        available=False,
        availability_source="seed-url-unreachable",
    )


def extract_product_links(
    html: str,
    base_url: str,
    target_keywords: Sequence[str],
) -> list[str]:
    """ページ内リンクから、次に取得する価値がある商品候補 URL だけを返す。"""
    extractor = LinkExtractor()
    extractor.feed(html)

    links = set()
    for href in extractor.links:
        absolute = normalize_url(urljoin(base_url, href))
        if not absolute.startswith(("http://", "https://")):
            continue
        if looks_like_binary_asset(absolute):
            continue
        if not looks_like_product_url(absolute):
            continue
        # seed URL 側でカテゴリを指定し、そこから対象商品URLだけへ進む。
        if target_keywords and not matches_any_keyword(absolute, target_keywords):
            continue
        links.add(absolute)

    return sorted(links)


def extract_hermes_state(html: str) -> dict[str, Any] | None:
    """Angular SSR が埋め込む hermes-state JSON を取り出す。"""
    pattern = re.compile(
        r"<script[^>]+id=[\"']hermes-state[\"'][^>]*>(.*?)</script>",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(html)
    if not match:
        return None

    raw = unescape(match.group(1)).strip()
    if not raw:
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    return data if isinstance(data, dict) else None


def find_hermes_state_product_items(data: Any) -> list[dict[str, Any]]:
    """hermes-state の階層から products.items の商品 dict を集める。"""
    items: list[dict[str, Any]] = []

    if isinstance(data, dict):
        products = data.get("products")
        if isinstance(products, dict) and isinstance(products.get("items"), list):
            items.extend(item for item in products["items"] if isinstance(item, dict))

        for value in data.values():
            items.extend(find_hermes_state_product_items(value))
    elif isinstance(data, list):
        for value in data:
            items.extend(find_hermes_state_product_items(value))

    return items


def product_snapshot_from_hermes_state_item(
    base_url: str,
    item: dict[str, Any],
    target_keywords: Sequence[str],
    target_sizes: Sequence[str],
) -> ProductSnapshot | None:
    """hermes-state の商品 dict を監視用 snapshot へ変換する。"""
    product_url = hermes_state_product_url(base_url, as_text(item.get("url")))
    name = first_non_empty(
        as_text(item.get("title")),
        as_text(item.get("name")),
        slug_name_from_url(product_url),
    )
    sku = first_non_empty_or_none(
        normalize_sku(as_text(item.get("sku"))),
        extract_sku_from_text(f"{name} {product_url}"),
    )

    combined_for_match = " ".join(
        [
            name,
            sku or "",
            product_url,
            as_text(item.get("slug")),
            as_text(item.get("productCode")),
        ]
    )
    if target_keywords and not matches_any_keyword(combined_for_match, target_keywords):
        return None

    size = detect_size(
        target_sizes,
        name,
        sku or "",
        product_url,
        as_text(item.get("size")),
        as_text(item.get("slug")),
    )
    if target_sizes and size not in {target_size.upper() for target_size in target_sizes}:
        return None

    return ProductSnapshot(
        product_id=stable_product_id(sku, product_url),
        name=name,
        size=size,
        url=product_url,
        sku=sku,
        available=availability_from_hermes_state_item(item),
        availability_source="hermes-state",
    )


def hermes_state_product_url(base_url: str, value: str) -> str:
    """hermes-state の /product/... URL を国/言語prefix付きの絶対URLにする。"""
    if not value:
        return normalize_url(base_url)

    parsed_base = urlparse(base_url)
    if value.startswith("/product/"):
        path_parts = [part for part in parsed_base.path.split("/") if part]
        locale_prefix = "/".join(path_parts[:2])
        if locale_prefix:
            value = f"/{locale_prefix}{value}"

    return normalize_url(urljoin(base_url, value))


def availability_from_hermes_state_item(item: dict[str, Any]) -> bool:
    """カテゴリ商品JSONの stock 情報からオンライン購入可否を判定する。"""
    stock = item.get("stock")
    if not isinstance(stock, dict):
        return False

    if bool(stock.get("displayOnly")):
        return False

    return bool(stock.get("ecom")) or bool(stock.get("hasVariantInEcomStock"))


def extract_metadata(html: str) -> MetadataExtractor:
    """HTMLParser を実行し、メタ情報抽出結果を返す。"""
    parser = MetadataExtractor()
    parser.feed(html)
    return parser


def iter_jsonld_products(html: str) -> list[dict[str, Any]]:
    """application/ld+json から schema.org Product を取り出す。"""
    products: list[dict[str, Any]] = []
    pattern = re.compile(
        r"<script[^>]+type=[\"'][^\"']*ld\+json[^\"']*[\"'][^>]*>(.*?)</script>",
        re.IGNORECASE | re.DOTALL,
    )

    for match in pattern.finditer(html):
        raw = unescape(match.group(1)).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # サイト側の埋め込み JSON が壊れていても、HTML fallback で続行する。
            continue
        products.extend(find_schema_products(data))

    return products


def find_schema_products(data: Any) -> list[dict[str, Any]]:
    """JSON-LD の階層を再帰的にたどり Product node を集める。"""
    found: list[dict[str, Any]] = []

    if isinstance(data, list):
        for item in data:
            found.extend(find_schema_products(item))
    elif isinstance(data, dict):
        schema_type = data.get("@type")
        schema_types = schema_type if isinstance(schema_type, list) else [schema_type]
        if any(str(item).lower() == "product" for item in schema_types):
            found.append(data)
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in data:
                found.extend(find_schema_products(data[key]))

    return found


def availability_from_jsonld(product: dict[str, Any]) -> bool | None:
    """schema.org Offer availability を bool へ変換する。判定不能なら None。"""
    offers = product.get("offers")
    if not offers:
        return None

    offer_items = offers if isinstance(offers, list) else [offers]
    values = []
    for offer in offer_items:
        if isinstance(offer, dict):
            values.append(str(offer.get("availability", "")))

    joined = " ".join(values).lower()
    if any(token in joined for token in ("instock", "limitedavailability", "preorder")):
        return True
    if any(token in joined for token in ("outofstock", "soldout", "discontinued")):
        return False
    return None


def availability_from_text(text: str) -> tuple[bool, str]:
    """本文テキストの購入/在庫なし文言から購入可否を fallback 判定する。"""
    compact = compact_text(text)
    has_in_stock = any(compact_text(phrase) in compact for phrase in DEFAULT_IN_STOCK_PHRASES)
    has_out_of_stock = any(compact_text(phrase) in compact for phrase in DEFAULT_OUT_OF_STOCK_PHRASES)

    if has_in_stock:
        return True, "text-in-stock"
    if has_out_of_stock:
        return False, "text-out-of-stock"
    return False, "unknown"


def visible_text(html: str) -> str:
    """script/style を除いた人間向け本文テキストを作る。"""
    without_scripts = re.sub(
        r"<(script|style|noscript)[^>]*>.*?</\1>",
        " ",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    without_tags = re.sub(r"<[^>]+>", " ", without_scripts)
    return normalize_space(unescape(without_tags))


def detect_size(target_sizes: Sequence[str], *values: str) -> str:
    """商品名、SKU、URL、本文から GM/TGM などのサイズ表記を検出する。"""
    text = unicodedata.normalize("NFKC", " ".join(value for value in values if value)).upper()
    for size in sorted({item.upper() for item in target_sizes}, key=len, reverse=True):
        pattern = rf"(?<![A-Z0-9]){re.escape(size)}(?![A-Z0-9])"
        if re.search(pattern, text):
            return size
    return ""


def matches_any_keyword(value: str, keywords: Sequence[str]) -> bool:
    """通常表記と区切り除去表記の両方でキーワード一致を判定する。"""
    if not keywords:
        return True
    normalized = normalize_for_match(value)
    compact = compact_text(value)
    for keyword in keywords:
        if not keyword:
            continue
        if normalize_for_match(keyword) in normalized:
            return True
        if compact_text(keyword) in compact:
            return True
    return False


def normalize_url(url: str) -> str:
    """クロール済み判定を安定させるため fragment を落とす。"""
    without_fragment, _fragment = urldefrag(url)
    return without_fragment


def stable_product_id(sku: str | None, url: str) -> str:
    """SKU が取れれば SKU、なければ URL hash で安定 ID を作る。"""
    if sku:
        return f"sku#{normalize_sku(sku)}"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return f"url#{digest}"


def normalize_space(value: str) -> str:
    """連続空白や改行を 1 つの空白へ正規化する。"""
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_for_match(value: str) -> str:
    """表記揺れ比較用に Unicode と大小文字を正規化する。"""
    value = unicodedata.normalize("NFKC", unquote(unescape(value or ""))).lower()
    value = value.replace("’", "'").replace("`", "'")
    return normalize_space(value)


def compact_text(value: str) -> str:
    """区切り文字を除去し、シェーヌ・ダンクル等の表記揺れを吸収する。"""
    value = normalize_for_match(value)
    return re.sub(r"[\s\-_./'’`・:;,\u3000]+", "", value)


def first_non_empty(*values: str | None) -> str:
    """複数候補から最初の非空文字列を返す。"""
    for value in values:
        if value and value.strip():
            return normalize_space(value)
    return "Unknown product"


def first_non_empty_or_none(*values: str | None) -> str | None:
    """複数候補から最初の非空文字列を返し、なければ None にする。"""
    for value in values:
        if value and value.strip():
            return normalize_space(value)
    return None


def as_text(value: Any) -> str:
    """JSON 由来の値を安全に文字列へ寄せる。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def extract_sku_from_text(value: str) -> str:
    """Hermes の商品番号らしい H 始まりの識別子を抽出する。"""
    match = re.search(r"\bH[0-9A-Z]{6,}\b", value, flags=re.IGNORECASE)
    return normalize_sku(match.group(0)) if match else ""


def normalize_sku(value: str) -> str:
    """URL slug 由来の H101672Bv00011 を H101672B 00011 表記へ寄せる。"""
    normalized = normalize_space(value).upper()
    match = re.fullmatch(r"(H\d{6}[A-Z])V([0-9A-Z]+)", normalized)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return normalized


def slug_name_from_url(url: str) -> str:
    """メタ情報が取れない場合の最後の手段として URL slug から商品名候補を作る。"""
    path = urlparse(url).path.rstrip("/")
    slug = path.split("/")[-1] if path else ""
    slug = re.sub(r"-H[0-9A-Z]+$", "", slug, flags=re.IGNORECASE)
    return normalize_space(unquote(slug).replace("-", " "))


def looks_like_product_url(url: str) -> bool:
    """Hermes 商品ページらしい URL かを緩く判定する。"""
    parsed = urlparse(url)
    path = parsed.path.lower()
    return (
        "/product/" in path
        or "/products/" in path
        or bool(re.search(r"(?:^|[-/])h[0-9a-z]{6,}(?:/|$)", path))
    )


def looks_like_binary_asset(url: str) -> bool:
    """画像や JS/CSS など、HTML として取得しない asset URL を除外する。"""
    path = urlparse(url).path.lower()
    return path.endswith(
        (
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".webp",
            ".avif",
            ".svg",
            ".css",
            ".js",
            ".woff",
            ".woff2",
            ".ttf",
            ".ico",
        )
    )

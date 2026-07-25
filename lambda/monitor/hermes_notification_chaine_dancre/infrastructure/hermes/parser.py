from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from html import unescape
from html.parser import HTMLParser
from typing import Any, Sequence
from urllib.parse import urldefrag, urljoin, urlparse

from hermes_notification_chaine_dancre.domain.models import ProductSnapshot


DEFAULT_IN_STOCK_PHRASES = (
    "add to cart",
    "add to bag",
    "add to shopping bag",
    "カートに追加",
    "バッグに追加",
    "ショッピングバッグに追加",
    "購入する",
)

DEFAULT_OUT_OF_STOCK_PHRASES = (
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
    metadata = extract_metadata(html)
    json_products = list(iter_jsonld_products(html))
    json_product = json_products[0] if json_products else {}

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

    visible = visible_text(html)
    combined_for_match = " ".join([name, sku or "", canonical_url, visible[:5000]])
    if target_keywords and not matches_any_keyword(combined_for_match, target_keywords):
        return None

    size = detect_size(target_sizes, name, sku or "", canonical_url, visible[:3000])
    if target_sizes and size not in {item.upper() for item in target_sizes}:
        return None

    schema_available = availability_from_jsonld(json_product)
    if schema_available is not None:
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


def extract_product_links(
    html: str,
    base_url: str,
    target_keywords: Sequence[str],
) -> list[str]:
    extractor = LinkExtractor()
    extractor.feed(html)

    links = set()
    for href in extractor.links:
        absolute = normalize_url(urljoin(base_url, href))
        if not absolute.startswith(("http://", "https://")):
            continue
        if looks_like_binary_asset(absolute):
            continue
        if looks_like_product_url(absolute) or matches_any_keyword(absolute, target_keywords):
            links.add(absolute)

    return sorted(links)


def extract_metadata(html: str) -> MetadataExtractor:
    parser = MetadataExtractor()
    parser.feed(html)
    return parser


def iter_jsonld_products(html: str) -> list[dict[str, Any]]:
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
            continue
        products.extend(find_schema_products(data))

    return products


def find_schema_products(data: Any) -> list[dict[str, Any]]:
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
    compact = compact_text(text)
    has_in_stock = any(compact_text(phrase) in compact for phrase in DEFAULT_IN_STOCK_PHRASES)
    has_out_of_stock = any(compact_text(phrase) in compact for phrase in DEFAULT_OUT_OF_STOCK_PHRASES)

    if has_in_stock:
        return True, "text-in-stock"
    if has_out_of_stock:
        return False, "text-out-of-stock"
    return False, "unknown"


def visible_text(html: str) -> str:
    without_scripts = re.sub(
        r"<(script|style|noscript)[^>]*>.*?</\1>",
        " ",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    without_tags = re.sub(r"<[^>]+>", " ", without_scripts)
    return normalize_space(unescape(without_tags))


def detect_size(target_sizes: Sequence[str], *values: str) -> str:
    text = unicodedata.normalize("NFKC", " ".join(value for value in values if value)).upper()
    for size in sorted({item.upper() for item in target_sizes}, key=len, reverse=True):
        pattern = rf"(?<![A-Z0-9]){re.escape(size)}(?![A-Z0-9])"
        if re.search(pattern, text):
            return size
    return ""


def matches_any_keyword(value: str, keywords: Sequence[str]) -> bool:
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
    without_fragment, _fragment = urldefrag(url)
    return without_fragment


def stable_product_id(sku: str | None, url: str) -> str:
    if sku:
        return f"sku#{sku}"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return f"url#{digest}"


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_for_match(value: str) -> str:
    value = unicodedata.normalize("NFKC", unescape(value or "")).lower()
    value = value.replace("’", "'").replace("`", "'")
    return normalize_space(value)


def compact_text(value: str) -> str:
    value = normalize_for_match(value)
    return re.sub(r"[\s\-_./'’`・:;,\u3000]+", "", value)


def first_non_empty(*values: str | None) -> str:
    for value in values:
        if value and value.strip():
            return normalize_space(value)
    return "Unknown product"


def first_non_empty_or_none(*values: str | None) -> str | None:
    for value in values:
        if value and value.strip():
            return normalize_space(value)
    return None


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def extract_sku_from_text(value: str) -> str:
    match = re.search(r"\bH[0-9A-Z]{6,}\b", value, flags=re.IGNORECASE)
    return match.group(0).upper() if match else ""


def slug_name_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    slug = path.split("/")[-1] if path else ""
    slug = re.sub(r"-H[0-9A-Z]+$", "", slug, flags=re.IGNORECASE)
    return normalize_space(slug.replace("-", " "))


def looks_like_product_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    return "/product/" in path or "/products/" in path or "-h" in path


def looks_like_binary_asset(url: str) -> bool:
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


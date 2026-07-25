from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProductSnapshot:
    product_id: str
    name: str
    size: str
    url: str
    available: bool
    availability_source: str
    sku: str | None = None


@dataclass(frozen=True)
class ProductState:
    product_id: str
    name: str
    size: str
    url: str
    available: bool
    availability_source: str
    last_seen_at: str
    sku: str | None = None
    last_available_at: str | None = None
    last_notification_at: str | None = None

    @classmethod
    def from_snapshot(
        cls,
        snapshot: ProductSnapshot,
        checked_at: str,
        previous: ProductState | None = None,
        notified: bool = False,
    ) -> ProductState:
        last_available_at = previous.last_available_at if previous else None
        last_notification_at = previous.last_notification_at if previous else None

        if snapshot.available:
            last_available_at = checked_at
        if notified:
            last_notification_at = checked_at

        return cls(
            product_id=snapshot.product_id,
            name=snapshot.name,
            size=snapshot.size,
            url=snapshot.url,
            sku=snapshot.sku,
            available=snapshot.available,
            availability_source=snapshot.availability_source,
            last_seen_at=checked_at,
            last_available_at=last_available_at,
            last_notification_at=last_notification_at,
        )


@dataclass(frozen=True)
class RestockEvent:
    snapshot: ProductSnapshot
    previous_available: bool | None
    checked_at: str


@dataclass(frozen=True)
class MonitorResult:
    checked_at: str
    seed_url_count: int
    matched_products: int
    notifications: int
    notified_product_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "checked_at": self.checked_at,
            "seed_urls": self.seed_url_count,
            "matched_products": self.matched_products,
            "notifications": self.notifications,
            "notified_product_ids": list(self.notified_product_ids),
        }


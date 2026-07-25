"""入荷監視ドメインで使う値オブジェクトと結果モデル。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProductSnapshot:
    """今回クロールで観測した商品の状態。"""

    product_id: str
    name: str
    size: str
    url: str
    available: bool
    availability_source: str
    sku: str | None = None


@dataclass(frozen=True)
class ProductState:
    """DynamoDB に保存する前回までの商品状態。"""

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
        # 前回の履歴時刻を引き継ぎつつ、今回の観測結果で必要な時刻だけ更新する。
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
    """通知 adapter へ渡す入荷イベント。"""

    snapshot: ProductSnapshot
    previous_available: bool | None
    checked_at: str


@dataclass(frozen=True)
class MonitorResult:
    """Lambda handler が返す監視実行結果。"""

    checked_at: str
    seed_url_count: int
    matched_products: int
    notifications: int
    notified_product_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """AWS Lambda の戻り値として扱いやすい dict に変換する。"""
        return {
            "checked_at": self.checked_at,
            "seed_urls": self.seed_url_count,
            "matched_products": self.matched_products,
            "notifications": self.notifications,
            "notified_product_ids": list(self.notified_product_ids),
        }

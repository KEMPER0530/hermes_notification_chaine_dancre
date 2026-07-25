"""application 層が外部I/Oを抽象化して扱うためのポート定義。"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence

from hermes_notification_chaine_dancre.application.config import MonitorConfig
from hermes_notification_chaine_dancre.domain.models import (
    ProductSnapshot,
    ProductState,
    RestockEvent,
)


class ProductCrawler(Protocol):
    """商品ページを取得して現在状態を返すポート。"""

    def crawl(self, config: MonitorConfig) -> Sequence[ProductSnapshot]:
        ...


class ProductStateRepository(Protocol):
    """商品状態の永続化を扱うポート。"""

    def get(self, product_id: str) -> ProductState | None:
        ...

    def save(self, state: ProductState) -> None:
        ...


class RestockNotifier(Protocol):
    """入荷イベント通知を扱うポート。"""

    def publish(self, event: RestockEvent) -> None:
        ...


class Clock(Protocol):
    """現在時刻取得を差し替え可能にするポート。"""

    def now(self) -> datetime:
        ...

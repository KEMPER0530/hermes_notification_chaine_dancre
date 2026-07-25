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
    def crawl(self, config: MonitorConfig) -> Sequence[ProductSnapshot]:
        ...


class ProductStateRepository(Protocol):
    def get(self, product_id: str) -> ProductState | None:
        ...

    def save(self, state: ProductState) -> None:
        ...


class RestockNotifier(Protocol):
    def publish(self, event: RestockEvent) -> None:
        ...


class Clock(Protocol):
    def now(self) -> datetime:
        ...


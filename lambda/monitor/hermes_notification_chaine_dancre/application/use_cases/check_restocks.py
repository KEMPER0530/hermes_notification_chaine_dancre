"""商品状態を比較し、入荷時だけ通知するユースケース。"""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from hermes_notification_chaine_dancre.application.config import MonitorConfig
from hermes_notification_chaine_dancre.application.ports import (
    Clock,
    ProductCrawler,
    ProductStateRepository,
    RestockNotifier,
)
from hermes_notification_chaine_dancre.domain.models import (
    MonitorResult,
    ProductState,
    RestockEvent,
)
from hermes_notification_chaine_dancre.domain.services import RestockPolicy


logger = logging.getLogger(__name__)


class CheckRestocksUseCase:
    """クロール、状態保存、通知を 1 回分の監視処理として調停する。"""

    def __init__(
        self,
        crawler: ProductCrawler,
        repository: ProductStateRepository,
        notifier: RestockNotifier,
        clock: Clock,
        policy: RestockPolicy,
    ) -> None:
        # 具体的な AWS/HTTP 実装は受け取るだけにして、ユースケースを純粋に保つ。
        self._crawler = crawler
        self._repository = repository
        self._notifier = notifier
        self._clock = clock
        self._policy = policy

    def execute(self, config: MonitorConfig) -> MonitorResult:
        logger.info("Checking %s Hermes seed URL(s)", len(config.seed_urls))

        snapshots = list(self._crawler.crawl(config))
        # 通知本文と DynamoDB の時刻は設定されたタイムゾーンで統一する。
        checked_at = self._clock.now().astimezone(ZoneInfo(config.notification_timezone)).isoformat()
        notified_product_ids: list[str] = []

        for snapshot in snapshots:
            previous = self._repository.get(snapshot.product_id)
            should_notify = self._policy.should_notify(previous, snapshot)

            if should_notify:
                # 通知 payload は domain のイベントとしてまとめ、SNS 固有の形式は adapter に任せる。
                self._notifier.publish(
                    RestockEvent(
                        snapshot=snapshot,
                        previous_available=previous.available if previous else None,
                        checked_at=checked_at,
                    )
                )
                notified_product_ids.append(snapshot.product_id)

            # 通知有無にかかわらず、次回比較のために最新状態を保存する。
            state = ProductState.from_snapshot(
                snapshot=snapshot,
                checked_at=checked_at,
                previous=previous,
                notified=should_notify,
            )
            self._repository.save(state)

        return MonitorResult(
            checked_at=checked_at,
            seed_url_count=len(config.seed_urls),
            matched_products=len(snapshots),
            notifications=len(notified_product_ids),
            notified_product_ids=tuple(notified_product_ids),
        )

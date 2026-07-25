from __future__ import annotations

from typing import Any

from hermes_notification_chaine_dancre.domain.models import RestockEvent


class SnsRestockNotifier:
    def __init__(self, sns_client: Any, topic_arn: str) -> None:
        self._sns_client = sns_client
        self._topic_arn = topic_arn

    def publish(self, event: RestockEvent) -> None:
        snapshot = event.snapshot
        previous_label = (
            "未記録" if event.previous_available is None else str(event.previous_available)
        )
        subject = f"Hermes入荷通知: {snapshot.name[:70]}"
        message = "\n".join(
            [
                "シェーヌダンクルの対象商品が購入可能として検知されました。",
                "",
                f"商品名: {snapshot.name}",
                f"サイズ: {snapshot.size}",
                f"商品番号: {snapshot.sku or '不明'}",
                f"URL: {snapshot.url}",
                f"前回購入可否: {previous_label}",
                f"今回購入可否: {snapshot.available}",
                f"判定元: {snapshot.availability_source}",
                f"確認時刻(JST): {event.checked_at}",
            ]
        )
        self._sns_client.publish(
            TopicArn=self._topic_arn,
            Subject=subject,
            Message=message,
        )

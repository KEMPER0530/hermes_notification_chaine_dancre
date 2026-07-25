"""入荷通知の業務ルールを定義する domain service。"""

from __future__ import annotations

from hermes_notification_chaine_dancre.domain.models import ProductSnapshot, ProductState


class RestockPolicy:
    """前回状態と今回状態から通知すべきかを判定する。"""

    def __init__(self, notify_on_first_available: bool) -> None:
        self._notify_on_first_available = notify_on_first_available

    def should_notify(
        self,
        previous: ProductState | None,
        current: ProductSnapshot,
    ) -> bool:
        # 購入不可の観測では通知しない。通知対象は available=True のみ。
        if not current.available:
            return False
        if previous is None:
            return self._notify_on_first_available
        # 前回 unavailable、今回 available の遷移だけを入荷として扱う。
        return previous.available is False

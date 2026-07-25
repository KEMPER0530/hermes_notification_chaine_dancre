from __future__ import annotations

from hermes_notification_chaine_dancre.domain.models import ProductSnapshot, ProductState


class RestockPolicy:
    def __init__(self, notify_on_first_available: bool) -> None:
        self._notify_on_first_available = notify_on_first_available

    def should_notify(
        self,
        previous: ProductState | None,
        current: ProductSnapshot,
    ) -> bool:
        if not current.available:
            return False
        if previous is None:
            return self._notify_on_first_available
        return previous.available is False


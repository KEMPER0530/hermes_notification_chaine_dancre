from __future__ import annotations

from typing import Any

from hermes_notification_chaine_dancre.domain.models import ProductState


class DynamoDbProductStateRepository:
    def __init__(self, table: Any) -> None:
        self._table = table

    def get(self, product_id: str) -> ProductState | None:
        response = self._table.get_item(Key={"id": product_id})
        item = response.get("Item")
        if not item:
            return None
        return self._to_state(item)

    def save(self, state: ProductState) -> None:
        item: dict[str, object] = {
            "id": state.product_id,
            "name": state.name,
            "size": state.size,
            "url": state.url,
            "sku": state.sku or "",
            "available": state.available,
            "availability_source": state.availability_source,
            "last_seen_at": state.last_seen_at,
        }
        if state.last_available_at:
            item["last_available_at"] = state.last_available_at
        if state.last_notification_at:
            item["last_notification_at"] = state.last_notification_at
        self._table.put_item(Item=item)

    def _to_state(self, item: dict[str, Any]) -> ProductState:
        return ProductState(
            product_id=str(item["id"]),
            name=str(item.get("name", "")),
            size=str(item.get("size", "")),
            url=str(item.get("url", "")),
            sku=str(item["sku"]) if item.get("sku") else None,
            available=bool(item.get("available", False)),
            availability_source=str(item.get("availability_source", "unknown")),
            last_seen_at=str(item.get("last_seen_at", "")),
            last_available_at=str(item["last_available_at"])
            if item.get("last_available_at")
            else None,
            last_notification_at=str(item["last_notification_at"])
            if item.get("last_notification_at")
            else None,
        )


from __future__ import annotations

import json
from typing import Any

from foxbot.storage import BotStore

_GLOBAL = "__global__"
_ITEM_PREFIX = "economy_shop_item:"
_PURCHASE_PREFIX = "economy_shop_purchases:"
_COIN_TYPES = {"bronze", "silver", "gold"}


def add_item(
    store: BotStore,
    item_id: str,
    title: str,
    price: int,
    coin_type: str = "bronze",
    *,
    description: str | None = None,
    stock: int | None = None,
    payload: Any = None,
) -> dict[str, Any]:
    identifier, name = str(item_id or "").strip(), str(title or "").strip()
    if not identifier or len(identifier) > 80:
        raise ValueError("shop item ID must contain 1 to 80 characters")
    if not name or len(name) > 120:
        raise ValueError("shop item title must contain 1 to 120 characters")
    if isinstance(price, bool) or not isinstance(price, int) or price <= 0:
        raise ValueError("shop item price must be a positive integer")
    if coin_type not in _COIN_TYPES:
        raise ValueError("shop item coin type is invalid")
    if stock is not None and (isinstance(stock, bool) or not isinstance(stock, int) or stock < 0):
        raise ValueError("shop item stock must be a non-negative integer")
    if description is not None and len(str(description)) > 500:
        raise ValueError("shop item description is too long")
    item: dict[str, Any] = {
        "id": identifier,
        "title": name,
        "price": price,
        "coin_type": coin_type,
    }
    if description:
        item["description"] = str(description)
    if stock is not None:
        item["stock"] = stock
    if payload is not None:
        try:
            json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("shop item payload must be JSON serializable") from exc
        item["payload"] = payload
    store.set_group_state(_GLOBAL, f"{_ITEM_PREFIX}{identifier}", item)
    return dict(item)


def remove_item(store: BotStore, item_id: str) -> bool:
    return store.delete_group_state(_GLOBAL, f"{_ITEM_PREFIX}{item_id!s}")


def clear_items(store: BotStore) -> None:
    for group_id, key, _item in store.list_group_states_prefix(_ITEM_PREFIX, limit=5000):
        if group_id == _GLOBAL:
            store.delete_group_state(group_id, key)


def get_item(store: BotStore, item_id: str) -> dict[str, Any] | None:
    item = store.get_group_state(_GLOBAL, f"{_ITEM_PREFIX}{item_id!s}")
    return dict(item) if isinstance(item, dict) else None


def list_items(store: BotStore) -> list[dict[str, Any]]:
    items = [
        dict(item)
        for group_id, _key, item in store.list_group_states_prefix(_ITEM_PREFIX, limit=5000)
        if group_id == _GLOBAL and isinstance(item, dict)
    ]
    return sorted(items, key=lambda item: (str(item.get("coin_type", "")), int(item.get("price", 0))))


def purchases(store: BotStore, group_id: str | int, user_id: str | int) -> list[dict[str, Any]]:
    records = store.get_group_state(group_id, f"{_PURCHASE_PREFIX}{user_id}", [])
    if not isinstance(records, list):
        return []
    return [dict(record) for record in records if isinstance(record, dict)]

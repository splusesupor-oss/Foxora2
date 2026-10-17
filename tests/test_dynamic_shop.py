from __future__ import annotations

import asyncio
from types import SimpleNamespace

from foxbot import shop_catalog
from foxbot.public_commands import handle_public_command
from foxbot.storage import BotStore


class Event:
    def __init__(self, text: str, user_id: str = "buyer") -> None:
        self.raw_text = text
        self.sender_id = user_id
        self.sender = SimpleNamespace(id=user_id, first_name="Shop", last_name="Buyer", username=user_id)
        self.replies: list[str] = []


async def _dispatch(event: Event, store: BotStore) -> bool:
    async def reply(_event, text, *, formatting_entities=None):
        event.replies.append(text)
        return SimpleNamespace(id=len(event.replies))

    return await handle_public_command(
        event, store, group_id="g", actor_id=event.sender_id, text=event.raw_text, reply=reply,
    )


def test_dynamic_items_render_and_purchase_atomically_in_the_economy_menu() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        shop_catalog.add_item(
            store, "special-kit", "کیت ویژه", 12, "bronze",
            description="آیتم آزمایشی", stock=1,
        )
        store.award_group_coins("g", "buyer", 20, "bronze", reference="shop-funds")
        store.award_group_coins("g", "peer", 8, "bronze", reference="shop-peer")

        entry = Event("فروشگاه")
        assert await _dispatch(entry, store)
        assert "کیت ویژه" in entry.replies[-1]
        assert "آیتم‌های موجود: ۳۳" in entry.replies[-1]
        assert "آیتم آزمایشی" in entry.replies[-1]

        choose_list = Event("۱")
        assert await _dispatch(choose_list, store)
        assert "🛍 خرید آیتم" in choose_list.replies[-1]
        assert "special-kit" not in choose_list.replies[-1]
        select = Event("special-kit")
        assert await _dispatch(select, store)
        assert "کیت ویژه" in select.replies[-1]
        confirm = Event("بله")
        assert await _dispatch(confirm, store)
        assert store.get_economy_profile("g", "buyer")["bronze"] == 8
        assert store.group_coin_rank("g", "peer") == 1
        assert store.group_coin_rank("g", "buyer") == 2
        assert store.get_group_state("__global__", "economy_shop_item:special-kit")["stock"] == 0
        history = store.economy_transaction_history("g", "buyer")
        assert any(row["note"] == "purchase:special-kit" for row in history)

        second_user = Event("فروشگاه", user_id="second")
        assert await _dispatch(second_user, store)
        assert "ناموجود" in second_user.replies[-1]
        store.close()

    asyncio.run(scenario())


def test_dynamic_shop_stock_and_debit_are_serialized_as_one_sqlite_transaction() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        shop_catalog.add_item(store, "limited", "آیتم محدود", 10, "silver", stock=1)
        for user_id in ("a", "b"):
            store.award_group_coins("g", user_id, 10, "silver", reference=f"fund:{user_id}")

        async def buy(user_id: str):
            return await asyncio.to_thread(
                store.buy_dynamic_shop_item,
                "g", user_id, "limited",
                reference=f"buy:{user_id}",
            )

        results = await asyncio.gather(buy("a"), buy("b"))
        outcomes = sorted(result[2] for result in results)
        assert outcomes == ["out_of_stock", "purchased"]
        remaining = store.get_group_state("__global__", "economy_shop_item:limited")
        assert remaining["stock"] == 0
        assert sum(store.get_economy_profile("g", uid)["silver"] for uid in ("a", "b")) == 10

        winner = next(uid for uid in ("a", "b") if store.get_economy_profile("g", uid)["silver"] == 0)
        _item, _balance, outcome = store.buy_dynamic_shop_item(
            "g", winner, "limited", reference=f"buy:{winner}"
        )
        assert outcome == "duplicate"
        assert store.get_economy_profile("g", winner)["silver"] == 0
        store.close()

    asyncio.run(scenario())

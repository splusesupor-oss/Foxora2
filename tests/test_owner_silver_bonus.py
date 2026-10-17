from __future__ import annotations

from foxbot.storage import BotStore

_OWNER_SILVER_TEST_GROUP = 12345
_OWNER_SILVER_TEST_ALIAS = -(1_000_000_000_000 + _OWNER_SILVER_TEST_GROUP)


def _store() -> BotStore:
    return BotStore(
        ":memory:",
        global_owner_check=lambda user_id: str(user_id) == "global-owner",
        owner_silver_group_ids=(_OWNER_SILVER_TEST_GROUP,),
    )


def test_virtual_silver_is_limited_to_global_owner_in_allowlisted_groups() -> None:
    store = _store()
    try:
        owner = store.get_economy_profile(_OWNER_SILVER_TEST_GROUP, "global-owner")
        assert owner["silver"] == 200_000
        assert owner["total_coin_value"] == 2_000_000

        aliased_owner = store.get_economy_profile(_OWNER_SILVER_TEST_ALIAS, "global-owner")
        assert aliased_owner["silver"] == 200_000

        other_group = store.get_economy_profile("not-allowlisted", "global-owner")
        group_owner = store.get_economy_profile(_OWNER_SILVER_TEST_GROUP, "group-owner")
        assert other_group["silver"] == 0
        assert group_owner["silver"] == 0
    finally:
        store.close()


def test_owner_silver_bonus_is_disabled_without_configured_groups() -> None:
    store = BotStore(
        ":memory:",
        global_owner_check=lambda user_id: str(user_id) == "global-owner",
    )
    try:
        profile = store.get_economy_profile(
            _OWNER_SILVER_TEST_GROUP, "global-owner"
        )
        assert profile["silver"] == 0
    finally:
        store.close()


def test_owner_silver_award_spend_conversion_and_transfer_are_idempotent() -> None:
    store = _store()
    try:
        reference = "owner-silver-award"
        profile, outcome = store.award_group_coins(
            _OWNER_SILVER_TEST_GROUP, "global-owner", 7, "silver",
            reference=reference, note="game:vampire", return_outcome=True,
        )
        assert outcome == "awarded"
        assert profile["silver"] == 200_000
        profile, outcome = store.award_group_coins(
            _OWNER_SILVER_TEST_GROUP, "global-owner", 7, "silver",
            reference=reference, note="game:vampire", return_outcome=True,
        )
        assert outcome == "duplicate"
        assert profile["silver"] == 200_000

        profile, outcome = store.spend_group_coins(
            _OWNER_SILVER_TEST_GROUP, "global-owner", 250_000, "silver",
            reference="owner-silver-spend", note="test-spend",
        )
        assert outcome == "spent"
        assert profile["silver"] == 200_000
        profile, outcome = store.spend_group_coins(
            _OWNER_SILVER_TEST_GROUP, "global-owner", 250_000, "silver",
            reference="owner-silver-spend", note="test-spend",
        )
        assert outcome == "duplicate"
        assert profile["silver"] == 200_000

        profile, outcome = store.convert_group_coins(
            _OWNER_SILVER_TEST_GROUP, "global-owner", "silver", "gold", 70, 10,
            reference="owner-silver-convert", note="conversion:test",
        )
        assert outcome == "converted"
        assert profile["silver"] == 200_000
        assert profile["gold"] == 10
        profile, outcome = store.convert_group_coins(
            _OWNER_SILVER_TEST_GROUP, "global-owner", "silver", "gold", 70, 10,
            reference="owner-silver-convert", note="conversion:test",
        )
        assert outcome == "duplicate"
        assert profile["gold"] == 10

        sender, receiver, outcome = store.transfer_group_coins(
            _OWNER_SILVER_TEST_GROUP, "global-owner", "recipient", "silver", 500_000,
            reference="owner-silver-transfer", sender_name="Owner",
        )
        assert outcome == "transferred"
        assert sender["silver"] == 200_000
        assert receiver["silver"] == 500_000
        sender, receiver, outcome = store.transfer_group_coins(
            _OWNER_SILVER_TEST_GROUP, "global-owner", "recipient", "silver", 500_000,
            reference="owner-silver-transfer", sender_name="Owner",
        )
        assert outcome == "duplicate"
        assert sender["silver"] == 200_000
        assert receiver["silver"] == 500_000
    finally:
        store.close()


def test_owner_silver_daily_reward_and_profile_shop_purchase_keep_virtual_balance() -> None:
    store = _store()
    try:
        profile, outcome, wait = store.claim_group_daily_coins(
            _OWNER_SILVER_TEST_GROUP, "global-owner", 25, now_epoch=1_800_000_000,
            display_name="Owner", rewards={"bronze": 25, "silver": 4, "gold": 0},
        )
        assert outcome == "claimed"
        assert wait == 0
        assert profile["bronze"] == 25
        assert profile["silver"] == 200_000

        profile_item = {
            "id": "silver-star", "kind": "star", "level": 1,
            "price": 200, "coin_type": "silver",
        }
        _profile, balance, outcome = store.buy_group_profile_item(
            _OWNER_SILVER_TEST_GROUP, "global-owner", profile_item,
            reference="owner-silver-profile-item", display_name="Owner",
        )
        assert outcome == "purchased"
        assert balance["silver"] == 200_000
        _profile, balance, outcome = store.buy_group_profile_item(
            _OWNER_SILVER_TEST_GROUP, "global-owner", profile_item,
            reference="owner-silver-profile-item", display_name="Owner",
        )
        assert outcome == "duplicate"
        assert balance["silver"] == 200_000

        store.set_group_state(
            "__global__", "economy_shop_item:owner-limited",
            {
                "id": "owner-limited", "title": "Silver item",
                "price": 500, "coin_type": "silver", "stock": 1,
            },
        )
        item, balance, outcome = store.buy_dynamic_shop_item(
            _OWNER_SILVER_TEST_GROUP, "global-owner", "owner-limited",
            reference="owner-silver-dynamic-item", display_name="Owner",
        )
        assert outcome == "purchased"
        assert item["stock"] == 0
        assert balance["silver"] == 200_000
        item, balance, outcome = store.buy_dynamic_shop_item(
            _OWNER_SILVER_TEST_GROUP, "global-owner", "owner-limited",
            reference="owner-silver-dynamic-item", display_name="Owner",
        )
        assert outcome == "duplicate"
        assert item["stock"] == 0
        assert balance["silver"] == 200_000
    finally:
        store.close()

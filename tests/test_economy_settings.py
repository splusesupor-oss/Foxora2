from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from foxbot import economy_settings
from foxbot.public_commands import handle_public_command
from foxbot.storage import BotStore


class Event:
    def __init__(self, text: str) -> None:
        self.raw_text = text
        self.replies: list[str] = []


async def _dispatch(event: Event, store: BotStore) -> bool:
    async def reply(_event, text, *, formatting_entities=None):
        event.replies.append(text)
        return SimpleNamespace(id=len(event.replies))

    return await handle_public_command(
        event, store, group_id="g", actor_id="user", text=event.raw_text, reply=reply,
    )


def test_persisted_settings_drive_wallet_conversions_daily_rewards_and_coin_value(
    tmp_path, monkeypatch,
) -> None:
    async def scenario() -> None:
        config_path = tmp_path / "economy_settings.json"
        monkeypatch.setenv("FOX_ECONOMY_SETTINGS_FILE", str(config_path))
        economy_settings.reset_cache()
        assert economy_settings.load()["BronzeToSilverCost"] == 100
        config = economy_settings.save({
            "BronzeToSilverCost": 200,
            "BronzeToSilverGain": 15,
            "SilverValue": 20,
            "DailyRewardBronze": 0,
            "DailyRewardSilver": 3,
            "DailyRewardGold": 1,
            "DailyRewardCooldownSeconds": 3600,
        })
        assert config["BronzeToSilverCost"] == 200

        store = BotStore(":memory:")
        store.award_group_coins("g", "user", 200, "bronze", reference="seed")
        assert store.get_economy_profile("g", "user")["total_coin_value"] == 200

        wallet = Event("موجودی")
        assert await _dispatch(wallet, store)
        assert "تبدیل برنز به نقره (۲۰۰ ➜ ۱۵)" in wallet.replies[-1]
        convert = Event("۱")
        assert await _dispatch(convert, store)
        profile = store.get_economy_profile("g", "user")
        assert profile["bronze"] == 0 and profile["silver"] == 15
        assert profile["total_coin_value"] == 300

        reopen = Event("موجودی")
        assert await _dispatch(reopen, store)
        daily = Event("۷")
        assert await _dispatch(daily, store)
        profile = store.get_economy_profile("g", "user")
        assert (profile["silver"], profile["gold"]) == (18, 1)
        assert profile["total_coin_value"] == 460
        assert "نقره +۳، طلا +۱" in daily.replies[-1]
        assert "💎 ارزش کل: ۴۶۰" in daily.replies[-1]
        assert "💰 کیف پول شما" not in daily.replies[-1]

        reopen_again = Event("موجودی")
        assert await _dispatch(reopen_again, store)
        cooldown = Event("۷")
        assert await _dispatch(cooldown, store)
        assert "نوبت بعدی: ۵۹ دقیقه دیگر" in cooldown.replies[-1]
        store.close()
        economy_settings.reset_cache()

    asyncio.run(scenario())


def test_economy_settings_bound_file_size_and_numeric_values(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "settings.json"
    monkeypatch.setenv("FOX_ECONOMY_SETTINGS_FILE", str(config_path))
    economy_settings.reset_cache()
    try:
        config_path.write_text(
            json.dumps({"BronzeValue": 5, "GoldValue": 1_000_000_001}),
            encoding="utf-8",
        )
        economy_settings.reset_cache()
        loaded = economy_settings.load()
        assert loaded["BronzeValue"] == 5
        assert loaded["GoldValue"] == economy_settings.DEFAULTS["GoldValue"]

        config_path.write_text(" " * 65_537, encoding="utf-8")
        assert economy_settings.load() == loaded

        with pytest.raises(ValueError, match="invalid economy setting"):
            economy_settings.save({"GoldValue": 1_000_000_001})
    finally:
        economy_settings.reset_cache()


def test_settings_reject_unknown_or_invalid_values(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FOX_ECONOMY_SETTINGS_FILE", str(tmp_path / "settings.json"))
    economy_settings.reset_cache()
    try:
        try:
            economy_settings.save({"Unknown": 1})
        except ValueError as exc:
            assert "unknown" in str(exc)
        else:
            raise AssertionError("unknown setting was accepted")
        try:
            economy_settings.save({"SilverValue": -1})
        except ValueError as exc:
            assert "invalid" in str(exc)
        else:
            raise AssertionError("negative coin value was accepted")
    finally:
        economy_settings.reset_cache()

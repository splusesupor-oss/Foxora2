from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import foxbot.runtime as runtime_module
from foxbot.filters import (
    MAX_GLOBAL_TERM_LENGTH,
    MAX_GLOBAL_TERMS,
    MAX_GLOBAL_WORD_FILE_BYTES,
    WordFilter,
)
from foxbot.login import main as login_main
from foxbot.rpc_queue import SerialRpcQueue
from foxbot.runtime import (
    GlobalWordListReloader,
    RuntimeSettings,
    _process_scheduled_actions,
    build_runtime,
    create_soroush_client,
    main,
    run_bot,
)
from foxbot.storage import BotStore


def test_cli_help_exits_without_starting_runtime(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["fox-soroush-bot", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    assert "authorized SPlusthon session" in capsys.readouterr().out


def test_login_cli_help_exits_without_login(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["fox-soroush-login", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        login_main()
    assert exc_info.value.code == 0
    assert "interactive terminal" in capsys.readouterr().out


class FakeClient:
    def __init__(self) -> None:
        self.handlers = []
        self.connected = False
        self.disconnected = False
        self.started = False

    async def connect(self) -> None:
        self.connected = True

    async def is_user_authorized(self) -> bool:
        return True

    async def get_me(self):
        return SimpleNamespace(id=123)

    def add_event_handler(self, callback, builder) -> None:
        self.handlers.append((callback, builder))

    async def run_until_disconnected(self) -> None:
        self.started = True

    async def disconnect(self) -> None:
        self.disconnected = True


def make_settings(tmp_path: Path, **kwargs) -> RuntimeSettings:
    defaults = {
        "session_path": tmp_path / "data" / "soroush",
        "session_string": None,
        "api_id": 12345,
        "api_hash": "test-api-hash",
        "database_path": tmp_path / "data" / "foxbot.sqlite3",
        "owner_path": tmp_path / "owner.json",
        "global_words_path": tmp_path / "words.txt",
        "rpc_max_pending_total": 20,
        "rpc_max_pending_per_group": 4,
        "rpc_min_interval_seconds": 0,
        "mute_duration_seconds": None,
        "page_size": 50,
    }
    defaults.update(kwargs)
    return RuntimeSettings(**defaults)


def test_runtime_settings_parse_env_and_reject_invalid_mute_duration(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FOX_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FOX_CONFIG_FILE", str(tmp_path / "no-config.json"))
    monkeypatch.setenv("SOROUSH_API_ID", "12345")
    monkeypatch.setenv("SOROUSH_API_HASH", "test-api-hash")
    monkeypatch.setenv("FOX_BOT_DB", str(tmp_path / "custom.sqlite3"))
    monkeypatch.setenv("FOX_LEGACY_ADMINS_FILE", str(tmp_path / "admins.json"))
    monkeypatch.setenv("RPC_MIN_INTERVAL_SECONDS", "0.5")
    monkeypatch.setenv("FOX_MEMBER_PAGE_SIZE", "75")
    monkeypatch.setenv("FOX_MUTE_DURATION_SECONDS", "3600")
    monkeypatch.setenv("FOX_ACTION_DURATION_SECONDS", "7200")
    monkeypatch.setenv("FOX_SPAM_THRESHOLD", "4")
    monkeypatch.setenv("FOX_ACTION_ON_THRESHOLD", "mute")
    monkeypatch.setenv("FOX_OWNER_SILVER_GROUP_IDS", "12345, 67890, 12345")
    monkeypatch.setenv("FOX_CHECK_LINKS", "off")
    monkeypatch.setenv("FOX_DELETE_SPAM", "false")
    monkeypatch.setenv("FOX_CHECK_BANNED_WORDS", "false")
    monkeypatch.setenv("FOX_CHECK_AD_NAMES", "false")
    monkeypatch.setenv("FOX_HOT_RELOAD_BANNED_WORDS", "false")
    monkeypatch.setenv("FOX_CHECK_PHONE_NUMBERS", "false")
    monkeypatch.setenv("FOX_CHECK_USERNAMES", "0")
    monkeypatch.setenv("FOX_CHECK_FORWARDED", "true")
    monkeypatch.setenv("FOX_SEND_SPAM_WARNINGS", "off")
    monkeypatch.setenv(
        "FOX_WARNING_MESSAGE", "{username}: {reason} ({count}/{threshold})"
    )
    monkeypatch.setenv("FOX_NAME_FAMILY_MIN_OBSERVATIONS", "6")
    monkeypatch.setenv("FOX_NAME_FAMILY_MIN_UNIQUE_USERS", "4")
    monkeypatch.setenv("FOX_NAME_FAMILY_MIN_UNIQUE_CHATS", "3")
    settings = RuntimeSettings.from_env()
    assert settings.database_path == tmp_path / "custom.sqlite3"
    assert settings.legacy_admins_path == tmp_path / "admins.json"
    assert settings.rpc_min_interval_seconds == 0.5
    assert settings.page_size == 75
    assert settings.mute_duration_seconds == 3600
    assert settings.action_duration_seconds == 7200
    assert settings.owner_silver_group_ids == ("12345", "67890")
    assert settings.spam_threshold == 4
    assert settings.action_on_threshold == "mute"
    assert settings.check_links is False
    assert settings.delete_spam is False
    assert settings.check_banned_words is False
    assert settings.check_ad_names is False
    assert settings.hot_reload_banned_words is False
    assert settings.check_phone_numbers is False
    assert settings.check_usernames is False
    assert settings.check_forwarded is True
    assert settings.send_spam_warnings is False
    assert settings.warning_message_template == "{username}: {reason} ({count}/{threshold})"
    assert settings.name_family_min_observations == 6
    assert settings.name_family_min_unique_users == 4
    assert settings.name_family_min_unique_chats == 3
    monkeypatch.delenv("FOX_CHECK_FORWARDED")
    assert RuntimeSettings.from_env().check_forwarded is False
    monkeypatch.setenv("FOX_WARNING_MESSAGE", "{unsupported}")
    with pytest.raises(ValueError, match="FOX_WARNING_MESSAGE"):
        RuntimeSettings.from_env()
    for unsafe_template in (
        "{username:1000000}",
        "{username!r}",
        "{username:{count}}",
        "{}",
        "{username.__class__}",
    ):
        monkeypatch.setenv("FOX_WARNING_MESSAGE", unsafe_template)
        with pytest.raises(ValueError, match="FOX_WARNING_MESSAGE"):
            RuntimeSettings.from_env()
    monkeypatch.setenv(
        "FOX_WARNING_MESSAGE", "{username}: {reason} ({count}/{threshold})"
    )

    monkeypatch.setenv("FOX_MUTE_DURATION_SECONDS", "0")
    with pytest.raises(ValueError, match="40 days"):
        RuntimeSettings.from_env()

    monkeypatch.setenv("FOX_MUTE_DURATION_SECONDS", "3600")
    monkeypatch.setenv("FOX_SPAM_THRESHOLD", "13")
    with pytest.raises(ValueError, match="FOX_SPAM_THRESHOLD"):
        RuntimeSettings.from_env()
    monkeypatch.setenv("FOX_SPAM_THRESHOLD", "4")
    monkeypatch.setenv("FOX_ACTION_ON_THRESHOLD", "quarantine")
    with pytest.raises(ValueError, match="FOX_ACTION_ON_THRESHOLD"):
        RuntimeSettings.from_env()
    monkeypatch.setenv("FOX_ACTION_ON_THRESHOLD", "mute")
    for invalid_group_ids in (
        "12345,not-an-id",
        "0",
        "1" * 21,
        ",".join(str(value) for value in range(65)),
    ):
        monkeypatch.setenv("FOX_OWNER_SILVER_GROUP_IDS", invalid_group_ids)
        with pytest.raises(ValueError, match="FOX_OWNER_SILVER_GROUP_IDS"):
            RuntimeSettings.from_env()
    monkeypatch.setenv("FOX_OWNER_SILVER_GROUP_IDS", "12345,67890")
    monkeypatch.delenv("SOROUSH_API_HASH")
    with pytest.raises(ValueError, match="API_ID and SOROUSH_API_HASH"):
        RuntimeSettings.from_env()


def test_runtime_rpc_queue_and_member_page_settings_are_bounded(
    tmp_path, monkeypatch
) -> None:
    env_names = (
        "FOX_BOT_DATA_DIR", "SOROUSH_SESSION_PATH", "SOROUSH_SESSION_STRING",
        "SOROUSH_API_ID", "SOROUSH_API_HASH", "FOX_BOT_DB", "FOX_OWNER_FILE",
        "FOX_GLOBAL_WORDS_FILE", "FOX_LEGACY_ADMINS_FILE", "FOX_CONFIG_FILE",
        "FOX_OWNER_SILVER_GROUP_IDS", "FOX_MEMBER_PAGE_SIZE", "FOX_MUTE_DURATION_SECONDS", "FOX_SPAM_THRESHOLD",
        "FOX_ACTION_ON_THRESHOLD", "FOX_ACTION_DURATION_SECONDS", "FOX_CHECK_LINKS",
        "FOX_DELETE_SPAM", "FOX_CHECK_BANNED_WORDS", "FOX_CHECK_AD_NAMES",
        "FOX_CHECK_PHONE_NUMBERS", "FOX_CHECK_USERNAMES", "FOX_CHECK_FORWARDED",
        "FOX_SEND_SPAM_WARNINGS",
        "FOX_HOT_RELOAD_BANNED_WORDS", "FOX_WARNING_MESSAGE",
        "FOX_NAME_FAMILY_MIN_OBSERVATIONS", "FOX_NAME_FAMILY_MIN_UNIQUE_USERS",
        "FOX_NAME_FAMILY_MIN_UNIQUE_CHATS", "RPC_MAX_PENDING_TOTAL",
        "RPC_MAX_PENDING_PER_GROUP", "RPC_MIN_INTERVAL_SECONDS",
    )
    for name in env_names:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FOX_CONFIG_FILE", str(tmp_path / "missing-config.json"))
    monkeypatch.setenv("SOROUSH_API_ID", "12345")
    monkeypatch.setenv("SOROUSH_API_HASH", "test-api-hash")

    settings = RuntimeSettings.from_env()
    assert settings.rpc_max_pending_total == 256
    assert settings.rpc_max_pending_per_group == 32
    assert settings.rpc_min_interval_seconds == 0.25
    assert settings.page_size == 100
    assert settings.check_ad_names is True

    invalid_values = (
        ("RPC_MAX_PENDING_TOTAL", "4097"),
        ("RPC_MAX_PENDING_PER_GROUP", "257"),
        ("RPC_MIN_INTERVAL_SECONDS", "nan"),
        ("RPC_MIN_INTERVAL_SECONDS", "inf"),
        ("RPC_MIN_INTERVAL_SECONDS", "-0.01"),
        ("RPC_MIN_INTERVAL_SECONDS", "60.01"),
        ("FOX_MEMBER_PAGE_SIZE", "0"),
        ("FOX_MEMBER_PAGE_SIZE", "1001"),
    )
    for name, value in invalid_values:
        with monkeypatch.context() as scoped:
            scoped.setenv(name, value)
            with pytest.raises(ValueError, match=name):
                RuntimeSettings.from_env()


def test_runtime_settings_load_only_safe_json_moderation_fields(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "check_links": False,
                "delete_spam": False,
                "check_banned_words": True,
                "check_phone_numbers": False,
                "check_usernames": False,
                "check_forwarded": True,
                "send_warning": False,
                "enable_hot_reload_banned_words": False,
                "spam_threshold": 3,
                "action_on_threshold": "mute",
                "action_duration_seconds": 7200,
                "warning_message": "must be explicitly configured by the operator",
                "banned_words": ["not-imported-marker"],
                "admin_user_ids": ["not-imported-id"],
                "whitelisted_user_ids": ["not-imported-id"],
                "owner_silver_group_ids": ["not-imported-id"],
                "api_hash": "ignored-secret",
            }
        ),
        encoding="utf-8",
    )
    words_path = tmp_path / "approved-words.txt"
    words_path.write_text("approved-marker\n", encoding="utf-8")
    monkeypatch.setenv("FOX_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("FOX_GLOBAL_WORDS_FILE", str(words_path))
    monkeypatch.setenv("FOX_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOROUSH_API_ID", "12345")
    monkeypatch.setenv("SOROUSH_API_HASH", "test-api-hash")
    for key in (
        "FOX_CHECK_LINKS",
        "FOX_DELETE_SPAM",
        "FOX_CHECK_BANNED_WORDS",
        "FOX_HOT_RELOAD_BANNED_WORDS",
        "FOX_CHECK_PHONE_NUMBERS",
        "FOX_CHECK_USERNAMES",
        "FOX_CHECK_FORWARDED",
        "FOX_SEND_SPAM_WARNINGS",
        "FOX_SPAM_THRESHOLD",
        "FOX_ACTION_ON_THRESHOLD",
        "FOX_ACTION_DURATION_SECONDS",
        "FOX_OWNER_SILVER_GROUP_IDS",
        "FOX_WARNING_MESSAGE",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = RuntimeSettings.from_env()
    assert settings.check_links is False
    assert settings.delete_spam is False
    assert settings.check_banned_words is True
    assert settings.check_phone_numbers is False
    assert settings.check_usernames is False
    assert settings.check_forwarded is True
    assert settings.send_spam_warnings is False
    assert settings.hot_reload_banned_words is False
    assert settings.spam_threshold == 3
    assert settings.action_on_threshold == "mute"
    assert settings.action_duration_seconds == 7200
    assert settings.owner_silver_group_ids == ()
    assert settings.warning_message_template is None
    assert settings.global_words_path == words_path
    assert WordFilter.read_file(settings.global_words_path) == ("approved-marker",)
    runtime = build_runtime(FakeClient(), settings)
    try:
        assert runtime.word_filter.global_terms == ("approved-marker",)
    finally:
        asyncio.run(runtime.rpc_queue.close())
        runtime.store.close()

    monkeypatch.setenv("FOX_CHECK_LINKS", "true")
    assert RuntimeSettings.from_env().check_links is True


def test_runtime_settings_reject_invalid_safe_json_config(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text('{"check_links": "false"}', encoding="utf-8")
    monkeypatch.setenv("FOX_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("SOROUSH_API_ID", "12345")
    monkeypatch.setenv("SOROUSH_API_HASH", "test-api-hash")
    for key in (
        "FOX_CHECK_LINKS",
        "FOX_CHECK_BANNED_WORDS",
        "FOX_CHECK_PHONE_NUMBERS",
        "FOX_CHECK_USERNAMES",
        "FOX_CHECK_FORWARDED",
        "FOX_SEND_SPAM_WARNINGS",
        "FOX_HOT_RELOAD_BANNED_WORDS",
        "FOX_WARNING_MESSAGE",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match="check_links must be boolean"):
        RuntimeSettings.from_env()

    config_path.write_bytes(b'{"spam_threshold":' + b"9" * 5000 + b"}")
    with pytest.raises(ValueError, match="readable UTF-8 JSON object"):
        RuntimeSettings.from_env()
    config_path.write_bytes(b" " * 1_000_001)
    with pytest.raises(ValueError, match="1 MB"):
        RuntimeSettings.from_env()


def test_hot_reload_reloads_only_the_configured_global_word_file(tmp_path) -> None:
    path = tmp_path / "approved-words.txt"
    path.write_text("first-marker\n", encoding="utf-8")
    word_filter = WordFilter(WordFilter.read_file(path))
    callbacks: list[str] = []
    reloader = GlobalWordListReloader(
        path,
        word_filter,
        on_reload=lambda: callbacks.append("reloaded"),
    )
    assert not reloader.reload_if_changed()

    path.write_text("second-marker\n", encoding="utf-8")
    assert reloader.reload_if_changed()
    assert word_filter.global_terms == ("second-marker",)
    assert word_filter.match_global("first-marker") is None
    assert word_filter.match_global("second-marker") is not None
    assert callbacks == ["reloaded"]

    path.unlink()
    assert not reloader.reload_if_changed()
    assert word_filter.global_terms == ("second-marker",)


def test_hot_reload_rejects_oversized_file_without_replacing_working_terms(tmp_path) -> None:
    path = tmp_path / "approved-words.txt"
    path.write_text("stable-marker\n", encoding="utf-8")
    word_filter = WordFilter(WordFilter.read_file(path))
    reloader = GlobalWordListReloader(path, word_filter)

    path.write_text("x" * (MAX_GLOBAL_WORD_FILE_BYTES + 1), encoding="utf-8")
    assert not reloader.reload_if_changed()
    assert word_filter.global_terms == ("stable-marker",)
    assert word_filter.match_global("stable-marker") is not None

    with pytest.raises(ValueError, match="length limit"):
        word_filter.replace_global_terms(("x" * (MAX_GLOBAL_TERM_LENGTH + 1),))
    with pytest.raises(ValueError, match="term limit"):
        word_filter.replace_global_terms(("x",) * (MAX_GLOBAL_TERMS + 1))
    assert word_filter.global_terms == ("stable-marker",)


def test_runtime_scheduler_wires_the_approved_wordlist_reloader(tmp_path) -> None:
    async def scenario() -> None:
        path = tmp_path / "approved-words.txt"
        path.write_text("initial-marker\n", encoding="utf-8")
        runtime = build_runtime(
            FakeClient(),
            make_settings(tmp_path, global_words_path=path),
        )
        try:
            name_family = runtime.event_bridge.name_family_service
            assert "تهران" in name_family._known_answers["شهر"]
            path.write_text("تهران\n", encoding="utf-8")
            await _process_scheduled_actions(runtime, FakeClient())
            assert runtime.word_filter.global_terms == ("تهران",)
            assert "تهران" not in name_family._known_answers["شهر"]
        finally:
            await runtime.rpc_queue.close()
            runtime.store.close()

    asyncio.run(scenario())


def test_create_client_rejects_missing_api_credentials_before_import_or_network(tmp_path) -> None:
    settings = make_settings(tmp_path, api_id=None, api_hash=None)
    with pytest.raises(ValueError, match="requires a positive API ID"):
        create_soroush_client(settings)


def test_build_runtime_uses_local_components_without_network(tmp_path) -> None:
    settings = make_settings(
        tmp_path,
        check_links=False,
        delete_spam=False,
        check_banned_words=False,
        check_ad_names=False,
        check_phone_numbers=False,
        check_usernames=False,
        check_forwarded=True,
        send_spam_warnings=False,
        action_duration_seconds=7200,
        owner_silver_group_ids=("12345",),
        warning_message_template="{username}:{count}/{threshold}",
        spam_threshold=4,
        action_on_threshold="mute",
    )
    settings.owner_path.write_text('{"user_id":"owner"}', encoding="utf-8")
    settings.global_words_path.write_text("global-term\n", encoding="utf-8")
    client = FakeClient()
    runtime = build_runtime(client, settings)
    assert runtime.word_filter.global_terms == ("global-term",)
    assert runtime.wordlist_reloader is not None
    assert runtime.event_bridge.spam_guard._site_link_policy_enabled() is False
    assert runtime.event_bridge.delete_spam_enabled is False
    assert runtime.message_pipeline.check_banned_words is False
    assert runtime.event_bridge.check_ad_names_enabled is False
    assert runtime.event_bridge.spam_guard.check_phone_numbers is False
    assert runtime.event_bridge.spam_guard.check_usernames is False
    assert runtime.event_bridge.spam_guard.check_forwarded is True
    assert runtime.event_bridge.send_spam_warnings_enabled is False
    assert runtime.event_bridge.action_duration_seconds == 7200
    assert runtime.store.get_economy_profile("12345", "owner")["silver"] == 200_000
    assert runtime.store.get_economy_profile("67890", "owner")["silver"] == 0
    assert runtime.event_bridge.warning_message_template == "{username}:{count}/{threshold}"
    assert runtime.group_admin.warning_threshold("g") == 4
    assert runtime.group_admin.punishment_mode("g") == "mute"
    assert runtime.event_bridge.name_family_service.min_observations == 5
    assert runtime.event_bridge.name_family_service.min_unique_users == 3
    assert runtime.event_bridge.name_family_service.min_unique_chats == 2
    assert runtime.management.bot_user_id is None
    runtime.store.close()
    asyncio.run(runtime.rpc_queue.close(drain=False))


def test_run_bot_connects_registers_and_cleans_up_with_fake_client(tmp_path) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        settings.owner_path.write_text('{"user_id":"owner"}', encoding="utf-8")
        settings.global_words_path.write_text("", encoding="utf-8")
        client = FakeClient()
        await run_bot(settings, client=client)
        assert client.connected and client.started and client.disconnected
        assert len(client.handlers) == 3

    asyncio.run(scenario())


def test_scheduler_uses_nearest_deadline_then_idle_poll(monkeypatch) -> None:
    async def scenario() -> None:
        intervals: list[float] = []
        next_checks = iter((1.0, None))

        async def process_scheduled_actions(_runtime, _client) -> float | None:
            return next(next_checks)

        async def capture_sleep(seconds: float) -> None:
            intervals.append(seconds)
            if len(intervals) == 2:
                raise asyncio.CancelledError

        monkeypatch.setattr(
            runtime_module, "_process_scheduled_actions", process_scheduled_actions
        )
        monkeypatch.setattr(runtime_module.asyncio, "sleep", capture_sleep)
        with pytest.raises(asyncio.CancelledError):
            await runtime_module._scheduler_loop(SimpleNamespace(), object())

        assert intervals == [1.0, 15]

    asyncio.run(scenario())


def test_scheduler_delivers_game_timeout_notice_and_clears_staged_state() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        state_key = "game_session:group:truth_lie"
        store.set_group_state(
            "g",
            state_key,
            {
                "status": "pending",
                "kind": "truth_lie",
                "token": "timeout-token",
                "answers": [],
                "data": {"truth": True},
                "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).timestamp(),
            },
        )

        class NoticeClient:
            def __init__(self):
                self.notices = []

            async def send_message(self, group_id, text):
                self.notices.append((group_id, text))
                return SimpleNamespace(id=len(self.notices))

        queue = SerialRpcQueue(min_interval_seconds=0)
        runtime = SimpleNamespace(store=store, group_admin=None, rpc_queue=queue)
        client = NoticeClient()
        await _process_scheduled_actions(runtime, client)

        assert client.notices == [("g", "⏰ زمان تمام شد!\n\nپاسخ درست: حقیقت")]
        assert store.get_group_state("g", state_key) is None
        await queue.close()
        store.close()

    asyncio.run(scenario())


def test_scheduler_sends_battle_timeout_and_followup_question_separately() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        state_key = "game_session:group:battle"
        now = datetime.now(UTC).timestamp()
        data = {
            "phase": "playing",
            "players": [
                {"user_id": "p1", "name": "Player One"},
                {"user_id": "p2", "name": "Player Two"},
            ],
            "scores": {"p1": 0, "p2": 0},
            "questions": [
                {"id": str(index), "question": f"Question {index}", "accepted_answers": ["x"]}
                for index in range(6)
            ],
            "index": 0,
        }
        store.set_group_state(
            "g", state_key,
            {
                "status": "pending", "kind": "battle", "token": "battle-timeout",
                "phase": "playing", "data": data, "expires_at": now - 1,
            },
        )

        class NoticeClient:
            def __init__(self):
                self.notices = []
                self.delivered_at = []

            async def send_message(self, group_id, text):
                await asyncio.sleep(0.005)
                self.notices.append((group_id, text))
                self.delivered_at.append(runtime_module.time.time())
                return SimpleNamespace(id=len(self.notices))

        queue = SerialRpcQueue(min_interval_seconds=0)
        runtime = SimpleNamespace(store=store, group_admin=None, rpc_queue=queue)
        client = NoticeClient()
        await _process_scheduled_actions(runtime, client)

        assert client.notices[0] == ("g", "⏰ زمان سؤال تمام شد!")
        assert client.notices[1][0] == "g"
        assert "بازیکن ۲ — سؤال ۱" in client.notices[1][1]
        assert "Question 1" in client.notices[1][1]
        state = store.get_group_state("g", state_key)
        assert state["data"]["index"] == 1
        assert state["notice_pending"] is False
        assert "notice_text" not in state
        assert "notice_queue" not in state
        assert state["expires_at"] >= client.delivered_at[-1] + 29.9
        await queue.close()
        store.close()

    asyncio.run(scenario())


def test_scheduler_acknowledges_survival_transition_notice_without_deleting_the_live_game() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        state_key = "game_session:group:survival"
        store.set_group_state(
            "g", state_key,
            {
                "status": "pending", "kind": "survival", "token": "survival-round",
                "phase": "joining", "answers": [],
                "data": {
                    "phase": "joining", "round": 0, "answered": [],
                    "players": {
                        "p1": {"user_id": "p1", "name": "Player One", "alive": True, "round_coins": 0},
                        "p2": {"user_id": "p2", "name": "Player Two", "alive": True, "round_coins": 0},
                    },
                    "order": ["p1", "p2"], "used_questions": [],
                },
                "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).timestamp(),
            },
        )

        class NoticeClient:
            def __init__(self):
                self.notices = []
                self.delivered_at = []

            async def send_message(self, group_id, text):
                await asyncio.sleep(0.005)
                self.notices.append((group_id, text))
                self.delivered_at.append(runtime_module.time.time())
                return SimpleNamespace(id=len(self.notices))

        queue = SerialRpcQueue(min_interval_seconds=0)
        runtime = SimpleNamespace(store=store, group_admin=None, rpc_queue=queue)
        client = NoticeClient()
        await _process_scheduled_actions(runtime, client)

        state = store.get_group_state("g", state_key)
        assert state["status"] == "pending"
        assert state["phase"] == "playing"
        assert state["notice_pending"] is False
        assert "notice_text" not in state
        assert "notice_queue" not in state
        assert state["expires_at"] >= client.delivered_at[-1] + 29.9
        assert len(client.notices) == 2
        assert "🏕 بازی شروع شد" in client.notices[0][1]
        assert "مرحلهٔ ۱ از ۲۰" in client.notices[1][1]

        await _process_scheduled_actions(runtime, client)
        assert len(client.notices) == 2
        assert store.get_group_state("g", state_key) is not None
        await queue.close()
        store.close()

    asyncio.run(scenario())


def test_scheduler_does_not_refresh_deadline_after_prompt_changes_during_send() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        state_key = "game_session:group:battle"
        deadline = datetime.now(UTC).timestamp() + 60
        store.set_group_state(
            "g", state_key,
            {
                "status": "pending", "kind": "battle", "token": "race",
                "phase": "playing", "prompt": "Question 1",
                "notice_text": "Question 1", "notice_pending": True,
                "expires_at": deadline,
                "data": {"phase": "playing", "index": 1},
            },
        )

        class NoticeClient:
            async def send_message(self, _group_id, _text):
                state = store.get_group_state("g", state_key)
                state["data"]["index"] = 2
                state["prompt"] = "Question 2"
                store.set_group_state("g", state_key, state)
                return SimpleNamespace(id=1)

        queue = SerialRpcQueue(min_interval_seconds=0)
        runtime = SimpleNamespace(store=store, group_admin=None, rpc_queue=queue)
        await _process_scheduled_actions(runtime, NoticeClient())

        state = store.get_group_state("g", state_key)
        assert state["data"]["index"] == 2
        assert state["expires_at"] == deadline
        assert state["notice_pending"] is False
        await queue.close()
        store.close()

    asyncio.run(scenario())


def test_scheduler_polls_at_the_nearest_game_deadline_with_five_second_cap() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        deadline = datetime.now(UTC).timestamp() + 1.2
        store.set_group_state(
            "g", "game_session:g:riddle",
            {"status": "pending", "kind": "riddle", "expires_at": deadline},
        )
        queue = SerialRpcQueue(min_interval_seconds=0)
        runtime = SimpleNamespace(store=store, group_admin=None, rpc_queue=queue)

        delay = await _process_scheduled_actions(runtime, SimpleNamespace())
        assert delay is not None
        assert 0 < delay <= 1.2

        await queue.close()
        store.close()

    asyncio.run(scenario())


def test_scheduler_stages_cleanup_for_confirmation_and_unlocks_on_deadline() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        store.set_group_state(
            "g",
            "auto_cleanup",
            {
                "status": "scheduled",
                "actor_id": "owner",
                "count": 2,
                "scheduled_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            },
        )
        store.set_group_state(
            "g",
            "timed_unlock",
            {
                "status": "scheduled",
                "actor_id": "owner",
                "unlock_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            },
        )
        store.set_group_state(
            "g",
            "reminder:test-reminder",
            {
                "user_id": "user",
                "name": "Test User",
                "text": "check the kettle",
                "due_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            },
        )

        class FakeGroupAdmin:
            def __init__(self):
                self.prepared = []
                self.lock_changes = []

            async def prepare_delete_recent(self, group_id, actor_id, count):
                self.prepared.append((group_id, actor_id, count))
                return {"scan_complete": True, "job_id": "ABCDEF123456", "count": count}

            async def set_group_locked(self, group_id, actor_id, locked):
                self.lock_changes.append((group_id, actor_id, locked))
                return True

        class NoticeClient:
            def __init__(self):
                self.notices = []

            async def send_message(self, group_id, text):
                self.notices.append((group_id, text))
                return SimpleNamespace(id=len(self.notices))

        service = FakeGroupAdmin()
        client = NoticeClient()
        queue = SerialRpcQueue(min_interval_seconds=0)
        runtime = SimpleNamespace(store=store, group_admin=service, rpc_queue=queue)
        await _process_scheduled_actions(runtime, client)

        cleanup = store.get_group_state("g", "auto_cleanup")
        assert cleanup["status"] == "awaiting_confirmation"
        assert cleanup["job_id"] == "ABCDEF123456"
        assert service.prepared == [("g", "owner", 2)]
        assert store.get_group_state("g", "timed_unlock") is None
        assert service.lock_changes == [("g", "owner", False)]
        assert store.get_group_state("g", "reminder:test-reminder") is None
        assert len(client.notices) == 3
        assert "تایید پاک ABCDEF123456" in client.notices[0][1]
        assert "قفل ساعتی پایان یافت" in client.notices[1][1]
        assert "check the kettle" in client.notices[2][1]
        await queue.close()
        store.close()

    asyncio.run(scenario())

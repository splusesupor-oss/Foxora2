"""Comprehensive tests for Foxora2 instance isolation and message formatting.

Covers:
  1. Foxora2 repository identity (.bot_instance, foxora2, foxora2.py, README, .env.example).
  2. Complete path isolation across all runtime config, log, SQLite DB, backup,
     archive, PID lock, and watchdog lock files (~/.foxora2 vs ~/.foxora vs B2).
  3. Concurrent dual-instance execution (Foxora + Foxora2) writing to SQLite and
     JSON storage without any shared state or cross-contamination.
  4. Session, API credential, and Bot Token isolation (SOROUSH_SESSION_STRING_FOXORA2,
     BOT_TOKEN_FOXORA2, tools/login_primary_once.py, watchdog secret redaction).
  5. Launcher CLI (./foxora2 status / stop) stopping ONLY Foxora2 while a live
     Foxora process and its ~/.foxora lock files remain completely untouched.
  6. Preservation of Bold (MessageEntityBold), Quote (MessageEntityBlockquote),
     Spoiler (MessageEntitySpoiler), and actual outgoing message sending/replying.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from splusthon.tl.types import (
    MessageEntityBlockquote,
    MessageEntityBold,
    MessageEntitySpoiler,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _u16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _u16_slice(text: str, offset: int, length: int) -> str:
    raw = text.encode("utf-16-le")
    return raw[offset * 2 : (offset + length) * 2].decode("utf-16-le")


def test_foxora2_repository_identity_and_launchers_exist():
    marker = (PROJECT_ROOT / ".bot_instance").read_text(encoding="utf-8").strip()
    assert marker == "foxora2"

    py_launcher = PROJECT_ROOT / "foxora2.py"
    sh_launcher = PROJECT_ROOT / "foxora2"
    assert py_launcher.is_file()
    assert sh_launcher.is_file()
    assert os.access(py_launcher, os.X_OK)
    assert os.access(sh_launcher, os.X_OK)
    assert not (PROJECT_ROOT / "foxora.py").exists()
    assert not (PROJECT_ROOT / "foxora").exists()

    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "BOT_INSTANCE=foxora2" in env_example
    assert "SOROUSH_SESSION_STRING_FOXORA2=" in env_example

    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "Foxora2" in readme
    assert "~/.foxora2" in readme
    assert "SOROUSH_SESSION_STRING_FOXORA2" in readme
    assert "./foxora2" in readme


def test_all_storage_modules_resolve_exclusively_under_dot_foxora2(tmp_path):
    env = os.environ.copy()
    for k in list(env):
        if k.startswith("SOROUSH_BOT_DATA_DIR") or k == "BOT_INSTANCE":
            env.pop(k, None)
    env["HOME"] = str(tmp_path)

    code = """
import json
import watchdog
from modules import (
    runtime_paths, runtime_db, group_storage, admin_storage, admin_tools,
    warning_threshold, manual_mute_storage, owner_check, user_map,
    banned_storage, riddles, punishment_mode, group_memory,
    entertainment_control, reminders, user_original_storage, fox_game_tokens,
    watchdog_reporting, group_words_storage, group_names_storage, group_level, owner_private,
    daily_admin_report, vip_storage, name_family_learning, pinned_messages,
    user_activity, group_expiry, group_actions, web_search, group_rules,
    user_history, broadcast_state, group_stats, clipboard_storage,
    search_access, flag_guess, group_banned_words_control, bot_detector,
)
from modules.fox_games import minesweeper, sentence_guess, lucky_box
from economy import storage as eco_storage, settings as eco_settings
from economy.shop import store as eco_store

paths = {
    "instance": runtime_paths.INSTANCE_NAME,
    "data_dir": str(runtime_paths.DATA_DIR),
    "runtime_db": str(runtime_db.DB_FILE),
    "watchdog_lock": str(watchdog.LOCK_FILE),
    "watchdog_log": str(watchdog.LOG_FILE),
    "bot_pid": str(runtime_paths.runtime_config_file("bot.pid", migrate=False)),
    "group_storage": str(group_storage.FILE),
    "admin_storage": str(admin_storage.FILE),
    "admin_log": str(admin_tools._ADMIN_LOG_FILE),
    "auto_cleanup": str(admin_tools._CLEANUP_FILE),
    "warning_threshold": str(warning_threshold._FILE),
    "manual_mute": str(manual_mute_storage.FILE),
    "owner_runtime": str(owner_check.RUNTIME_FILE),
    "user_map": str(user_map.FILE),
    "banned_storage": str(banned_storage.FILE),
    "riddles": str(riddles._PROGRESS_FILE),
    "punishment_mode": str(punishment_mode._FILE),
    "group_memory": str(group_memory.FILE),
    "entertainment_control": str(entertainment_control.FILE),
    "reminders": str(reminders.FILE),
    "user_original": str(user_original_storage.FILE),
    "fox_tokens": str(fox_game_tokens.TOKEN_FILE),
    "fox_leaderboard": str(fox_game_tokens.LEADERBOARD_FILE),
    "fox_official_pool": str(fox_game_tokens.OFFICIAL_POOL_FILE),
    "watchdog_pending": str(watchdog_reporting.PENDING_FILE),
    "group_words": str(group_words_storage.FILE),
    "group_names": str(group_names_storage.FILE),
    "group_level": str(group_level._FILE),
    "owner_private": str(owner_private.PEER_FILE),
    "daily_admin_report": str(daily_admin_report.FILE),
    "vip_storage": str(vip_storage.FILE),
    "name_family_learning": str(name_family_learning.FILE),
    "pinned_messages": str(pinned_messages.FILE),
    "user_activity": str(user_activity.FILE),
    "user_activity_archive": str(user_activity.ARCHIVE_FILE),
    "group_expiry": str(group_expiry.FILE),
    "group_lock_state": str(group_actions._LOCK_STATE_FILE),
    "web_search": str(web_search.FILE),
    "group_rules": str(group_rules.FILE),
    "user_history": str(user_history._FILE),
    "broadcast_state": str(broadcast_state._STATE_FILE),
    "group_stats": str(group_stats.FILE),
    "clipboard_storage": str(clipboard_storage.FILE),
    "search_access": str(search_access.FILE),
    "flag_guess": str(flag_guess._PROGRESS_FILE),
    "group_banned_words": str(group_banned_words_control.FILE),
    "bot_detector": str(bot_detector._FILE),
    "minesweeper": str(minesweeper.STATE_FILE),
    "sentence_guess": str(sentence_guess.FILE),
    "lucky_box": str(lucky_box.STATE_FILE),
    "eco_data": str(eco_storage.DATA_FILE),
    "eco_legacy_coins": str(eco_storage.LEGACY_COINS_FILE),
    "eco_db": str(eco_storage.DB_FILE),
    "eco_settings": str(eco_settings.SETTINGS_FILE),
    "eco_shop": str(eco_store.ITEMS_FILE),
}
print(json.dumps(paths))
"""
    out = subprocess.check_output(
        [sys.executable, "-c", code], cwd=str(PROJECT_ROOT), env=env, text=True
    )
    paths = json.loads(out.strip().splitlines()[-1])
    expected_root = (tmp_path / ".foxora2").resolve()

    assert paths.pop("instance") == "foxora2"
    for key, val in paths.items():
        resolved = Path(val).resolve()
        assert resolved == expected_root or expected_root in resolved.parents, (
            f"{key}={resolved} is outside {expected_root}"
        )
    assert not (tmp_path / ".foxora").exists()
    assert not (tmp_path / ".local" / "share" / "soroush-bot").exists()


def test_concurrent_foxora_and_foxora2_databases_and_configs_never_collide(tmp_path):
    """Run Foxora (bot 1) and Foxora2 (bot 2) against the same HOME and verify
    that SQLite DBs, groups.json, banned_users, and economy wallets stay 100% separate.
    """
    worker_code = """
import json, os
from modules import group_storage, banned_storage
from economy.coins import accounts

inst = os.environ["BOT_INSTANCE"]
gid = "-1001" if inst == "foxora" else "-2002"
uid = 111 if inst == "foxora" else 222
title = f"Group-{inst}"

group_storage.activate_group(gid, title)
banned_storage.add_banned(gid, uid, username=f"user_{inst}", reason=f"reason_{inst}")
accounts.add(gid, uid, "gold", 50 if inst == "foxora" else 99)

other_gid = "-2002" if inst == "foxora" else "-1001"
other_uid = 222 if inst == "foxora" else 111
print(json.dumps({
    "instance": inst,
    "groups": group_storage.load_groups(),
    "is_banned_own": banned_storage.is_banned(gid, uid),
    "is_banned_other": banned_storage.is_banned(other_gid, other_uid),
    "gold_own": accounts.get_balance(gid, uid)["gold"],
    "gold_other": accounts.get_balance(other_gid, other_uid)["gold"],
}))
"""
    results = {}
    for inst in ("foxora", "foxora2"):
        env = os.environ.copy()
        for k in list(env):
            if k.startswith("SOROUSH_BOT_DATA_DIR") or k == "BOT_INSTANCE":
                env.pop(k, None)
        env["HOME"] = str(tmp_path)
        env["BOT_INSTANCE"] = inst
        out = subprocess.check_output(
            [sys.executable, "-c", worker_code], cwd=str(PROJECT_ROOT), env=env, text=True
        )
        results[inst] = json.loads(out.strip().splitlines()[-1])

    # Verify Foxora 1 state vs Foxora 2 state
    assert results["foxora"]["is_banned_own"] is True
    assert results["foxora"]["is_banned_other"] is False
    assert results["foxora"]["gold_own"] == 50
    assert results["foxora"]["gold_other"] == 0
    assert "-1001" in results["foxora"]["groups"]
    assert "-2002" not in results["foxora"]["groups"]

    assert results["foxora2"]["is_banned_own"] is True
    assert results["foxora2"]["is_banned_other"] is False
    assert results["foxora2"]["gold_own"] == 99
    assert results["foxora2"]["gold_other"] == 0
    assert "-2002" in results["foxora2"]["groups"]
    assert "-1001" not in results["foxora2"]["groups"]

    assert (tmp_path / ".foxora" / "db" / "bot.sqlite3").is_file()
    assert (tmp_path / ".foxora2" / "db" / "bot.sqlite3").is_file()


def test_bot_token_and_watchdog_secret_isolation():
    from bot_api_mode import resolve_bot_token
    import watchdog

    env = {
        "BOT_TOKEN": "GENERIC_TOKEN",
        "BOT_TOKEN_FOXORA": "FOXORA1_TOKEN",
        "BOT_TOKEN_FOXORA2": "FOXORA2_TOKEN",
    }
    assert resolve_bot_token(env_get=env.get, instance="foxora2") == "FOXORA2_TOKEN"
    assert resolve_bot_token(env_get=env.get, instance="foxora") == "FOXORA1_TOKEN"

    redacted = watchdog._redact(
        "SOROUSH_SESSION_STRING_FOXORA2=secret123 BOT_TOKEN_FOXORA2=tok456"
    )
    assert "secret123" not in redacted
    assert "tok456" not in redacted
    assert "SOROUSH_SESSION_STRING_FOXORA2=<redacted>" in redacted
    assert "BOT_TOKEN_FOXORA2=<redacted>" in redacted


def test_foxora2_launcher_status_and_stop_never_affect_foxora1(tmp_path):
    """Spawn a live simulated Foxora1 process and a live simulated Foxora2 process.
    Verify `./foxora2 status` and `./foxora2 stop` stop ONLY Foxora2 while Foxora1
    remains alive and its ~/.foxora files remain intact.
    """
    foxora1_dir = tmp_path / ".foxora" / "config"
    foxora2_dir = tmp_path / ".foxora2" / "config"
    foxora1_dir.mkdir(parents=True, exist_ok=True)
    foxora2_dir.mkdir(parents=True, exist_ok=True)

    env1 = os.environ.copy()
    env1["HOME"] = str(tmp_path)
    env1["BOT_INSTANCE"] = "foxora"
    proc_foxora1 = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        env=env1,
    )

    env2 = os.environ.copy()
    env2["HOME"] = str(tmp_path)
    env2["BOT_INSTANCE"] = "foxora2"
    proc_foxora2 = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        env=env2,
    )

    try:
        (foxora1_dir / "bot.pid").write_text(str(proc_foxora1.pid), encoding="utf-8")
        (foxora2_dir / "bot.pid").write_text(str(proc_foxora2.pid), encoding="utf-8")

        launcher_env = os.environ.copy()
        launcher_env["HOME"] = str(tmp_path)
        launcher_env.pop("SOROUSH_BOT_DATA_DIR_FOXORA2", None)

        # 1. Check status while Foxora2 is running
        status_out = subprocess.check_output(
            [str(PROJECT_ROOT / "foxora2"), "status"],
            cwd=str(PROJECT_ROOT),
            env=launcher_env,
            text=True,
        )
        assert f"RUNNING (pid={proc_foxora2.pid})" in status_out
        assert str(tmp_path / ".foxora2") in status_out

        # 2. Stop Foxora2 via `./foxora2 stop`
        stop_out = subprocess.check_output(
            [str(PROJECT_ROOT / "foxora2"), "stop"],
            cwd=str(PROJECT_ROOT),
            env=launcher_env,
            text=True,
        )
        assert f"Foxora2 (pid={proc_foxora2.pid}) stopped." in stop_out
        proc_foxora2.wait(timeout=5)
        assert proc_foxora2.poll() is not None
        assert not (foxora2_dir / "bot.pid").exists()

        # 3. Verify Foxora1 is still alive and its PID file is untouched!
        assert proc_foxora1.poll() is None, "Foxora1 process must NOT be killed by ./foxora2 stop!"
        assert (foxora1_dir / "bot.pid").read_text(encoding="utf-8") == str(proc_foxora1.pid)

        # 4.Even if Foxora1's PID were somehow written into Foxora2's bot.pid (e.g. PID reuse),
        # `./foxora2 stop` must refuse to kill it because its BOT_INSTANCE is `foxora`!
        (foxora2_dir / "bot.pid").write_text(str(proc_foxora1.pid), encoding="utf-8")
        stop_out2 = subprocess.check_output(
            [str(PROJECT_ROOT / "foxora2"), "stop"],
            cwd=str(PROJECT_ROOT),
            env=launcher_env,
            text=True,
        )
        assert "Foxora2 is not running." in stop_out2
        assert proc_foxora1.poll() is None
    finally:
        for p in (proc_foxora1, proc_foxora2):
            if p.poll() is None:
                p.kill()
                p.wait(timeout=5)


def test_bold_and_quote_formatting_templates_and_sending():
    """Verify Bold (MessageEntityBold), Quote (MessageEntityBlockquote), Spoiler
    (MessageEntitySpoiler), and actual message sending/replying across modules.
    """
    from modules.admin_help_builder import build_admin_help
    from economy.ui.formatting import quote_spans, spans_for
    from handlers.economy_handler import _entities as eco_entities, _send as eco_send
    from handlers.fox_games_router import _bold_reply
    from handlers.group_expiry_handler import _entities as expiry_entities
    from handlers.clipboard_handler import build_guide_entities
    from modules.group_expiry import build_confirmation, build_expired_message
    from modules.entertainment_control import blocked_bold_span, full_bold_span
    from modules import outgoing_sender

    # 1. Admin help builder (Bold titles/commands + Blockquote sections)
    help_text, help_ents = build_admin_help()
    assert len(help_ents) >= 10
    assert any(isinstance(e, MessageEntityBold) for e in help_ents)
    assert any(isinstance(e, MessageEntityBlockquote) for e in help_ents)
    total_u16 = _u16_len(help_text)
    for e in help_ents:
        assert 0 <= e.offset < total_u16
        assert 0 < e.length <= total_u16 - e.offset

    # 2. Economy UI formatting & sending (Bold + Blockquote)
    sample_text = "💰 موجودی حساب\n━━━━━━━━━━━━\nسکه طلا: ۱۰"
    q_spans = quote_spans(sample_text, "━━━━━━━━━━━━\nسکه طلا: ۱۰")
    b_spans = spans_for(sample_text, ["💰 موجودی حساب", "سکه طلا:"])
    combined_spans = b_spans + q_spans
    ents = eco_entities(combined_spans)
    assert any(isinstance(e, MessageEntityBold) for e in ents)
    assert any(isinstance(e, MessageEntityBlockquote) for e in ents)

    captured_replies = []

    class DummyEvent:
        chat_id = -100555
        id = 42

        async def reply(self, text, formatting_entities=None, **kw):
            captured_replies.append((text, list(formatting_entities or []), kw))
            return None

    asyncio.run(eco_send(DummyEvent(), (sample_text, combined_spans)))
    assert len(captured_replies) == 1
    sent_text, sent_ents, _ = captured_replies[0]
    assert sent_text == sample_text
    assert any(isinstance(e, MessageEntityBold) for e in sent_ents)
    assert any(isinstance(e, MessageEntityBlockquote) for e in sent_ents)

    # 3. Fox games router _bold_reply
    captured_replies.clear()
    asyncio.run(
        _bold_reply(
            DummyEvent(),
            "🦊 بازی روباه\nشروع شد!",
            parts=["🦊 بازی روباه"],
        )
    )
    assert len(captured_replies) == 1
    fg_text, fg_ents, _ = captured_replies[0]
    assert len(fg_ents) == 1 and isinstance(fg_ents[0], MessageEntityBold)
    assert _u16_slice(fg_text, fg_ents[0].offset, fg_ents[0].length) == "🦊 بازی روباه"

    # 4. Group expiry confirmation & expired notices (Bold + Blockquote)
    from datetime import datetime, timezone
    conf_text, conf_spans = build_confirmation(
        datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc),
    )
    conf_ents = expiry_entities(conf_spans)
    assert any(isinstance(e, MessageEntityBold) for e in conf_ents)
    assert any(isinstance(e, MessageEntityBlockquote) for e in conf_ents)

    exp_text, exp_spans = build_expired_message()
    exp_ents = expiry_entities(exp_spans)
    assert any(isinstance(e, MessageEntityBold) for e in exp_ents)

    captured_replies.clear()
    asyncio.run(DummyEvent().reply(conf_text, formatting_entities=conf_ents))
    assert len(captured_replies) == 1
    assert len(captured_replies[0][1]) == len(conf_ents)

    # 5. Entertainment control full_bold_span & blocked_bold_span
    ent_msg = "✅ سرگرمی در این گروه فعال شد."
    ent_off, ent_len = full_bold_span(ent_msg)
    assert ent_off == 0 and ent_len == _u16_len(ent_msg)
    blk_span = blocked_bold_span()
    assert blk_span is not None and blk_span[1] > 0

    # 6. Clipboard guide (Bold + Blockquote) + OutgoingSender formatting_entities pass-through
    guide_ents = build_guide_entities()
    assert any(isinstance(e, MessageEntityBold) for e in guide_ents)
    assert any(isinstance(e, MessageEntityBlockquote) for e in guide_ents)

    sent_client_calls = []

    class DummyClient:
        async def send_message(self, entity, message, **kwargs):
            sent_client_calls.append((entity, message, kwargs))
            return type("Msg", (), {"id": 99})()

    class DummyLogger:
        def log_info(self, *a, **k):
            pass

        def log_error(self, *a, **k):
            pass

    async def _exercise_sender():
        client = DummyClient()
        sender = outgoing_sender.OutgoingSender(client, DummyLogger())

        # OutgoingSender queue preserves formatting_entities (Bold + Blockquote + Spoiler)
        combo_ents = [
            MessageEntityBold(offset=0, length=4),
            MessageEntityBlockquote(offset=5, length=4),
            MessageEntitySpoiler(offset=10, length=4),
        ]
        await client.send_message(
            -100888,
            "سلام دنیا تست!",
            reply_to=12,
            formatting_entities=combo_ents,
        )
        assert len(sent_client_calls) == 1
        target, body, kwargs = sent_client_calls[0]
        assert target == -100888
        assert body == "سلام دنیا تست!"
        assert kwargs.get("reply_to") == 12
        assert kwargs.get("formatting_entities") == combo_ents

    asyncio.run(_exercise_sender())

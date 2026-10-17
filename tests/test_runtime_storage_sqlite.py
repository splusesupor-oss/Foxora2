"""Focused subprocess tests for the production/Termux storage backend.

Subprocesses are intentional: runtime path/backend selection happens at import
time and must be tested with a clean interpreter.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(script, data_dir):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["SOROUSH_BOT_DATA_DIR"] = str(data_dir)
    env.pop("ECONOMY_BACKEND", None)
    env.pop("RUNTIME_STATE_BACKEND", None)
    subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=str(ROOT), env=env, check=True,
    )


def test_legacy_coins_migrates_transactionally_and_exports(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "coins.json").write_text(json.dumps({
        "daily_messages": {
            "2026-08-19": {"123": {"7": {"name": "Ali", "messages": 9}}}
        },
        "paid_days": ["2026-08-19"],
        "users": {
            "123": {
                "7": {"name": "Ali", "coins": 50, "wins": 2},
                "8": {"name": "Sara", "coins": 5, "wins": 1},
            }
        },
    }, ensure_ascii=False), encoding="utf-8")

    _run(r'''
        import json
        from pathlib import Path
        import economy
        from economy import storage
        from economy.coins import accounts

        assert storage.backend_name() == "sqlite"
        assert economy.get_balance(123, 7)["bronze"] == 50
        assert economy.get_balance(123, 8)["bronze"] == 5
        profile = storage.snapshot()["users"]["123:7"]
        assert profile["wins"] == 2 and profile["name"] == "Ali"

        economy.add_bronze(123, 7, 10, reference="focused:once")
        economy.add_bronze(123, 7, 10, reference="focused:once")
        assert economy.get_balance(123, 7)["bronze"] == 60
        economy.transfer(123, 7, 8, "bronze", 20, reference="focused:transfer")
        assert economy.get_balance(123, 7)["bronze"] == 40
        assert economy.get_balance(123, 8)["bronze"] == 25

        try:
            with storage.transaction() as data:
                data["users"][accounts.user_key(123, 7)]["bronze"] = 999
                raise RuntimeError("rollback")
        except RuntimeError:
            pass
        assert economy.get_balance(123, 7)["bronze"] == 40
        assert storage.integrity_check() == "ok"

        exported = storage.export_json()
        assert json.loads(exported.read_text(encoding="utf-8"))["users"]["123:7"]["bronze"] == 40
        backup = storage.backup_to()
        assert Path(backup).exists()
        source = storage._connection().execute(
            "SELECT value FROM storage_meta WHERE key='legacy_economy_source'"
        ).fetchone()[0]
        assert source == "coins"

        storage.flush()
        storage._close_connection()
        storage._cache = None
        assert economy.get_balance(123, 7)["bronze"] == 40
        assert economy.get_balance(123, 8)["bronze"] == 25
    ''', tmp_path)


def test_runtime_tables_migrate_batch_prune_cap_and_export(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "banned_users.json").write_text(json.dumps({
        "-1000000000123": [{
            "user_id": "7", "username": "OldName", "display_name": "Old User",
            "reason": "legacy", "source": "system",
            "username_aliases": ["OlderName"],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    (config / "user_activity.json").write_text(json.dumps({
        "123": {"7": {"gifs": 2, "videos": 3, "first": 100.0,
                         "last": 1787184000.0}}
    }), encoding="utf-8")
    (config / "riddle_progress.json").write_text(
        json.dumps({"7": ["legacy-riddle"]}, ensure_ascii=False), encoding="utf-8"
    )
    (config / "flag_guess_progress.json").write_text(
        json.dumps({"7": ["ایران"]}, ensure_ascii=False), encoding="utf-8"
    )

    _run(r'''
        import json
        import threading
        from pathlib import Path
        from modules import runtime_db, banned_storage as bans, user_activity
        from modules import riddles, flag_guess
        from modules.admin_tools import log_action, get_log, export_admin_log_json
        from modules.game_progress_storage import flush_all, export_all_json

        assert runtime_db.integrity_check() == "ok"
        assert bans.is_banned(123, 999, "oldername")
        bans.add_banned(123, 7, "NewName", "Old User", "updated")
        assert bans.is_banned(-1000000000123, 7, "oldname")

        class Message:
            gif = True
            animation = None
            document = None
            media = None

        # Exercise generation-based batching while a flush thread races events.
        stop = threading.Event()
        def flusher():
            while not stop.is_set():
                user_activity.flush()
        thread = threading.Thread(target=flusher)
        thread.start()
        try:
            for _ in range(500):
                user_activity.record(123, 7, Message())
        finally:
            stop.set(); thread.join()
        user_activity.flush()
        assert user_activity.get(123, 7)["gifs"] == 502

        assert riddles.seen_count(7) == 1
        assert flag_guess.seen_count(7) == 1
        riddles.new_riddle(1, 8)
        flag_guess.start(2, 8)
        assert flush_all() is True

        for index in range(205):
            log_action(123, {"id": 1, "username": "admin"}, f"action-{index}")
        assert len(get_log(123, 1000)) == 200

        runtime_db.execute(
            "INSERT INTO user_activity VALUES(?,?,?,?,?,?)",
            ("old", "1", 0, 0, 1, 1),
        )
        report = runtime_db.maintenance(activity_retention_days=180)
        assert report["activity_deleted"] >= 1
        assert runtime_db.query_one(
            "SELECT 1 FROM user_activity WHERE group_id='old'"
        ) is None

        ban_file = bans.export_json()
        activity_file = user_activity.export_json()
        game_files = export_all_json()
        admin_file = export_admin_log_json()
        assert json.loads(Path(ban_file).read_text(encoding="utf-8"))["123"]
        assert json.loads(Path(activity_file).read_text(encoding="utf-8"))["123"]["7"]["gifs"] == 502
        assert len(game_files) == 2
        assert len(json.loads(Path(admin_file).read_text(encoding="utf-8"))["123"]) == 200

        backup = runtime_db.backup_to()
        assert Path(backup).exists()
        assert runtime_db.integrity_check() == "ok"
        stats = runtime_db.stats()
        assert stats["banned_users"] == 1
        assert stats["user_activity"] == 1
        assert stats["admin_events"] == 200
    ''', tmp_path)

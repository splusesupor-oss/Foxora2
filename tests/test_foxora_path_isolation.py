"""Foxora2 runtime path isolation tests.

ثابت می‌کند که وقتی ربات با هویت Foxora2 بالا می‌آید:
  * همهٔ فایل‌های زمان اجرا (config, logs, db, backups, archive, bot.pid)
    زیر ``~/.foxora2`` قرار می‌گیرند.
  * هیچ فایلی داخل مسیرهای Foxora (``~/.foxora``) یا B2
    (``~/.local/share/soroush-bot*``) ساخته نمی‌شود.
  * حتی اگر در محیط شل متغیر عمومی ``SOROUSH_BOT_DATA_DIR`` به مسیر پیش‌فرض
    Foxora یا B2 اشاره کند، Foxora2 به مسیر اشتراکی نمی‌نویسد.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _probe_paths(extra_env: dict[str, str], home: Path) -> dict:
    env = os.environ.copy()
    for k in list(env):
        if k.startswith("SOROUSH_BOT_DATA_DIR") or k == "BOT_INSTANCE":
            env.pop(k, None)
    env["HOME"] = str(home)
    env.update(extra_env)
    code = (
        "import json; "
        "from modules import runtime_paths as rp; "
        "info = rp.describe(); "
        "info['pid_file'] = str(rp.runtime_config_file('bot.pid', migrate=False)); "
        "info['groups_file'] = str(rp.runtime_config_file('groups.json', migrate=False)); "
        "info['log_file'] = str(rp.runtime_log_file('bot.log', migrate=False)); "
        "info['db_file'] = str(rp.runtime_db_file('bot.sqlite3')); "
        "print(json.dumps(info))"
    )
    out = subprocess.check_output(
        [sys.executable, "-c", code], cwd=str(PROJECT_ROOT), env=env, text=True
    )
    return json.loads(out.strip().splitlines()[-1])


def test_marker_file_identifies_foxora2(tmp_path):
    marker = (PROJECT_ROOT / ".bot_instance").read_text(encoding="utf-8").strip()
    assert marker == "foxora2"

    info = _probe_paths({}, tmp_path)
    expected = (tmp_path / ".foxora2").resolve()
    assert info["instance"] == "foxora2"
    assert Path(info["data_dir"]) == expected
    assert Path(info["config_dir"]) == expected / "config"
    assert Path(info["log_dir"]) == expected / "logs"
    assert Path(info["db_dir"]) == expected / "db"
    assert Path(info["pid_file"]) == expected / "config" / "bot.pid"
    assert Path(info["groups_file"]) == expected / "config" / "groups.json"
    assert Path(info["log_file"]) == expected / "logs" / "bot.log"
    assert Path(info["db_file"]) == expected / "db" / "bot.sqlite3"
    assert not (tmp_path / ".foxora").exists()
    assert not (tmp_path / ".local" / "share" / "soroush-bot").exists()
    assert not (tmp_path / ".local" / "share" / "soroush-bot-foxora2").exists()


def test_termux_environment_still_uses_dot_foxora2_not_b2_or_foxora_dir(tmp_path):
    info = _probe_paths(
        {"PREFIX": "/data/data/com.termux/files/usr", "BOT_INSTANCE": "foxora2"},
        tmp_path,
    )
    expected = (tmp_path / ".foxora2").resolve()
    assert Path(info["data_dir"]) == expected
    assert "soroush-bot" not in info["data_dir"]
    assert Path(info["data_dir"]).name == ".foxora2"


def test_instance_specific_env_override_wins(tmp_path):
    custom = tmp_path / "custom-foxora2-home"
    info = _probe_paths(
        {
            "BOT_INSTANCE": "foxora2",
            "SOROUSH_BOT_DATA_DIR": str(tmp_path / "wrong-shared"),
            "SOROUSH_BOT_DATA_DIR_FOXORA": str(tmp_path / "wrong-foxora1"),
            "SOROUSH_BOT_DATA_DIR_FOXORA2": str(custom),
        },
        tmp_path,
    )
    assert Path(info["data_dir"]) == custom.resolve()


def test_shared_b2_or_foxora_env_override_is_deflected(tmp_path):
    b2_default = tmp_path / ".local" / "share" / "soroush-bot"
    info = _probe_paths(
        {
            "BOT_INSTANCE": "foxora2",
            "SOROUSH_BOT_DATA_DIR": str(b2_default),
        },
        tmp_path,
    )
    assert Path(info["data_dir"]) != b2_default.resolve()
    assert Path(info["data_dir"]).name == "soroush-bot-foxora2"

    foxora1_default = tmp_path / ".foxora"
    info2 = _probe_paths(
        {
            "BOT_INSTANCE": "foxora2",
            "SOROUSH_BOT_DATA_DIR": str(foxora1_default),
        },
        tmp_path,
    )
    assert Path(info2["data_dir"]) == (tmp_path / ".foxora2").resolve()
    assert Path(info2["data_dir"]) != foxora1_default.resolve()

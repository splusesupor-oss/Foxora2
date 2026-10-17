"""Foxora runtime path isolation tests.

Every runtime artifact of the Foxora instance (session state files, database,
config, owner, logs, temp files, backups) must live inside its own
independent directory ``~/.foxora`` — never inside a B2 directory
(``~/.local/share/soroush-bot*``) and never inside the project tree.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_describe(tmp_path: Path, extra_env: dict | None = None,
                  remove_instance: bool = False) -> dict:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PREFIX"] = "/data/data/com.termux/files/usr"  # exercise the Termux branch
    env.pop("SOROUSH_BOT_DATA_DIR", None)
    for key in list(env):
        if key.startswith("SOROUSH_BOT_DATA_DIR_"):
            env.pop(key)
    if remove_instance:
        env.pop("BOT_INSTANCE", None)
    else:
        env["BOT_INSTANCE"] = "foxora"
    if extra_env:
        env.update(extra_env)
    code = (
        "import json\n"
        "from modules import runtime_paths\n"
        "print(json.dumps(runtime_paths.describe()))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_foxora_instance_uses_isolated_home_dir(tmp_path: Path) -> None:
    info = _run_describe(tmp_path)
    assert info["instance"] == "foxora"
    expected = str((tmp_path / ".foxora").resolve())
    assert info["data_dir"] == expected
    for key in ("config_dir", "log_dir", "db_dir"):
        assert info[key].startswith(expected + os.sep), (key, info[key])
    # Never inside a B2/soroush-bot directory or the project tree.
    for key in ("data_dir", "config_dir", "log_dir", "db_dir"):
        assert "soroush-bot" not in info[key]
        assert not info[key].startswith(str(REPO_ROOT))


def test_bot_instance_marker_file_keeps_foxora_without_env(tmp_path: Path) -> None:
    # The shipped .bot_instance marker must pin direct `python3 main.py`
    # runs to the foxora instance even when BOT_INSTANCE is unset.
    marker = REPO_ROOT / ".bot_instance"
    assert marker.read_text(encoding="utf-8").strip() == "foxora"
    info = _run_describe(tmp_path, remove_instance=True)
    assert info["instance"] == "foxora"
    assert info["data_dir"] == str((tmp_path / ".foxora").resolve())


def test_instance_specific_env_override_wins(tmp_path: Path) -> None:
    custom = tmp_path / "custom-data"
    info = _run_describe(tmp_path, {"SOROUSH_BOT_DATA_DIR_FOXORA": str(custom)})
    assert info["data_dir"] == str(custom.resolve())


def test_dangerous_public_override_cannot_point_at_b2_main(tmp_path: Path) -> None:
    # If an operator exports SOROUSH_BOT_DATA_DIR aimed at B2's main
    # directory, Foxora must NOT write there; it gets suffixed instead.
    b2_default = tmp_path / ".local" / "share" / "soroush-bot"
    info = _run_describe(tmp_path, {"SOROUSH_BOT_DATA_DIR": str(b2_default)})
    assert info["data_dir"] != str(b2_default.resolve())
    assert info["data_dir"].endswith("soroush-bot-foxora")


def test_ensure_layout_creates_private_dirs(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["BOT_INSTANCE"] = "foxora"
    env.pop("SOROUSH_BOT_DATA_DIR", None)
    code = (
        "from modules.runtime_paths import DATA_DIR, CONFIG_DIR, LOG_DIR, DB_DIR, ensure_layout\n"
        "ensure_layout()\n"
        "import os\n"
        "for d in (DATA_DIR, CONFIG_DIR, LOG_DIR, DB_DIR):\n"
        "    assert d.is_dir() and str(d).startswith(os.path.expanduser('~/.foxora')), d\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().endswith("ok")
    assert (tmp_path / ".foxora" / "logs").is_dir()
    assert not (tmp_path / ".local" / "share").exists()

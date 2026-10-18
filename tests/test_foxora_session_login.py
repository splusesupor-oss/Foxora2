"""Tests for permanent Soroush session error handling and Foxora2 session isolation.

تضمین می‌کند:
  * خطای دائم نشست (``AuthKeyUnregisteredError``) تشخیص داده می‌شود و هرگز
    باعث حلقهٔ اتصال مجدد یا restart-loop در watchdog نمی‌شود.
  * ابزار ``tools/login_primary_once.py`` نشست جدید را فقط در
    ``SOROUSH_SESSION_STRING_FOXORA2`` می‌نویسد و به ``SOROUSH_SESSION_STRING_FOXORA``
    یا ``SOROUSH_SESSION_STRING`` (نشست ربات‌های دیگر) دست نمی‌زند.
  * خواندن نشست در ``core/bot_working_split_ok.py`` اول سراغ متغیر اختصاصی
    ``SOROUSH_SESSION_STRING_FOXORA2`` می‌رود.
"""
from __future__ import annotations

from core.bot_working_split_ok import (
    FatalAuthError,
    is_permanent_auth_error,
    resolve_session_string,
)
from modules.watchdog_reporting import FATAL_AUTH_EXIT_CODE
from tools import login_primary_once as login_tool
import watchdog


class AuthKeyUnregisteredError(Exception):
    """Fake splusthon RPC error with the exact production class name."""


class TransientNetworkError(Exception):
    pass


def test_detects_auth_key_unregistered_by_class_and_message():
    err = AuthKeyUnregisteredError(
        "The key is not registered in the system (caused by GetStateRequest)"
    )
    assert is_permanent_auth_error(err) is True
    assert is_permanent_auth_error(
        RuntimeError("RPCError 401: AUTH_KEY_UNREGISTERED")
    ) is True
    assert is_permanent_auth_error(TransientNetworkError("timeout")) is False
    assert is_permanent_auth_error(ConnectionResetError("eof")) is False
    assert is_permanent_auth_error(None) is False


def test_watchdog_recognizes_fatal_auth_exit_code():
    assert FATAL_AUTH_EXIT_CODE == 43
    assert watchdog.is_fatal_auth_exit(FATAL_AUTH_EXIT_CODE) is True
    assert watchdog.is_fatal_auth_exit(1) is False
    assert watchdog.is_fatal_auth_exit(0) is False
    assert issubclass(FatalAuthError, RuntimeError)


def test_session_resolution_prefers_foxora2_specific_env_over_other_sessions():
    env = {
        "SOROUSH_SESSION_STRING": "BROKEN_B2_OR_OLD_GENERIC_SESSION",
        "SOROUSH_SESSION_STRING_FOXORA": "FOXORA_1_SESSION",
        "SOROUSH_SESSION_STRING_FOXORA2": "FRESH_FOXORA2_SESSION",
    }
    assert (
        resolve_session_string(env_get=env.get, instance="foxora2")
        == "FRESH_FOXORA2_SESSION"
    )
    # اگر فقط generic وجود داشته باشد، به‌عنوان fallback خوانده می‌شود (نه FOXORA_1)
    only_generic = {
        "SOROUSH_SESSION_STRING": "FALLBACK_SESSION",
        "SOROUSH_SESSION_STRING_FOXORA": "FOXORA_1_SESSION",
    }
    assert (
        resolve_session_string(env_get=only_generic.get, instance="foxora2")
        == "FALLBACK_SESSION"
    )


def test_login_tool_targets_foxora2_key_and_preserves_other_sessions(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BOT_INSTANCE", "foxora2")
    assert login_tool.session_env_key() == "SOROUSH_SESSION_STRING_FOXORA2"

    env_file = tmp_path / ".env"
    env_file.write_text(
        "BOT_INSTANCE=foxora2\n"
        "SOROUSH_SESSION_STRING=UNTOUCHED_B2_SESSION\n"
        "SOROUSH_SESSION_STRING_FOXORA=UNTOUCHED_FOXORA1_SESSION\n"
        "SOROUSH_SESSION_STRING_FOXORA2=OLD_EXPIRED_FOXORA2\n"
        "API_ID=12345\n",
        encoding="utf-8",
    )

    login_tool.save_primary("NEW_VALID_FOXORA2_SESSION", env_file=env_file)
    content = env_file.read_text(encoding="utf-8").splitlines()

    assert "SOROUSH_SESSION_STRING=UNTOUCHED_B2_SESSION" in content
    assert "SOROUSH_SESSION_STRING_FOXORA=UNTOUCHED_FOXORA1_SESSION" in content
    assert "SOROUSH_SESSION_STRING_FOXORA2=NEW_VALID_FOXORA2_SESSION" in content
    assert "SOROUSH_SESSION_STRING_FOXORA2=OLD_EXPIRED_FOXORA2" not in content
    assert "API_ID=12345" in content


def test_login_tool_appends_foxora2_key_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_INSTANCE", "foxora2")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SOROUSH_SESSION_STRING=KEEP_ME\n"
        "SOROUSH_SESSION_STRING_FOXORA=KEEP_FOXORA1\n",
        encoding="utf-8",
    )

    login_tool.save_primary("BRAND_NEW_FOXORA2", env_file=env_file)
    text = env_file.read_text(encoding="utf-8")
    assert "SOROUSH_SESSION_STRING=KEEP_ME\n" in text
    assert "SOROUSH_SESSION_STRING_FOXORA=KEEP_FOXORA1\n" in text
    assert "SOROUSH_SESSION_STRING_FOXORA2=BRAND_NEW_FOXORA2\n" in text

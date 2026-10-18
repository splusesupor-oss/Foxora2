"""Tests for permanent Soroush session error handling and Foxora2 session isolation.

تضمین می‌کند:
  * خطای دائم نشست (``AuthKeyUnregisteredError``) تشخیص داده می‌شود و هرگز
    باعث حلقهٔ اتصال مجدد یا restart-loop در watchdog نمی‌شود.
  * ``SoroushClient(StringSession())`` در کتابخانهٔ ``SPlusthon`` بدون نیاز به
    وارد کردن دستی ``API_ID`` و ``API_HASH`` با مقادیر پیش‌فرض رسمی سروش‌پلاس
    کار می‌کند.
  * ابزار ``tools/login_primary_once.py`` شماره تلفن، کد تأیید و رمز دومرحله‌ای
    (در صورت فعال بودن) را دریافت کرده و نشست جدید را فقط در
    ``SOROUSH_SESSION_STRING_FOXORA2`` داخل ``.env`` همین پروژه می‌نویسد و به
    ``SOROUSH_SESSION_STRING_FOXORA`` یا ``SOROUSH_SESSION_STRING`` (نشست ربات‌های
    دیگر) دست نمی‌زند.
  * خواندن نشست در ``core/bot_working_split_ok.py`` اول سراغ متغیر اختصاصی
    ``SOROUSH_SESSION_STRING_FOXORA2`` می‌رود.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
import pytest

from core.bot_working_split_ok import (
    FatalAuthError,
    SoroushAntiSpamBot,
    is_permanent_auth_error,
    resolve_session_string,
)
from modules.watchdog_reporting import FATAL_AUTH_EXIT_CODE
from tools import login_primary_once as login_tool
import watchdog


class AuthKeyUnregisteredError(Exception):
    """Fake splusthon RPC error with the exact production class name."""


class SessionPasswordNeededError(Exception):
    """Fake splusthon 2FA error with the exact production class name."""


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


def test_splusthon_soroush_client_works_without_manual_api_id_and_hash(monkeypatch):
    from splusthon import SoroushClient
    from splusthon.sessions import StringSession

    monkeypatch.delenv("API_ID_FOXORA2", raising=False)
    monkeypatch.delenv("API_HASH_FOXORA2", raising=False)
    # حتی اگر متغیرهای عمومی API_ID / API_HASH با مقادیر نمونه در محیط باشند،
    # create_client باید از مقادیر پیش‌فرض داخلی SPlusthon استفاده کند.
    monkeypatch.setenv("API_ID", "12345")
    monkeypatch.setenv("API_HASH", "not_a_real_soroush_hash")

    direct_client = SoroushClient(StringSession())
    tool_client = login_tool.create_client()

    assert direct_client.api_id == 1030400
    assert isinstance(direct_client.api_hash, str) and len(direct_client.api_hash) == 32
    assert tool_client.api_id == direct_client.api_id
    assert tool_client.api_hash == direct_client.api_hash


def test_bot_make_client_uses_splusthon_defaults_without_manual_api_credentials(monkeypatch):
    monkeypatch.delenv("API_ID_FOXORA2", raising=False)
    monkeypatch.delenv("API_HASH_FOXORA2", raising=False)
    monkeypatch.setenv("API_ID", "12345")
    monkeypatch.setenv("API_HASH", "wrong_generic_hash")
    monkeypatch.delenv("SOROUSH_SESSION_STRING_FOXORA2", raising=False)
    monkeypatch.delenv("SOROUSH_SESSION_STRING", raising=False)

    dummy_bot = SimpleNamespace(
        config_manager=SimpleNamespace(get=lambda _k, default=None: default)
    )
    client = SoroushAntiSpamBot._make_client(dummy_bot)
    assert client.api_id == 1030400
    assert isinstance(client.api_hash, str) and len(client.api_hash) == 32


def test_normalize_phone_and_code_accept_persian_digits_and_local_format():
    assert login_tool.normalize_phone("09121234567") == "+989121234567"
    assert login_tool.normalize_phone("+98 912-123-4567") == "+989121234567"
    assert login_tool.normalize_phone("۰۹۱۲۱۲۳۴۵۶۷") == "+989121234567"
    assert login_tool.normalize_phone("+۹۸۹۱۲۱۲۳۴۵۶۷") == "+989121234567"
    assert login_tool.normalize_phone("00989121234567") == "+989121234567"

    with pytest.raises(ValueError):
        login_tool.normalize_phone("")
    with pytest.raises(ValueError):
        login_tool.normalize_phone("abc")

    assert login_tool.normalize_code(" ۱۲۳۴۵ ") == "12345"
    assert login_tool.normalize_code("67-890") == "67890"
    with pytest.raises(ValueError):
        login_tool.normalize_code("   ")


def test_login_tool_targets_foxora2_key_even_if_foreign_bot_instance_set(
    tmp_path, monkeypatch
):
    # حتی اگر در شل متغیر BOT_INSTANCE به اشتباه foxora یا main باشد،
    # اسکریپت Foxora2 فقط باید SOROUSH_SESSION_STRING_FOXORA2 را هدف بگیرد.
    monkeypatch.setenv("BOT_INSTANCE", "foxora")
    assert login_tool.session_env_key() == "SOROUSH_SESSION_STRING_FOXORA2"

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


def test_login_once_flow_without_2fa_saves_only_foxora2_session(
    tmp_path, monkeypatch, capsys
):
    for key in (
        "BOT_INSTANCE",
        "SOROUSH_SESSION_STRING",
        "SOROUSH_SESSION_STRING_FOXORA",
        "SOROUSH_SESSION_STRING_FOXORA2",
    ):
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "BOT_INSTANCE=foxora2\n"
        "SOROUSH_SESSION_STRING=B2_SESSION_KEEP\n"
        "SOROUSH_SESSION_STRING_FOXORA=FOXORA1_SESSION_KEEP\n",
        encoding="utf-8",
    )

    events_order = []

    class FakeClient:
        def __init__(self):
            self.session = SimpleNamespace(
                auth_key=object(),
                save=lambda: "SAVED_SESSION_NO_2FA_FOXORA2",
            )

        async def connect(self):
            events_order.append("connect")

        async def send_code_request(self, phone):
            events_order.append(("send_code", phone))

        async def sign_in(self, phone=None, code=None, *, password=None):
            events_order.append(("sign_in", phone, code, password))

        async def disconnect(self):
            events_order.append("disconnect")

    answers = iter(["۰۹۱۲۱۲۳۴۵۶۷", "۱۲۳۴۵"])
    saved_key = asyncio.run(
        login_tool.login_once(
            input_fn=lambda _prompt="": next(answers),
            env_file=env_file,
            client=FakeClient(),
        )
    )

    assert saved_key == "SOROUSH_SESSION_STRING_FOXORA2"
    assert events_order == [
        "connect",
        ("send_code", "+989121234567"),
        ("sign_in", "+989121234567", "12345", None),
        "disconnect",
    ]
    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert "SOROUSH_SESSION_STRING=B2_SESSION_KEEP" in lines
    assert "SOROUSH_SESSION_STRING_FOXORA=FOXORA1_SESSION_KEEP" in lines
    assert "SOROUSH_SESSION_STRING_FOXORA2=SAVED_SESSION_NO_2FA_FOXORA2" in lines

    captured = capsys.readouterr()
    assert "SAVED_SESSION_NO_2FA_FOXORA2" not in captured.out
    assert "SAVED_SESSION_NO_2FA_FOXORA2" not in captured.err


def test_login_once_flow_with_2fa_password_saves_only_foxora2_session(
    tmp_path, monkeypatch, capsys
):
    for key in (
        "BOT_INSTANCE",
        "SOROUSH_SESSION_STRING",
        "SOROUSH_SESSION_STRING_FOXORA",
        "SOROUSH_SESSION_STRING_FOXORA2",
    ):
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "BOT_INSTANCE=foxora2\n"
        "SOROUSH_SESSION_STRING_FOXORA=FOXORA1_SESSION_KEEP\n",
        encoding="utf-8",
    )

    events_order = []

    class Fake2FAClient:
        def __init__(self):
            self.session = SimpleNamespace(
                auth_key=object(),
                save=lambda: "SAVED_SESSION_WITH_2FA_FOXORA2",
            )

        async def connect(self):
            events_order.append("connect")

        async def send_code_request(self, phone):
            events_order.append(("send_code", phone))

        async def sign_in(self, phone=None, code=None, *, password=None):
            events_order.append(("sign_in", phone, code, password))
            if code is not None and password is None:
                raise SessionPasswordNeededError("Two-steps verification is enabled")

        async def disconnect(self):
            events_order.append("disconnect")

    answers = iter(["+989129876543", "54321", "MySecret2FA!"])
    saved_key = asyncio.run(
        login_tool.login_once(
            input_fn=lambda _prompt="": next(answers),
            env_file=env_file,
            client=Fake2FAClient(),
        )
    )

    assert saved_key == "SOROUSH_SESSION_STRING_FOXORA2"
    assert events_order == [
        "connect",
        ("send_code", "+989129876543"),
        ("sign_in", "+989129876543", "54321", None),
        ("sign_in", "+989129876543", None, "MySecret2FA!"),
        "disconnect",
    ]
    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert "SOROUSH_SESSION_STRING_FOXORA=FOXORA1_SESSION_KEEP" in lines
    assert "SOROUSH_SESSION_STRING_FOXORA2=SAVED_SESSION_WITH_2FA_FOXORA2" in lines

    captured = capsys.readouterr()
    assert "SAVED_SESSION_WITH_2FA_FOXORA2" not in captured.out
    assert "MySecret2FA!" not in captured.out

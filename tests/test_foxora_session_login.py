"""Foxora-specific login/session isolation guarantees.

چرا این تست‌ها لازم‌اند: خطای واقعی کاربر (AuthKeyUnregisteredError)
سه تضمین خواست: (۱) ابزار ورود فقط یک بار تلاش می‌کند و نشست را فقط در
``SOROUSH_SESSION_STRING_FOXORA`` می‌نویسد، (۲) متغیر اختصاصی اولویت
قطعی دارد، (۳) نشست نامعتبر دائم بدون حلقهٔ اتصال/ری‌استارت با یک خروج
تمیز گزارش می‌شود.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest


def _ensure_splusthon_stub():
    """Meta-path stub کامل برای splusthon (در نصب نبودن واقعی).

    core زنجیره‌ای عمیق از ``splusthon.tl.functions.*`` را لازم دارد؛
    stub دستی هر submodule غیرعملی است. این finder هر ماژول مجازی
    splusthon را با attributeهای placeholder می‌سازد؛ در محیطی که
    splusthon واقعی نصب است هیچ کاری نمی‌کند.
    """
    import importlib.abc
    import importlib.machinery
    import importlib.util

    if importlib.util.find_spec("splusthon") is not None:
        return

    class _MagicLoader(importlib.abc.Loader):
        def create_module(self, spec):
            mod = types.ModuleType(spec.name)

            def __getattr__(name):
                placeholder = type(
                    name, (), {"__init__": lambda self, *a, **k: None}
                )
                setattr(mod, name, placeholder)
                return placeholder

            mod.__getattr__ = __getattr__
            return mod

        def exec_module(self, module):
            return None

    class _MagicFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            if fullname == "splusthon" or fullname.startswith("splusthon."):
                return importlib.machinery.ModuleSpec(
                    fullname, _MagicLoader(), is_package=True
                )
            return None

    sys.meta_path.insert(0, _MagicFinder())


_ensure_splusthon_stub()

from core.bot_working_split_ok import (  # noqa: E402
    FatalAuthError,
    is_permanent_auth_error,
    resolve_session_string,
)
import modules.watchdog_reporting as watchdog_reporting  # noqa: E402
import tools.login_primary_once as login_tool  # noqa: E402
import watchdog as watchdog_module  # noqa: E402


# ---------------------------------------------------------------------------
# 1) ابزار ورود: فقط یک تلاش، فقط متغیر اختصاصی
# ---------------------------------------------------------------------------

def test_session_env_key_is_instance_scoped(monkeypatch):
    monkeypatch.setenv("BOT_INSTANCE", "foxora")
    assert login_tool.session_env_key() == "SOROUSH_SESSION_STRING_FOXORA"
    monkeypatch.setenv("BOT_INSTANCE", "bot7")
    assert login_tool.session_env_key() == "SOROUSH_SESSION_STRING_BOT7"
    # بدون BOT_INSTANCE: نشانگر .bot_instance از ریشهٔ پروژه (foxora)
    monkeypatch.delenv("BOT_INSTANCE", raising=False)
    key = login_tool.session_env_key()
    assert key.endswith("_FOXORA")
    # هرگز به متغیر عمومی تبدیل نمی‌شود
    assert key != "SOROUSH_SESSION_STRING"


def test_save_primary_writes_only_instance_key(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_INSTANCE", "foxora")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "SOROUSH_SESSION_STRING=OLD-B2-SESSION\n"
        "SOROUSH_SESSION_STRING_B2=config-of-other-bot\n"
        "API_ID=1\n",
        encoding="utf-8",
    )
    login_tool.save_primary(
        "NEW-FOXORA-SESSION", key="SOROUSH_SESSION_STRING_FOXORA",
        env_file=env_file,
    )
    text = env_file.read_text(encoding="utf-8")
    # متغیر اختصاصی Foxora نوشته شد
    assert "SOROUSH_SESSION_STRING_FOXORA=NEW-FOXORA-SESSION" in text
    # متغیر عمومی (B2) و متغیرهای ربات‌های دیگر دست‌نخورده‌اند
    assert "SOROUSH_SESSION_STRING=OLD-B2-SESSION" in text
    assert "SOROUSH_SESSION_STRING_B2=config-of-other-bot" in text
    assert text.count("SOROUSH_SESSION_STRING_FOXORA=") == 1
    # مجوز محرمانه نگه داشته می‌شود
    assert (env_file.stat().st_mode & 0o777) == 0o600
    # اجرای دوباره: جایگزینی درجا، بدون خط تکراری
    login_tool.save_primary(
        "SECOND-SESSION", key="SOROUSH_SESSION_STRING_FOXORA",
        env_file=env_file,
    )
    text = env_file.read_text(encoding="utf-8")
    assert text.count("SOROUSH_SESSION_STRING_FOXORA=") == 1
    assert "SECOND-SESSION" in text
    assert "OLD-B2-SESSION" in text


def _install_recording_client(monkeypatch, *, fail_sign_in):
    """کلاینت استب با شمارنده؛ تضمین می‌کند هر مرحله فقط یک بار صدا زده شود."""
    calls = {"connect": 0, "send_code": 0, "sign_in": 0, "disconnect": 0}

    class _StringSession:
        def __init__(self, value=None):
            self.value = value

        def save(self):
            return "RECORDED-SESSION"

    class _RecorderClient:
        def __init__(self, session, *_args):
            self.session = session

        async def connect(self):
            calls["connect"] += 1

        async def send_code_request(self, *_args):
            calls["send_code"] += 1

        async def sign_in(self, *_args, **_kwargs):
            calls["sign_in"] += 1
            if fail_sign_in:
                AuthError = type(
                    "AuthKeyUnregisteredError", (Exception,), {}
                )
                raise AuthError(
                    "AuthKeyUnregisteredError: The key is not registered"
                    " in the system"
                )

        async def disconnect(self):
            calls["disconnect"] += 1

    fake = types.ModuleType("splusthon")
    fake.SoroushClient = _RecorderClient
    sessions = types.ModuleType("splusthon.sessions")
    sessions.StringSession = _StringSession
    monkeypatch.setitem(sys.modules, "splusthon", fake)
    monkeypatch.setitem(sys.modules, "splusthon.sessions", sessions)
    return calls


def test_login_success_writes_instance_key_once(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("BOT_INSTANCE", "foxora")
    env_file = tmp_path / ".env"
    env_file.write_text("SOROUSH_SESSION_STRING=OLD-B2\n", encoding="utf-8")
    monkeypatch.setattr(login_tool, "ENV_FILE", env_file)
    calls = _install_recording_client(monkeypatch, fail_sign_in=False)
    inputs = iter(["+989120000000", "12345"])
    monkeypatch.setattr("builtins.input", lambda *_a: next(inputs))

    import asyncio

    asyncio.run(login_tool.main())

    # هر مرحلهٔ ورود دقیقاً یک بار (بدون retry loop)
    assert calls == {
        "connect": 1, "send_code": 1, "sign_in": 1, "disconnect": 1,
    }
    text = env_file.read_text(encoding="utf-8")
    assert "SOROUSH_SESSION_STRING_FOXORA=RECORDED-SESSION" in text
    assert "SOROUSH_SESSION_STRING=OLD-B2\n" in text
    # هیچ مقدار session در خروجی سرکنسول چاپ نمی‌شود (بدون سکرت)
    out = capsys.readouterr().out
    assert "RECORDED-SESSION" not in out
    assert "SOROUSH_SESSION_STRING_FOXORA" in out


def test_login_failure_attempts_once_and_keeps_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_INSTANCE", "foxora")
    env_file = tmp_path / ".env"
    before = "SOROUSH_SESSION_STRING=OLD-B2\n"
    env_file.write_text(before, encoding="utf-8")
    monkeypatch.setattr(login_tool, "ENV_FILE", env_file)
    calls = _install_recording_client(monkeypatch, fail_sign_in=True)
    inputs = iter(["+989120000000", "00000"])
    monkeypatch.setattr("builtins.input", lambda *_a: next(inputs))

    import asyncio

    with pytest.raises(SystemExit) as outcome:
        asyncio.run(login_tool.main())
    assert outcome.value.code == 1
    # فقط یک تلاش برای هر مرحله؛ هیچ حلقهٔ اتصال مجددی وجود ندارد
    assert calls["connect"] == 1
    assert calls["send_code"] == 1
    assert calls["sign_in"] == 1
    # در شکست، .env همان‌طور که بود می‌ماند (نشست خراب overwrite نشد)
    assert env_file.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# 2) اولویت قطعی متغیر اختصاصی هنگام اجرا
# ---------------------------------------------------------------------------

def test_resolve_session_string_priority_and_isolation():
    env = {
        "SOROUSH_SESSION_STRING_FOXORA": "F-SESSION",
        "SOROUSH_SESSION_STRING": "OLD-BROKEN-GENERIC",
    }
    lookup = env.get
    # متغیر اختصاصی همیشه برنده است حتی اگر generic هم تعریف شده باشد
    assert resolve_session_string(env_get=lookup, instance="foxora") == "F-SESSION"
    # بدون اختصاصی: generic به‌کار می‌رود (راه مهاجرت)، سپس config
    assert (
        resolve_session_string(
            env_get={}.get, config_get=lambda *_a: "CFG", instance="foxora"
        )
        == "CFG"
    )
    assert resolve_session_string(env_get=lookup, instance="nope") == "OLD-BROKEN-GENERIC"
    # هیچ‌کدام: رشتهٔ خالی (هرگز None نیست تا مسیرهای downstream نشکنند)
    assert resolve_session_string(env_get={}.get, instance="foxora") == ""


# ---------------------------------------------------------------------------
# 3) خطای دائم نشست: تشخیص و قرارداد exit code (بدون حلقه)
# ---------------------------------------------------------------------------

def test_permanent_auth_error_detection():
    Unregistered = type("AuthKeyUnregisteredError", (Exception,), {})
    error = Unregistered(
        "AuthKeyUnregisteredError: The key is not registered in the system"
    )
    assert is_permanent_auth_error(error) is True
    Deactivated = type("UserDeactivatedError", (Exception,), {})
    assert is_permanent_auth_error(Deactivated("user deleted")) is True
    # متنِ شناخته‌شده حتی با نام کلاس ناشناخته
    assert is_permanent_auth_error(RuntimeError("AUTH_KEY_UNREGISTERED")) is True
    # خطاهای گذرای شبکه دائمی نیستند و باید مسیر reconnect عادی را داشته باشند
    assert is_permanent_auth_error(ConnectionError("network hiccup")) is False
    assert is_permanent_auth_error(TimeoutError("slow server")) is False
    assert is_permanent_auth_error(None) is False


def test_fatal_exit_code_contract_between_bot_and_watchdog():
    assert watchdog_reporting.FATAL_AUTH_EXIT_CODE == 43
    assert watchdog_module.is_fatal_auth_exit(43) is True
    for benign_code in (0, 1, 2, 130):
        assert watchdog_module.is_fatal_auth_exit(benign_code) is False


def test_main_exits_once_with_login_guidance(monkeypatch, capsys):
    """ربات با نشست دائماً نامعتبر: یک خروج تمیز با کد 43؛ هیچ rebuild loop ای ندارد."""
    import asyncio

    import main as main_module

    class _FailedBot:
        def __init__(self):
            pass

        async def run(self):
            Unregistered = type("AuthKeyUnregisteredError", (Exception,), {})
            raise FatalAuthError("نشست از کار افتاده") from Unregistered(
                "The key is not registered in the system"
            )

    monkeypatch.setattr(main_module, "SoroushAntiSpamBot", _FailedBot)
    # در تست واقعاً قفل pid نزن (دیتا فقط ~/.foxora است، اما اینجا لازم نیست)
    monkeypatch.setattr(main_module, "_acquire_instance_lock", lambda: None)

    with pytest.raises(SystemExit) as outcome:
        asyncio.run(main_module.main())
    assert outcome.value.code == watchdog_reporting.FATAL_AUTH_EXIT_CODE
    printed = capsys.readouterr().out
    assert "tools/login_primary_once.py" in printed
    # دقیقاً یک راهنمای متمرکز چاپ شد؛ دیگر print/loop تکراری در کار نیست
    assert printed.count("login_primary_once.py") >= 1

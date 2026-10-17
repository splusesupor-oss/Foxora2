"""Foxora: تشخیص مالک گروه هنگام فعال‌سازی باید مقاوم و قابل ردیابی باشد.

سناریوی واقعی کاربر: ادمین‌ها لیست شدند اما مالک «شناسایی نشد»، چون
ردیف Creator بدون آبجکت User کامل برگشته بود (اکانت حذف‌شده/ناشناس).
این تست‌ها رفتار جدید را قفل می‌کنند: creator هرگز بی‌صدا skip نمی‌شود
و ID عددی به کاربر نمایش داده نمی‌شود.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from handlers.message_handler import get_activation_admin_info


def _user(user_id, username=None, first_name="کاربر"):
    return SimpleNamespace(id=user_id, username=username, first_name=first_name)


def _participant(class_name, user_id, **attrs):
    cls = type(class_name, (), {})
    instance = cls()
    instance.user_id = user_id
    for key, value in attrs.items():
        setattr(instance, key, value)
    return instance


class _ClientStub:
    """بازگشتیِ ثابت برای GetParticipantsRequest؛ نبود گروه پایهٔ fallback."""

    def __init__(self, users, participants):
        self._response = SimpleNamespace(users=users, participants=participants)

    async def get_input_entity(self, chat_id):
        return SimpleNamespace(channel_id=chat_id)

    async def __call__(self, request):
        return self._response


class _LoggerStub:
    def __init__(self):
        self.infos = []
        self.errors = []

    def log_info(self, message):
        self.infos.append(str(message))

    def log_error(self, message):
        self.errors.append(str(message))


def _invoke(client, logger):
    bot = SimpleNamespace(client=client, logger=logger)
    return asyncio.run(get_activation_admin_info(bot, chat_id=-1001))


def test_owner_detected_normally_from_creator_class():
    client = _ClientStub(
        users=[_user(1, username="king"), _user(2, username="mod")],
        participants=[
            _participant("ChannelParticipantCreator", 1),
            _participant("ChannelParticipantAdmin", 2),
        ],
    )
    logger = _LoggerStub()
    owner, admins = _invoke(client, logger)
    assert owner == "@king"
    assert admins == ["@mod"]
    # با موفقیت تشخیص، هیچ دیباگِ «مالک یافت نشد» ثبت نمی‌شود
    assert not any("GROUP OWNER NOT FOUND" in line for line in logger.errors)


def test_creator_without_user_row_is_not_silently_dropped():
    """قابل‌عایت‌ترین باگ واقعی: creator سرور آمده ولی آبجکت User نه."""
    client = _ClientStub(
        users=[_user(2, username="mod")],
        participants=[
            _participant("ChannelParticipantCreator", 999),
            _participant("ChannelParticipantAdmin", 2),
        ],
    )
    logger = _LoggerStub()
    owner, admins = _invoke(client, logger)
    # دیگر «شناسایی نشد» نمی‌بینیم؛ دلیل دقیق به‌نمایش درمی‌آید و ID لو نمی‌رود
    assert owner == "شناسایی شد (اما دسترسی به اطلاعاتش ممکن نیست)"
    assert "999" not in owner
    assert admins == ["@mod"]


def test_creator_detected_via_is_creator_flag():
    """لایه‌های جدید: سازنده به‌صورت Admin + is_creator می‌رسد."""
    client = _ClientStub(
        users=[_user(7, username="flagowner")],
        participants=[
            _participant("ChannelParticipantAdmin", 7, is_creator=True),
        ],
    )
    logger = _LoggerStub()
    owner, admins = _invoke(client, logger)
    assert owner == "@flagowner"
    # سازنده نباید دوباره در لیست ادمین‌ها تکرار شود
    assert admins == []


def test_no_creator_row_reports_diagnostics_once():
    client = _ClientStub(
        users=[_user(2, username="mod")],
        participants=[_participant("ChannelParticipantAdmin", 2)],
    )
    logger = _LoggerStub()
    owner, admins = _invoke(client, logger)
    assert owner is None  # send_activation_message خودش "یافت نشد" می‌سازد
    assert admins == ["@mod"]
    diag = [line for line in logger.errors if "GROUP OWNER NOT FOUND" in line]
    assert len(diag) == 1
    assert "group_id=-1001" in diag[0]
    assert "creator_rows=0" in diag[0]

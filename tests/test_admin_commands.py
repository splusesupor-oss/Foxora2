from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from foxbot.admin_commands import handle_group_admin_command
from foxbot.domain import Member, MemberPage, ModerationResult
from foxbot.filters import WordFilter
from foxbot.group_admin import GroupAdminService
from foxbot.management import MemberManagementService
from foxbot.security import AccessController, OwnerRegistry
from foxbot.storage import BotStore


class FakePlatform:
    def __init__(self) -> None:
        self.members: dict[str, Member] = {}
        self.recent: list[Any] = []
        self.messages: dict[int, Any] = {}
        self.deleted: list[tuple[str, int]] = []
        self.locked: dict[str, bool] = {}
        self.title: dict[str, str] = {}
        self.photo: dict[str, str] = {}
        self.pinned: dict[str, int] = {}
        self.members_count = 4
        self.native_admin_ids: set[str] = set()

    async def is_native_admin(self, _group_id: str, user_id: str) -> bool:
        return str(user_id) in self.native_admin_ids

    async def list_members_page(self, group_id: str, offset: int, limit: int, *, include_bio: bool = False) -> MemberPage:
        del include_bio
        members = [member for member in self.members.values() if member.group_id == str(group_id)]
        batch = members[offset:offset + limit]
        next_offset = offset + len(batch) if offset + len(batch) < len(members) else None
        return MemberPage(tuple(batch), next_offset, total_count=len(members), raw_count=len(batch))

    async def list_active_mutes_page(self, *_args, **_kwargs) -> MemberPage:
        return MemberPage((), None, total_count=0, raw_count=0)

    async def get_member(self, group_id: str, user_id: str, *, include_bio: bool = False) -> Member | None:
        del include_bio
        member = self.members.get(str(user_id))
        return member if member and member.group_id == str(group_id) else None

    async def resolve_replied_user(self, _group_id: str, event: Any) -> Any | None:
        return getattr(event, "reply_user", None)

    async def resolve_username(self, _group_id: str, username: str) -> Any | None:
        return SimpleNamespace(id=81, username=username, first_name="Resolved", last_name="User")

    async def get_message(self, group_id: str, message_id: int) -> Any | None:
        message = self.messages.get(int(message_id))
        if message is None or str(getattr(message, "chat_id", group_id)) != str(group_id):
            return None
        return message

    async def get_recent_messages(self, _group_id: str, limit: int) -> list[Any]:
        return self.recent[:limit]

    async def delete_message(self, group_id: str, message_id: int) -> bool:
        self.deleted.append((str(group_id), int(message_id)))
        return True

    async def set_group_locked(self, group_id: str, locked: bool, *, restore_rights=None):
        self.locked[str(group_id)] = bool(locked)
        return {"send_messages": False} if restore_rights is None else restore_rights

    async def change_group_title(self, group_id: str, title: str) -> bool:
        self.title[str(group_id)] = title
        return True

    async def change_group_photo(self, group_id: str, file_path: str) -> bool:
        self.photo[str(group_id)] = file_path
        return True

    async def pin_group_message(self, group_id: str, message_id: int) -> bool:
        self.pinned[str(group_id)] = int(message_id)
        return True

    async def get_pinned_message_id(self, group_id: str) -> int | None:
        return self.pinned.get(str(group_id))

    async def get_group_member_count(self, _group_id: str) -> int:
        return self.members_count

    async def mute_member(self, *_args, **_kwargs) -> ModerationResult:
        return ModerationResult(True, verified=True)

    async def unmute_member(self, *_args, **_kwargs) -> ModerationResult:
        return ModerationResult(True, verified=True)

    async def kick_member(self, *_args, **_kwargs) -> ModerationResult:
        return ModerationResult(True, verified=True)

    async def ban_member(self, *_args, **_kwargs) -> ModerationResult:
        return ModerationResult(True, verified=True)

    async def unban_member(self, *_args, **_kwargs) -> ModerationResult:
        return ModerationResult(True, verified=True)


def make_member(group_id: str = "-1001", user_id: str = "77", *, username: str = "target") -> Member:
    return Member(
        group_id=group_id,
        user_id=user_id,
        display_name="Target User",
        username=username,
        is_member=True,
        membership_status_known=True,
        is_admin=False,
        admin_status_known=True,
        is_muted=False,
        mute_status_known=True,
        is_banned=False,
        ban_status_known=True,
    )


class Event:
    is_group = True
    is_private = False

    def __init__(self, text: str, *, group_id: str = "-1001", actor_id: str = "owner", reply_user: Any | None = None, reply_id: int | None = None) -> None:
        self.chat_id = group_id
        self.sender_id = actor_id
        self.raw_text = text
        self.sender = SimpleNamespace(id=actor_id, first_name="Group", last_name="Owner", username="owner")
        self.reply_user = reply_user
        self.reply_to = SimpleNamespace(reply_to_msg_id=reply_id) if reply_id else None
        self.message = SimpleNamespace(reply_to_msg_id=reply_id, entities=[])
        self.replies: list[tuple[str, list[Any] | None, bool]] = []


class Harness:
    def __init__(self, tmp_path) -> None:
        self.store = BotStore(":memory:")
        self.platform = FakePlatform()
        self.owner_path = tmp_path / "owner.json"
        self.owner_path.write_text('{"user_id":"global-owner"}', encoding="utf-8")
        self.access = AccessController(
            OwnerRegistry(self.owner_path), self.store, self.platform.is_native_admin
        )
        self.store.set_group_owner("-1001", "owner")
        self.management = MemberManagementService(
            self.platform,
            self.store,
            self.access,
            mute_duration_provider=lambda _group: 3600,
        )
        self.group_admin = GroupAdminService(self.store, self.access, self.management)

        async def reply(event, text, *, formatting_entities=None, prefer_respond=False):
            event.replies.append((text, formatting_entities, prefer_respond))
            event.last_sent_id = 1000 + len(event.replies)
            return SimpleNamespace(id=event.last_sent_id)

        self.reply = reply

    async def dispatch(self, event: Event) -> bool:
        return await handle_group_admin_command(
            event,
            self.group_admin,
            group_id=event.chat_id,
            actor_id=event.sender_id,
            text=event.raw_text,
            reply=self.reply,
        )


async def _scenario_rules_and_admin_list(tmp_path) -> None:
    app = Harness(tmp_path)
    start = Event("ثبت قوانین")
    assert await app.dispatch(start)
    assert start.replies[-1][0] == "📜 لطفاً قوانین گروه را ارسال کنید."

    save = Event("قانون اول\nقانون دوم")
    assert await app.dispatch(save)
    assert app.group_admin.get_law("-1001") == "قانون اول\nقانون دوم"
    assert save.replies[-1][0] == "📜 قوانین گروه ثبت شد ✅"

    show = Event("قوانین", actor_id="member")
    assert await app.dispatch(show)
    assert show.replies[-1][0] == "قانون اول\nقانون دوم"

    app.store.add_group_command_member(
        "-1001", "registered_admin", "88", display_name="Registered Admin", username="regadmin"
    )
    admin_list = Event("لیست ادمین")
    assert await app.dispatch(admin_list)
    assert "@regadmin" in admin_list.replies[-1][0]
    assert "✍ ادمین‌های گروه" in admin_list.replies[-1][0]
    app.store.close()


def test_rules_admin_list_and_read_only_rules(tmp_path) -> None:
    asyncio.run(_scenario_rules_and_admin_list(tmp_path))


async def _scenario_warning_reset_and_privilege(tmp_path) -> None:
    app = Harness(tmp_path)
    target = SimpleNamespace(id=77, username="target", first_name="Target", last_name="User")
    app.platform.members["77"] = make_member()
    warn = Event("اخطار", reply_user=target, reply_id=10)
    assert await app.dispatch(warn)
    assert app.group_admin.warning_count("-1001", "77") == 1
    assert "یک اخطار دستی دریافت کرد" in warn.replies[-1][0]

    remove = Event("حذف اخطار", reply_user=target, reply_id=10)
    assert await app.dispatch(remove)
    assert app.group_admin.warning_count("-1001", "77") == 0
    assert remove.replies[-1][0] == "✅ تمام اخطارهای کاربر حذف شد."

    warn_again = Event("اخطار", reply_user=target, reply_id=10)
    assert await app.dispatch(warn_again)
    plural_remove = Event("حذف اخطارها", reply_user=target, reply_id=10)
    assert await app.dispatch(plural_remove)
    assert app.group_admin.warning_count("-1001", "77") == 0
    assert plural_remove.replies[-1][0] == "✅ همهٔ اخطارهای کاربر حذف شد.\nتعداد اخطار: 0"

    denied_plural = Event("حذف اخطارها", actor_id="ordinary", reply_user=target, reply_id=10)
    assert await app.dispatch(denied_plural)
    assert denied_plural.replies[-1][0] == "❌ فقط مالک یا ادمین اجازه حذف اخطارها را دارند"

    unprivileged = Event("صفر", actor_id="ordinary", reply_user=target, reply_id=10)
    assert await app.dispatch(unprivileged)
    assert unprivileged.replies[-1][0] == "❌ فقط مالک اصلی ربات یا مالک گروه اجازه صفر کردن تخلفات را دارد"

    owner_reset = Event("صفر", reply_user=target, reply_id=10)
    assert await app.dispatch(owner_reset)
    assert owner_reset.replies[-1][0] == "✅ تخلفات کاربر صفر شد."
    app.store.close()


def test_warning_add_remove_and_zero_are_group_scoped(tmp_path) -> None:
    asyncio.run(_scenario_warning_reset_and_privilege(tmp_path))


def test_ai_access_commands_support_group_scoped_toggle_reply_and_username_aliases(tmp_path) -> None:
    async def scenario() -> None:
        app = Harness(tmp_path)
        enable = Event("هوش مصنوعی فعال")
        assert await app.dispatch(enable)
        assert app.store.get_group_state("-1001", "google_ai_enabled") is True

        target = SimpleNamespace(id=82, username="target", first_name="Target", last_name="User")
        grant_reply = Event("مجاز", reply_user=target, reply_id=10)
        assert await app.dispatch(grant_reply)
        assert app.store.has_group_command_member("-1001", "ai_allowed", "82")

        grant_username = Event("مجاز @resolveduser")
        assert await app.dispatch(grant_username)
        assert app.store.has_group_command_member("-1001", "ai_allowed", "81")

        listing = Event("لیست هوش مصنوعی")
        assert await app.dispatch(listing)
        assert "@target" in listing.replies[-1][0]
        assert "@resolveduser" in listing.replies[-1][0]

        revoke_spaced = Event("غیر مجاز @resolveduser")
        assert await app.dispatch(revoke_spaced)
        assert not app.store.has_group_command_member("-1001", "ai_allowed", "81")

        grant_again = Event("مجاز @resolveduser")
        assert await app.dispatch(grant_again)
        revoke_compact = Event("غیرمجاز @resolveduser")
        assert await app.dispatch(revoke_compact)
        assert not app.store.has_group_command_member("-1001", "ai_allowed", "81")

        revoke_reply = Event("غیرمجاز", reply_user=target, reply_id=11)
        assert await app.dispatch(revoke_reply)
        assert not app.store.has_group_command_member("-1001", "ai_allowed", "82")

        denied = Event("هوش مصنوعی خاموش", actor_id="ordinary")
        assert await app.dispatch(denied)
        assert denied.replies[-1][0] == "❌ فقط مالک یا ادمین‌های گروه اجازه استفاده از این دستور را دارند"
        assert app.store.get_group_state("-1001", "google_ai_enabled") is True

        disable = Event("هوش مصنوعی خاموش")
        assert await app.dispatch(disable)
        assert app.store.get_group_state("-1001", "google_ai_enabled") is False
        app.store.close()

    asyncio.run(scenario())


async def _scenario_bulk_delete_confirmation(tmp_path) -> None:
    app = Harness(tmp_path)
    for message_id in (301, 302):
        message = SimpleNamespace(
            id=message_id,
            chat_id="-1001",
            sender_id=77,
            sender=SimpleNamespace(id=77, username="target", first_name="Target", last_name="User"),
            raw_text=f"message {message_id}",
            message=f"message {message_id}",
        )
        app.platform.recent.append(message)
        app.platform.messages[message_id] = message

    stage = Event("پاک 2")
    assert await app.dispatch(stage)
    prompt = stage.replies[-1][0]
    job_id = re.search(r"تایید پاک ([A-Fa-f0-9]{12})", prompt).group(1)
    assert app.platform.deleted == []

    confirm = Event(f"تایید پاک {job_id}")
    assert await app.dispatch(confirm)
    assert app.platform.deleted == [("-1001", 301), ("-1001", 302)]
    assert confirm.replies[-1][0] == "2 پیام پاک شد 💣"
    assert app.store.get_group_stats("-1001")["deleted"] == 2
    app.store.close()


def test_bulk_delete_waits_for_confirmation_then_revalidates_messages(tmp_path) -> None:
    asyncio.run(_scenario_bulk_delete_confirmation(tmp_path))


async def _scenario_clipboard_exact_reply(tmp_path) -> None:
    app = Harness(tmp_path)
    guide = Event("کپی بورد")
    assert await app.dispatch(guide)
    approved_filter = WordFilter(WordFilter.read_file("config/banned_words.txt"))
    assert approved_filter.match_global(guide.replies[-1][0]) is None
    pending = app.group_admin.clipboard_pending_guide("-1001")
    assert pending is not None
    target_guide_id = pending["message_id"]
    assert guide.last_sent_id == int(target_guide_id)

    save = Event("saved text", reply_id=int(target_guide_id))
    save.message.entities = []
    assert await app.dispatch(save)
    assert app.group_admin.clipboard("-1001")["text"] == "saved text"
    assert save.replies[-1][0] == "✅ متن کپی بورد با موفقیت ذخیره شد."

    copy = Event("کپی")
    assert await app.dispatch(copy)
    assert copy.replies[-1][0] == "saved text"
    assert copy.replies[-1][2] is True
    app.store.close()


def test_clipboard_requires_reply_to_pending_guide_and_preserves_content(tmp_path) -> None:
    asyncio.run(_scenario_clipboard_exact_reply(tmp_path))


async def _scenario_lock_title_stats(tmp_path) -> None:
    app = Harness(tmp_path)
    lock = Event("!lock")
    assert await app.dispatch(lock)
    assert app.platform.locked["-1001"] is True
    assert lock.replies[-1][0] == "🔒 گروه قفل شد"

    unlock = Event("!unlock")
    assert await app.dispatch(unlock)
    assert app.platform.locked["-1001"] is False
    assert unlock.replies[-1][0] == "🔓 گروه باز شد"

    title = Event("!اسم نام جدید")
    assert await app.dispatch(title)
    assert app.platform.title["-1001"] == "نام جدید"
    assert title.replies[-1][0] == "✅ اسم گروه تغییر کرد"

    stats = Event("آمار گروه", actor_id="ordinary")
    assert await app.dispatch(stats)
    assert "📊 **آمار گروه**" in stats.replies[-1][0]
    assert "تعداد اعضا:** 4" in stats.replies[-1][0]
    app.store.close()


def test_lock_title_and_group_stats_are_routed(tmp_path) -> None:
    asyncio.run(_scenario_lock_title_stats(tmp_path))


def test_native_group_admin_is_not_promoted_to_registered_command_admin(tmp_path) -> None:
    async def scenario() -> None:
        app = Harness(tmp_path)
        app.platform.native_admin_ids.add("native")

        lock = Event("!lock", actor_id="native")
        assert await app.dispatch(lock)
        assert "اجازه استفاده" in lock.replies[-1][0]
        assert "-1001" not in app.platform.locked

        admin_list = Event("لیست ادمین", actor_id="native")
        assert await app.dispatch(admin_list)
        assert "فقط مالک یا ادمین ثبت‌شده" in admin_list.replies[-1][0]
        for command in ("!help", "!timelock", "!title"):
            denied = Event(command, actor_id="native")
            assert await app.dispatch(denied)
            assert "اجازه استفاده" in denied.replies[-1][0]

        # The source explicitly admits platform-native admins for the
        # read-only expiry-status route, not for other management routes.
        expiry = Event("مهلت گروه", actor_id="native")
        assert await app.dispatch(expiry)
        assert "تاریخ انقضایی ثبت نشده" in expiry.replies[-1][0]

        app.store.add_group_command_member(
            "-1001", "registered_admin", "registered", display_name="Registered"
        )
        registered_lock = Event("!lock", actor_id="registered")
        assert await app.dispatch(registered_lock)
        assert app.platform.locked["-1001"] is True
        app.store.close()

    asyncio.run(scenario())


async def _scenario_logs_and_history(tmp_path) -> None:
    app = Harness(tmp_path)
    app.platform.members["77"] = make_member()
    target = SimpleNamespace(id=77, username="target", first_name="Target", last_name="User")
    warn = Event("اخطار", reply_user=target, reply_id=10)
    await app.dispatch(warn)

    history = Event("سابقه ها")
    assert await app.dispatch(history)
    assert "⚠️ اخطار گرفته" in history.replies[-1][0]
    assert "77" not in history.replies[-1][0]

    message = SimpleNamespace(id=201, chat_id="-1001", sender_id=77, sender=target, raw_text="deleted body", message="deleted body")
    app.platform.messages[201] = message
    delete = Event("پاک", reply_id=201)
    assert await app.dispatch(delete)
    assert delete.replies[-1][0] == "✅ با موفقیت پاک شد"

    log = Event("لاگ مدیریتی")
    assert await app.dispatch(log)
    assert "🧾 لاگ مدیریتی گروه:" in log.replies[-1][0]
    assert "حذف پیام" in log.replies[-1][0]
    app.store.close()


def test_admin_log_and_24_hour_user_history(tmp_path) -> None:
    asyncio.run(_scenario_logs_and_history(tmp_path))


def test_history_permission_message_matches_source_for_both_aliases(tmp_path) -> None:
    async def scenario() -> None:
        app = Harness(tmp_path)
        denial = "⛔️ این دستور فقط برای مالک و ادمین‌های گروه است."
        for command in ("سابقه ها", "سابقه‌ها"):
            event = Event(command, actor_id="ordinary")
            assert await app.dispatch(event)
            assert event.replies[-1][0] == denial

        app.store.add_group_command_member(
            "-1001", "registered_admin", "registered", display_name="Registered"
        )
        admin_event = Event("سابقه‌ها", actor_id="registered")
        assert await app.dispatch(admin_event)
        assert admin_event.replies[-1][0] != denial
        app.store.close()

    asyncio.run(scenario())


def test_history_caps_to_25_users_and_50_records_per_user(tmp_path) -> None:
    async def scenario() -> None:
        app = Harness(tmp_path)
        now = datetime.now(UTC)
        for index in range(60):
            app.store.record_group_command_event(
                group_id="-1001",
                event_type="warning",
                user_id="frequent-user",
                display_name="Frequent",
                actor_id="owner",
                detail=f"warning {index}",
                occurred_at=now - timedelta(seconds=index),
            )
        for index in range(30):
            app.store.record_group_command_event(
                group_id="-1001",
                event_type="warning",
                user_id=f"user-{index}",
                display_name=f"User {index}",
                actor_id="owner",
                detail="warning",
                occurred_at=now - timedelta(minutes=index + 2),
            )

        rows = await app.group_admin.history_24h("-1001", "owner")
        assert len(rows) == 50 + 24
        assert sum(row["user_id"] == "frequent-user" for row in rows) == 50
        assert len({row["user_id"] for row in rows}) == 25
        retained = app.store.list_group_command_events(
            "-1001", event_types=("warning",), limit=1750
        )
        assert sum(row["user_id"] == "frequent-user" for row in retained) == 50
        assert len({row["user_id"] for row in retained}) == 25
        app.store.close()

    asyncio.run(scenario())


def test_history_merges_only_successful_kicks_mutes_and_warnings(tmp_path) -> None:
    async def scenario() -> None:
        app = Harness(tmp_path)
        now = datetime.now(UTC)
        app.store.record_group_command_event(
            group_id="-1001",
            event_type="warning",
            user_id="77",
            display_name="Target",
            actor_id="owner",
            detail="warning",
            occurred_at=now,
        )
        app.store.record_kick_event(
            event_key="kick-success",
            group_id="-1001",
            user_id="77",
            display_name="Target",
            result="success",
            occurred_at=now - timedelta(minutes=1),
        )
        app.store.record_kick_event(
            event_key="kick-failed",
            group_id="-1001",
            user_id="88",
            display_name="Unverified",
            result="failed",
            occurred_at=now,
        )
        app.store.record_mute(
            group_id="-1001",
            user_id="77",
            display_name="Target",
            reason="manual mute",
            actor_id="owner",
            actor_kind="group_owner",
            succeeded=True,
            occurred_at=now - timedelta(minutes=2),
        )
        app.store.record_mute(
            group_id="-1001",
            user_id="99",
            display_name="Unverified mute",
            reason="failed mute",
            succeeded=False,
            occurred_at=now,
        )

        rows = await app.group_admin.history_24h("-1001", "owner")
        assert {row["event_type"] for row in rows} == {"warning", "kick", "mute"}
        assert all(row["user_id"] == "77" for row in rows)
        assert len(rows) == 3
        app.store.close()

    asyncio.run(scenario())


def test_history_can_return_full_25_by_50_source_limit(tmp_path) -> None:
    async def scenario() -> None:
        app = Harness(tmp_path)
        now = datetime.now(UTC).replace(microsecond=0)
        events = []
        for user_index in range(30):
            for event_index in range(60):
                occurred_at = now - timedelta(
                    minutes=2 * user_index, seconds=event_index
                )
                events.append(
                    (
                        "-1001",
                        "warning",
                        f"user-{user_index}",
                        f"User {user_index}",
                        "owner",
                        "Owner",
                        "filter hit",
                        None,
                        "",
                        occurred_at.isoformat(),
                    )
                )
        with app.store._transaction() as db:
            db.executemany(
                "INSERT INTO group_command_events(group_id,event_type,user_id,display_name,"
                "actor_id,actor_display,detail,message_id,message_excerpt,occurred_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                events,
            )

        rows = await app.group_admin.history_24h("-1001", "owner")
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["user_id"]] = counts.get(row["user_id"], 0) + 1
        assert len(rows) == 1250
        assert set(counts) == {f"user-{index}" for index in range(25)}
        assert set(counts.values()) == {50}
        app.store.close()

    asyncio.run(scenario())


async def _scenario_legacy_admin_aliases(tmp_path) -> None:
    app = Harness(tmp_path)
    app.store.add_warning("-1001", "77")

    stats = Event("!stats")
    assert await app.dispatch(stats)
    assert stats.replies[-1][0] == "📊 تعداد کاربران متخلف: 1"

    reset = Event("!reset 77")
    assert await app.dispatch(reset)
    assert app.group_admin.warning_count("-1001", "77") == 0
    assert reset.replies[-1][0] == "✅ شمارنده کاربر صفر شد."

    help_message = Event("!help")
    assert await app.dispatch(help_message)
    assert "!stats" in help_message.replies[-1][0]

    lock = Event("!timelock 5")
    assert await app.dispatch(lock)
    assert app.platform.locked["-1001"] is True
    assert app.store.get_group_state("-1001", "timed_unlock")["actor_id"] == "owner"
    assert lock.replies[-1][0] == "⏰ قفل ساعتی فعال شد"

    photo = Event("!photo ./assets/group.png")
    assert await app.dispatch(photo)
    assert app.platform.photo["-1001"] == "./assets/group.png"
    assert photo.replies[-1][0] == "🖼️ عکس گروه تغییر کرد ✅"
    app.store.close()


def test_legacy_admin_aliases_are_routed(tmp_path) -> None:
    asyncio.run(_scenario_legacy_admin_aliases(tmp_path))


async def _scenario_owner_lifecycle_and_expiry_list(tmp_path) -> None:
    app = Harness(tmp_path)
    denied = Event("فعال سازی", actor_id="ordinary")
    assert await app.dispatch(denied)
    assert denied.replies[-1][0] == "❌ فقط مالک ربات اجازه این دستور را دارد"

    app.store.add_group_command_member("-1001", "registered_admin", "group-admin", display_name="Admin")
    toggle_off = Event("سرگرمی خاموش", actor_id="group-admin")
    assert await app.dispatch(toggle_off)
    assert app.store.get_group_state("-1001", "entertainment_enabled") is False
    assert toggle_off.replies[-1][0] == "🎮 بازی های روباه غیر فعال شد"
    denied_toggle = Event("سرگرمی فعال", actor_id="ordinary")
    assert await app.dispatch(denied_toggle)
    assert "اجازه تغییر وضعیت سرگرمی" in denied_toggle.replies[-1][0]
    toggle_on = Event("سرگرمی فعال", actor_id="group-admin")
    assert await app.dispatch(toggle_on)
    assert app.store.get_group_state("-1001", "entertainment_enabled") is True

    app.store.set_group_owner("-1001", "group-owner")
    group_owner_toggle = Event("سرگرمی خاموش", actor_id="group-owner")
    assert await app.dispatch(group_owner_toggle)
    assert app.store.get_group_state("-1001", "entertainment_enabled") is False
    global_owner_toggle = Event("سرگرمی فعال", actor_id="global-owner")
    assert await app.dispatch(global_owner_toggle)
    assert app.store.get_group_state("-1001", "entertainment_enabled") is True

    activate = Event("فعال", actor_id="global-owner")
    assert await app.dispatch(activate)
    assert app.store.get_group_state("-1001", "group_registry")["active"] is True
    assert "روباه در گروه" in activate.replies[-1][0]

    silent_repeat = Event("فعال", actor_id="global-owner")
    assert await app.dispatch(silent_repeat)
    assert silent_repeat.replies == []

    deactivate = Event("غیر فعال", actor_id="global-owner")
    assert await app.dispatch(deactivate)
    assert app.store.get_group_state("-1001", "group_registry")["active"] is False

    register = Event("ثبت گروه", actor_id="global-owner")
    assert await app.dispatch(register)
    assert app.store.get_group_state("-1001", "group_registry")["active"] is True

    target = SimpleNamespace(id=90, username="newowner", first_name="New", last_name="Owner")
    set_owner = Event("ثبت مالک", actor_id="global-owner", reply_user=target, reply_id=40)
    assert await app.dispatch(set_owner)
    assert app.store.get_group_owner("-1001") == "90"

    app.store.set_group_state(
        "-1001",
        "expiry",
        {"title": "Sample group", "expires_at": (datetime.now(UTC) + timedelta(days=2)).isoformat()},
    )
    expiry_list = Event("لیست انقضا", actor_id="global-owner")
    assert await app.dispatch(expiry_list)
    assert "Sample group" in expiry_list.replies[-1][0]
    assert "باقی مانده" in expiry_list.replies[-1][0]

    remove_owner = Event("لغو مالک", actor_id="global-owner")
    assert await app.dispatch(remove_owner)
    assert app.store.get_group_owner("-1001") is None
    app.store.close()


def test_legacy_admin_help_points_to_the_complete_routed_inventory(tmp_path) -> None:
    app = Harness(tmp_path)
    event = Event("!راهنما")
    assert asyncio.run(app.dispatch(event))
    text = event.replies[-1][0]
    assert "لیست ادمینی" in text
    assert "!timelock/!قفل_ساعتی" in text
    assert "!help راهنمای عمومی هستند" in text
    app.store.close()


def test_owner_lifecycle_and_expiry_listing_are_group_scoped(tmp_path) -> None:
    asyncio.run(_scenario_owner_lifecycle_and_expiry_list(tmp_path))


async def _scenario_auto_cleanup_schedule(tmp_path) -> None:
    app = Harness(tmp_path)
    start = Event("پاکسازی خودکار")
    assert await app.dispatch(start)
    assert "پاکسازی چه زمانی انجام شود؟" in start.replies[-1][0]

    day = Event("امروز")
    assert await app.dispatch(day)
    assert "ساعت انجام پاکسازی" in day.replies[-1][0]

    time = Event("23:59")
    assert await app.dispatch(time)
    assert "چه تعداد پیام حذف شود؟" in time.replies[-1][0]

    count = Event("2")
    assert await app.dispatch(count)
    scheduled = app.store.get_group_state("-1001", "auto_cleanup")
    assert scheduled["status"] == "scheduled"
    assert scheduled["count"] == 2
    assert scheduled["actor_id"] == "owner"
    assert "scheduled_at" in scheduled
    assert count.replies[-1][0].startswith("✅ پاکسازی خودکار تنظیم شد.")
    app.store.close()


def test_auto_cleanup_workflow_persists_a_one_shot_schedule(tmp_path) -> None:
    asyncio.run(_scenario_auto_cleanup_schedule(tmp_path))


def test_pending_workflows_migrate_and_keep_independent_slots(tmp_path) -> None:
    async def scenario() -> None:
        app = Harness(tmp_path)
        expires_at = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
        app.store.set_group_state(
            "-1001",
            "pending_workflow",
            {
                "kind": "warning_threshold",
                "actor_id": "owner",
                "values": {},
                "expires_at": expires_at,
            },
        )

        migrated = await app.group_admin.pending_workflow("-1001", "owner")
        assert migrated is not None and migrated["kind"] == "warning_threshold"
        assert app.store.get_group_state("-1001", "pending_workflow") is None
        assert await app.group_admin.pending_workflow(
            "-1001", "owner", kind="warning_threshold"
        ) is not None

        await app.group_admin.start_workflow(
            "-1001", "owner", "auto_cleanup", {"step": "day"}
        )
        assert (await app.group_admin.pending_workflow("-1001", "owner"))["kind"] == "auto_cleanup"
        assert await app.group_admin.pending_workflow(
            "-1001", "owner", kind="warning_threshold"
        ) is not None

        await app.group_admin.cancel_workflow(
            "-1001", actor_id="owner", kind="auto_cleanup"
        )
        assert (await app.group_admin.pending_workflow("-1001", "owner"))["kind"] == "warning_threshold"
        app.store.close()

    asyncio.run(scenario())

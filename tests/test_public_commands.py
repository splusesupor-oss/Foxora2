from __future__ import annotations

import asyncio
from collections import Counter
from datetime import date
from types import SimpleNamespace

from foxbot import economy_commands, public_commands
from foxbot.filters import WordFilter
from foxbot.game_commands import GAME_COMMANDS
from foxbot.game_commands import normalize as normalize_game
from foxbot.public_commands import _jalali_today, handle_public_command
from foxbot.storage import BotStore


class Event:
    def __init__(self, text: str, actor_id: str = "user") -> None:
        self.raw_text = text
        self.sender_id = actor_id
        self.sender = SimpleNamespace(id=actor_id, first_name="Test", last_name="User", username="testuser")
        self.replies = []


async def _dispatch(
    event: Event,
    store: BotStore,
    group_id: str = "g",
    word_filter: WordFilter | None = None,
) -> bool:
    async def reply(_event, text, *, formatting_entities=None, prefer_respond=False):
        event.replies.append((text, formatting_entities, prefer_respond))
        return SimpleNamespace(id=len(event.replies))

    return await handle_public_command(
        event,
        store,
        group_id=group_id,
        actor_id=event.sender_id,
        text=event.raw_text,
        reply=reply,
        word_filter=word_filter,
    )


def test_health_commands_are_global_owner_only() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        calls = []

        async def reply(_event, text, *, formatting_entities=None):
            calls.append(("reply", text))
            return SimpleNamespace(id=1)

        async def status_provider(_event):
            calls.append(("status", None))
            return "🩺 سلامت آزمایشی"

        owner = Event("وضعیت ربات", actor_id="owner")
        assert await handle_public_command(
            owner, store, group_id="g", actor_id="owner", text=owner.raw_text,
            reply=reply, global_owner_check=lambda user_id: user_id == "owner",
            status_provider=status_provider,
        )
        assert calls == [("status", None), ("reply", "🩺 سلامت آزمایشی")]

        calls.clear()
        outsider = Event("پینگ ربات", actor_id="outsider")
        assert await handle_public_command(
            outsider, store, group_id="g", actor_id="outsider", text=outsider.raw_text,
            reply=reply, global_owner_check=lambda user_id: user_id == "owner",
            status_provider=status_provider,
        )
        assert calls == []
        store.close()

    asyncio.run(scenario())


def test_button_smoke_command_uses_real_splusthon_inline_button() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        event = Event("تست دکمه")
        sent = {}

        async def reply(_event, text, *, formatting_entities=None, buttons=None):
            sent.update(text=text, formatting_entities=formatting_entities, buttons=buttons)
            return SimpleNamespace(id=1)

        assert await handle_public_command(
            event, store, group_id="g", actor_id="user", text=event.raw_text,
            reply=reply,
        )
        assert sent["text"] == "تست دکمه شیشه‌ای"
        assert sent["buttons"][0][0].text == "سلام"
        assert sent["buttons"][0][0].data == b"test_salam"
        store.close()

    asyncio.run(scenario())


def test_original_registration_is_user_scoped_and_ttl_bound() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        start = Event("ثبت اصل")
        assert await _dispatch(start, store)
        assert start.replies[-1][0] == "لقب یا اصل خودتو بنویس"
        capture = Event("روباهِ مهربان")
        assert await _dispatch(capture, store)
        assert capture.replies[-1][0] == "✅ اصل شما ثبت شد"
        show = Event("اصلم")
        assert await _dispatch(show, store)
        assert show.replies[-1][0] == "روباهِ مهربان"
        assert show.replies[-1][1]
        other_user = Event("اصلم", actor_id="other")
        assert await _dispatch(other_user, store)
        assert other_user.replies[-1][0] == "هنوز اصلی ثبت نکردی. برای ثبت بنویس: ثبت اصل"
        store.close()

    asyncio.run(scenario())


def test_group_memory_registration_show_remove_and_reserved_commands() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        add = Event("ثبت اسم علی")
        assert await _dispatch(add, store)
        assert add.replies[-1][0] == "✅ حافظه شما ثبت شد: علی"
        show = Event("حافظه من")
        assert await _dispatch(show, store)
        assert show.replies[-1][0] == "🧠 حافظه شما: علی"
        remove = Event("حذف اسم")
        assert await _dispatch(remove, store)
        assert remove.replies[-1][0] == "✅ حافظه شما حذف شد."
        invalid = Event("ثبت این نام دارای شماره 123 است")
        assert await _dispatch(invalid, store)
        assert "نام معتبر نیست" in invalid.replies[-1][0]
        reserved = Event("ثبت ادمین")
        assert await _dispatch(reserved, store) is False
        shortcut = Event("ثبت نیما", actor_id="other")
        assert await _dispatch(shortcut, store)
        assert store.get_group_state("g", "memory:other") == "نیما"
        store.close()

    asyncio.run(scenario())


def test_personal_group_stats_and_level_are_rendered_from_local_store() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        for _ in range(1001):
            store.record_group_message("g", "user", display_name="Test User", username="testuser")
        for _ in range(2):
            store.record_group_media_activity("g", "user", "gif")
        store.record_group_media_activity("g", "user", "video")
        store.add_warning("g", "user")

        personal = Event("آمارم")
        assert await _dispatch(personal, store)
        output = personal.replies[-1][0]
        assert "تعداد پیام [ 𝟭𝟬𝟬𝟭 ]" in output
        assert "تعداد گیف [ 𝟮 ]" in output
        assert "تعداد تخلف [ 𝟭 ]" in output

        level = Event("سطح گروه")
        assert await _dispatch(level, store)
        assert "سطح فعلی: 𝟭 از 𝟮𝟬" in level.replies[-1][0]
        assert "@testuser" in level.replies[-1][0]
        store.close()

    asyncio.run(scenario())


def test_points_profile_and_group_ranking_use_idempotent_store_awards() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        store.award_group_coins("g", "user", 3, reference="riddle:round-1", display_name="Test User")
        store.award_group_coins("g", "user", 3, reference="riddle:round-1", display_name="Test User")
        store.award_group_coins("g", "second", 1, "gold", reference="flag:round-1", display_name="Second")
        profile = Event("امتیاز من")
        assert await _dispatch(profile, store)
        assert "برنز: ← 𝟯" in profile.replies[-1][0]
        assert "ارزش کل: ← 𝟯" in profile.replies[-1][0]
        ranking = Event("رتبه ها")
        assert await _dispatch(ranking, store)
        assert "Second — 💎 𝟭𝟬𝟬" in ranking.replies[-1][0]
        assert "Test User — 💎 𝟯" in ranking.replies[-1][0]
        store.close()

    asyncio.run(scenario())


def test_reminder_workflow_validates_and_persists_persian_time_input() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        start = Event("یاد آوری")
        assert await _dispatch(start, store)
        assert start.replies[-1][0] == "📝 متن یادآوری و زمان موردنظر را ارسال کنید."

        invalid = Event("آب خوردن")
        assert await _dispatch(invalid, store)
        assert "۳۰ دقیقه آب بخور" in invalid.replies[-1][0]

        capture = Event("۲ دقیقه آب بخور")
        assert await _dispatch(capture, store)
        assert "✅ یادآوری ثبت شد." in capture.replies[-1][0]
        reminders = store.list_group_states_prefix("reminder:")
        assert len(reminders) == 1
        group_id, key, reminder = reminders[0]
        assert group_id == "g" and key.startswith("reminder:")
        assert reminder["text"] == "آب بخور"
        assert reminder["time_label"] == "2 دقیقه دیگر"
        assert reminder["user_id"] == "user"
        store.close()

    asyncio.run(scenario())


def test_local_user_content_and_personal_memory_reply_commands() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        for command, prefix in (("بیوگرافی", None), ("دانستنی", "🧠 دانستنی:"), ("جک", None)):
            event = Event(command)
            assert await _dispatch(event, store)
            output = event.replies[-1][0]
            if prefix:
                assert output.startswith(prefix)
            assert output

        name_report = Event("شخصیت علی")
        assert await _dispatch(name_report, store)
        assert "والا و بلندمرتبه" in name_report.replies[-1][0]
        assert "شخصیت واقعی را نمی‌توان فقط از روی نام" in name_report.replies[-1][0]

        register = Event("ثبت اسم نازنین")
        assert await _dispatch(register, store)
        greeting = Event("سلام")
        assert await _dispatch(greeting, store)
        assert "نازنین" in greeting.replies[-1][0]
        store.close()

    asyncio.run(scenario())


def test_translation_uses_real_service_boundary_without_blocking_event_loop(monkeypatch) -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        calls = []

        def fake_translation(text: str):
            calls.append(text)
            return "سلام دنیا", None

        monkeypatch.setattr(public_commands, "_translate_sync", fake_translation)
        start = Event("ترجمه")
        assert await _dispatch(start, store)
        assert start.replies[-1][0] == "🌐 متن انگلیسی خود را ارسال کنید."
        capture = Event("Hello world")
        assert await _dispatch(capture, store)
        assert calls == ["Hello world"]
        output = capture.replies[-1][0]
        assert "🇬🇧 متن:" in output and "Hello world" in output
        assert "🇮🇷 ترجمه:" in output and "سلام دنیا" in output
        assert capture.replies[-1][1]
        store.close()

    asyncio.run(scenario())


def test_web_search_preserves_cooldown_filter_and_nonblocking_boundary(monkeypatch) -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        calls = []

        def fake_search(query: str) -> str:
            calls.append(query)
            return "🔎 نتایج جستجو:\\n\\n1- نتیجه\\n🔗 https://example.test"

        monkeypatch.setattr(public_commands, "_search_web_sync", fake_search)
        first = Event("جستجو گربه", actor_id="one")
        assert await _dispatch(first, store)
        assert calls == ["گربه"]
        assert "https://example.test" in first.replies[-1][0]

        limited = Event("جستجو پرنده", actor_id="one")
        assert await _dispatch(limited, store)
        assert "صبر کنید" in limited.replies[-1][0]
        assert calls == ["گربه"]

        blocked = Event("جستجو xxx", actor_id="two")
        assert await _dispatch(blocked, store)
        assert blocked.replies[-1][0] == "🚫 جستجو این مطلب غیرمجاز است."
        assert "two" not in store.get_group_state("__global__", "search_cooldown", {})

        allowed_other = Event("جستجو تاریخ", actor_id="two")
        assert await _dispatch(allowed_other, store)
        assert calls == ["گربه", "تاریخ"]
        store.close()

    asyncio.run(scenario())


def test_factual_search_requires_group_enablement_user_access_and_bot_reply(monkeypatch) -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        store.set_group_state("g", "google_ai_enabled", True)
        store.add_group_command_member(
            "g", "ai_allowed", "user", display_name="Test User", username="testuser"
        )
        calls = []
        replies = []
        lookups = []

        def fake_search(topic: str) -> str:
            calls.append(topic)
            return "🔎 خلاصهٔ منبع‌دار"

        async def reply(_event, text, *, formatting_entities=None, buttons=None):
            replies.append(text)
            return SimpleNamespace(id=90)

        async def load_bot_reply(group_id, message_id):
            lookups.append((group_id, message_id))
            return SimpleNamespace(
                id=message_id,
                chat_id=group_id,
                sender_id="bot",
                message="سلام، چطور می‌توانم کمک کنم؟",
            )

        monkeypatch.setattr(public_commands, "search_factual", fake_search)
        event = Event("سیاره مریخ چیست؟")
        event.reply_to = SimpleNamespace(reply_to_msg_id=55)
        handled = await handle_public_command(
            event,
            store,
            group_id="g",
            actor_id="user",
            text=event.raw_text,
            reply=reply,
            bot_user_id="bot",
            reply_message_loader=load_bot_reply,
        )
        assert handled is True
        assert calls == ["سیاره مریخ چیست"]
        assert lookups == [("g", 55)]
        assert replies == ["🔎 خلاصهٔ منبع‌دار"]

        calls.clear()
        denied = Event("سیاره زمین چیست؟", actor_id="other")
        denied.reply_to = SimpleNamespace(reply_to_msg_id=56)
        assert await handle_public_command(
            denied,
            store,
            group_id="g",
            actor_id="other",
            text=denied.raw_text,
            reply=reply,
            bot_user_id="bot",
            reply_message_loader=load_bot_reply,
        ) is False
        assert calls == []
        assert lookups == [("g", 55)]

        async def load_user_reply(group_id, message_id):
            return SimpleNamespace(id=message_id, chat_id=group_id, sender_id="other")

        not_bot = Event("سیاره زهره چیست؟")
        not_bot.reply_to = SimpleNamespace(reply_to_msg_id=57)
        assert await handle_public_command(
            not_bot,
            store,
            group_id="g",
            actor_id="user",
            text=not_bot.raw_text,
            reply=reply,
            bot_user_id="bot",
            reply_message_loader=load_user_reply,
        ) is False
        assert calls == []
        store.close()

    asyncio.run(scenario())


def test_economy_history_marks_media_and_game_penalties_as_debits() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        store.award_group_coins("g", "user", 30, reference="history-seed")
        store.spend_group_coins(
            "g", "user", 10, "bronze", reference="history-photo",
            note="دانلود عکس (1 تصویر)",
        )
        store.spend_group_coins(
            "g", "user", 5, "bronze", reference="history-mine",
            note="مین یاب",
        )
        store.claim_group_daily_coins(
            "g", "user", 25, now_epoch=1_700_000_000,
            rewards={"bronze": 25, "silver": 3, "gold": 1},
        )
        assert await _dispatch(Event("موجودی"), store)
        history = Event("۶")
        assert await _dispatch(history, store)
        text = history.replies[-1][0]
        assert text.count("➖ خرج: برنز −") == 2
        assert "برنز −۵ — مین یاب" in text
        assert "برنز −۱۰ — دانلود عکس (1 تصویر)" in text
        assert "برنز +۵" not in text and "برنز +۱۰" not in text
        assert text.count("📅 جایزه روزانه:") == 1
        assert "برنز +۲۵ ، نقره +۳ ، طلا +۱" in text
        store.close()

    asyncio.run(scenario())


def test_zero_daily_claim_history_shows_an_event_without_a_coin_delta() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        store.claim_group_daily_coins(
            "g", "user", 0, now_epoch=1_700_000_000,
            rewards={"bronze": 0, "silver": 0, "gold": 0},
        )
        assert await _dispatch(Event("موجودی"), store)
        history = Event("۶")
        assert await _dispatch(history, store)
        text = history.replies[-1][0]
        assert "📅 جایزه روزانه: بدون تغییر در موجودی" in text
        assert "برنز +۰" not in text
        store.close()

    asyncio.run(scenario())


def test_wallet_convenience_aliases_open_the_wallet_menu() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        for command in ("کیف پول", "انتقال سکه"):
            event = Event(command)
            assert await _dispatch(event, store)
            assert "💰 کیف پول شما" in event.replies[-1][0]
        assert "گزینهٔ ۳، ۴ یا ۵" in event.replies[-1][0]
        store.close()

    asyncio.run(scenario())


def test_wallet_transfers_and_conversions_use_group_scoped_balances() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        store.award_group_coins("g", "user", 150, reference="wallet-seed")
        store.record_group_message("g", "target", username="targetuser", display_name="Target User")
        store.award_group_coins("g", "user", 50, "silver", reference="silver-seed")

        wallet = Event("موجودی")
        assert await _dispatch(wallet, store)
        assert "🥉 برنز: ۱۵۰" in wallet.replies[-1][0]
        convert = Event("۱")
        assert await _dispatch(convert, store)
        balance = store.get_economy_profile("g", "user")
        assert balance["bronze"] == 50 and balance["silver"] == 62
        assert "✅ ۱۰۰ برنز به ۱۲ نقره تبدیل شد." in convert.replies[-1][0]
        assert "🥉 برنز: ۵۰" in convert.replies[-1][0]
        assert "🥈 نقره: ۶۲" in convert.replies[-1][0]
        assert "💰 کیف پول شما" not in convert.replies[-1][0]

        open_wallet = Event("موجودی")
        assert await _dispatch(open_wallet, store)
        select_silver = Event("۴")
        assert await _dispatch(select_silver, store)
        target_name = Event("@target\u200cuser")
        assert await _dispatch(target_name, store)
        amount = Event("۱۰")
        assert await _dispatch(amount, store)
        assert store.get_economy_profile("g", "user")["silver"] == 52
        assert store.get_economy_profile("g", "target")["silver"] == 10
        assert store.get_economy_profile("g", "target")["display_name"] == "@targetuser"
        assert "✅ ۱۰ نقره منتقل شد." in amount.replies[-1][0]
        assert "موجودی شما:" in amount.replies[-1][0]
        assert "💰 کیف پول شما" not in amount.replies[-1][0]
        assert "💎 ارزش کل:" in amount.replies[-1][0]

        # The source closes the balance session after transfer.
        close = Event("۰")
        assert not await _dispatch(close, store)

        assert await _dispatch(Event("موجودی"), store)
        history = Event("۶")
        assert await _dispatch(history, store)
        assert history.replies[-1][0].count("🔄 تبدیل:") == 1
        assert "برنز −۱۰۰" in history.replies[-1][0]
        assert "نقره +۱۲" in history.replies[-1][0]
        assert history.replies[-1][0].index("برنز −۱۰۰") < history.replies[-1][0].index("نقره +۱۲")
        store.close()

    asyncio.run(scenario())


def test_transfer_target_resolution_is_group_scoped_and_rejects_self_or_numeric_ids() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        store.record_group_message(
            "other", "remote", username="remoteuser", display_name="Remote User"
        )
        store.record_group_message(
            "g", "user", username="selfuser", display_name="Test User"
        )
        store.record_group_message("g", "former", username="reuseduser")
        store.record_group_message("g", "current", username="reuseduser")
        assert store.find_group_user_by_username("g", "reuseduser")["user_id"] == "current"
        assert await _dispatch(Event("موجودی"), store)
        assert await _dispatch(Event("۳"), store)

        remote_target = Event("@remoteuser")
        assert await _dispatch(remote_target, store)
        assert "در این گروه پیدا نشد" in remote_target.replies[-1][0]

        numeric_target = Event("۱۲۳۴۵")
        assert await _dispatch(numeric_target, store)
        assert "آیدی عددی پذیرفته نمی‌شود" in numeric_target.replies[-1][0]

        self_target = Event("@selfuser")
        assert await _dispatch(self_target, store)
        assert "انتقال به خودتان ممکن نیست" in self_target.replies[-1][0]
        assert store.get_economy_profile("g", "user")["bronze"] == 0
        assert store.get_economy_profile("other", "remote")["bronze"] == 0
        store.close()

    asyncio.run(scenario())


def test_fixed_shop_catalog_matches_reference_aggregate() -> None:
    from foxbot.economy_commands import _items

    items = _items()
    assert len(items) == 32
    assert Counter(item["kind"] for item in items) == {
        "badge": 10,
        "star": 7,
        "title": 15,
    }
    assert Counter(item["coin_type"] for item in items) == {
        "bronze": 16,
        "silver": 16,
    }
    assert Counter((item["kind"], len(item.get("aliases", ()))) for item in items) == {
        ("badge", 3): 10,
        ("star", 4): 7,
        ("title", 3): 15,
    }
    assert Counter(item["price"] for item in items) == {
        100: 1,
        120: 1,
        150: 1,
        180: 1,
        200: 17,
        300: 2,
        400: 1,
        500: 1,
        700: 1,
        800: 1,
        900: 1,
        1000: 1,
        1200: 1,
        1400: 1,
        1800: 1,
    }
    expected_money = {
        "badge_fox": (100, "silver"),
        "badge_lion": (120, "silver"),
        "badge_heart": (300, "bronze"),
        "badge_king": (300, "silver"),
        "badge_bolt": (150, "silver"),
        "badge_skull": (200, "silver"),
        "badge_wolf": (180, "silver"),
        "badge_dragon": (500, "silver"),
        "badge_legend": (700, "silver"),
        "badge_galaxy": (900, "silver"),
        "star_1": (200, "silver"),
        "star_2": (400, "silver"),
        "star_3": (800, "silver"),
        "star_4": (1000, "silver"),
        "star_5": (1200, "silver"),
        "star_6": (1400, "silver"),
        "star_7": (1800, "silver"),
    }
    expected_money.update(
        {
            item["id"]: (200, "bronze")
            for item in items
            if item["kind"] == "title"
        }
    )
    assert {
        item["id"]: (item["price"], item["coin_type"]) for item in items
    } == expected_money


def test_economy_session_expiry_matches_wallet_and_profile_windows(monkeypatch) -> None:
    store = BotStore(":memory:")
    monkeypatch.setattr(economy_commands.time, "time", lambda: 1000.0)
    session_key = economy_commands._session_key("user")

    store.set_group_state(
        "g", session_key, {"step": "wallet_menu", "updated_at": 820.0}
    )
    assert economy_commands._get_session(store, "g", "user") is not None
    store.set_group_state(
        "g", session_key, {"step": "wallet_menu", "updated_at": 819.0}
    )
    assert economy_commands._get_session(store, "g", "user") is None

    store.set_group_state(
        "g", session_key, {"step": "shop_buy", "updated_at": 820.0}
    )
    assert economy_commands._get_session(store, "g", "user") is not None
    store.set_group_state(
        "g", session_key, {"step": "shop_buy", "updated_at": 819.0}
    )
    assert economy_commands._get_session(store, "g", "user") is None

    store.set_group_state(
        "g", session_key, {"step": "profile_menu", "updated_at": 700.0}
    )
    assert economy_commands._get_session(store, "g", "user") is not None
    store.set_group_state(
        "g", session_key, {"step": "profile_menu", "updated_at": 699.0}
    )
    assert economy_commands._get_session(store, "g", "user") is None

    for step in ("shop_buy", "shop_confirm"):
        store.set_group_state(
            "g", session_key,
            {"step": step, "return_to": "profile_buy", "updated_at": 701.0},
        )
        assert economy_commands._get_session(store, "g", "user") is not None
        store.set_group_state(
            "g", session_key,
            {"step": step, "return_to": "profile_buy", "updated_at": 699.0},
        )
        assert economy_commands._get_session(store, "g", "user") is None
    store.close()


def test_shop_confirmation_profile_registration_and_profile_deletion() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        store.award_group_coins("g", "user", 150, "silver", reference="shop-seed")
        open_shop = Event("فروشگاه")
        assert await _dispatch(open_shop, store)
        assert "آیتم‌های موجود" in open_shop.replies[-1][0]
        enter_shop = Event("۱")
        assert await _dispatch(enter_shop, store)
        assert len(enter_shop.replies) == 1
        assert "🛍 خرید آیتم" in enter_shop.replies[-1][0]
        assert "نشان‌ها" not in enter_shop.replies[-1][0]
        choose_item = Event("۱")
        assert await _dispatch(choose_item, store)
        assert "آیا از خرید این آیتم مطمئن هستید؟" in choose_item.replies[-1][0]
        confirm = Event("بله")
        assert await _dispatch(confirm, store)
        assert store.get_economy_profile("g", "user")["silver"] == 50
        assert store.get_group_state("g", "economy_profile:user")["badges"] == ["badge_fox"]
        assert "نشان 🦊 به پروفایل شما اضافه شد." in confirm.replies[-1][0]

        register = Event("ثبت پرفایل")
        assert await _dispatch(register, store)
        registration_steps = []
        for value in ("Test User", "Nuremberg", "۲۹", "۰"):
            step = Event(value)
            registration_steps.append(step)
            assert await _dispatch(step, store)
        assert store.get_group_state("g", "economy_profile:user")["registered"] is True
        assert len(registration_steps[-1].replies) == 2
        assert registration_steps[-1].replies[0][0] == "✅ پروفایل شما ثبت شد."
        assert "╔════════════════════╗" in registration_steps[-1].replies[-1][0]

        show = Event("پرفایلم")
        assert await _dispatch(show, store)
        assert "نشان‌ها:\n🦊" in show.replies[-1][0]
        assert "🦊 @testuser 🦊" in show.replies[-1][0]
        assert "🏷 لقب:" not in show.replies[-1][0]
        assert "شهر: Nuremberg" in show.replies[-1][0]
        assert show.replies[-1][1]

        remove = Event("حذف پرفایل")
        assert await _dispatch(remove, store)
        profile = store.get_group_state("g", "economy_profile:user")
        assert profile["registered"] is False and profile["badges"] == ["badge_fox"]
        assert store.get_economy_profile("g", "user")["silver"] == 50
        store.close()

    asyncio.run(scenario())


def test_economy_entry_points_refresh_display_name_without_wallet_side_effects() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        store.award_group_coins(
            "g", "user", 41, reference="display-name-seed", display_name="Old Name",
        )
        before = store.get_economy_profile("g", "user")
        history_before = store.economy_transaction_history("g", "user")

        wallet = Event("موجودی")
        wallet.sender.first_name = "Wallet"
        wallet.sender.last_name = "Name"
        assert await _dispatch(wallet, store)
        after_wallet = store.get_economy_profile("g", "user")
        assert after_wallet["display_name"] == "Wallet Name"
        assert after_wallet["updated_at"] == before["updated_at"]
        assert after_wallet["bronze"] == before["bronze"]
        assert store.economy_transaction_history("g", "user") == history_before

        shop = Event("فروشگاه")
        shop.sender.first_name = "Shop"
        shop.sender.last_name = "Name"
        assert await _dispatch(shop, store)
        after_shop = store.get_economy_profile("g", "user")
        assert after_shop["display_name"] == "Shop Name"
        assert after_shop["updated_at"] == before["updated_at"]
        assert after_shop["bronze"] == before["bronze"]
        assert store.economy_transaction_history("g", "user") == history_before
        store.close()

    asyncio.run(scenario())


def test_profile_registration_validation_keeps_the_flow_at_invalid_steps() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        actor = "profile-validator"
        word_filter = WordFilter(WordFilter.read_file("config/banned_words.txt"))
        assert word_filter.global_terms
        assert await _dispatch(
            Event("ثبت پروفایل", actor_id=actor), store, word_filter=word_filter,
        )
        session_key = f"economy_session:{actor}"

        blocked_name = Event(word_filter.global_terms[0], actor_id=actor)
        assert await _dispatch(blocked_name, store, word_filter=word_filter)
        blocked_notice = blocked_name.replies[-1][0]
        assert "مجاز نیست" in blocked_notice
        assert not bool(word_filter.match_global(blocked_notice))
        assert store.get_group_state("g", session_key)["step"] == "profile_name"

        blank_name = Event("   ", actor_id=actor)
        assert not await _dispatch(blank_name, store, word_filter=word_filter)
        assert store.get_group_state("g", session_key)["step"] == "profile_name"
        invalid_name = Event("N" * 33, actor_id=actor)
        assert await _dispatch(invalid_name, store, word_filter=word_filter)
        assert "❌" in invalid_name.replies[-1][0]
        assert store.get_group_state("g", session_key)["step"] == "profile_name"
        assert await _dispatch(
            Event("Valid Name", actor_id=actor), store, word_filter=word_filter,
        )
        assert store.get_group_state("g", session_key)["step"] == "profile_city"

        blank_city = Event("   ", actor_id=actor)
        assert not await _dispatch(blank_city, store, word_filter=word_filter)
        assert store.get_group_state("g", session_key)["step"] == "profile_city"
        invalid_city = Event("C" * 33, actor_id=actor)
        assert await _dispatch(invalid_city, store, word_filter=word_filter)
        assert "❌" in invalid_city.replies[-1][0]
        assert store.get_group_state("g", session_key)["step"] == "profile_city"
        assert await _dispatch(
            Event("Nuremberg", actor_id=actor), store, word_filter=word_filter,
        )
        assert store.get_group_state("g", session_key)["step"] == "profile_age"

        for invalid_age in ("۴", "۱۲۱", "۲۰ سال"):
            event = Event(invalid_age, actor_id=actor)
            assert await _dispatch(event, store, word_filter=word_filter)
            assert "❌" in event.replies[-1][0]
            assert store.get_group_state("g", session_key)["step"] == "profile_age"
        # The reference also accepts mathematical bold digits for profile age.
        assert await _dispatch(
            Event("𝟭𝟮𝟬", actor_id=actor), store, word_filter=word_filter,
        )
        assert store.get_group_state("g", session_key)["step"] == "profile_nickname"

        too_long_nickname = Event("N" * 33, actor_id=actor)
        assert await _dispatch(too_long_nickname, store, word_filter=word_filter)
        assert "❌" in too_long_nickname.replies[-1][0]
        assert store.get_group_state("g", session_key)["step"] == "profile_nickname"
        blocked_nickname = Event(word_filter.global_terms[0], actor_id=actor)
        assert await _dispatch(
            blocked_nickname, store, word_filter=word_filter,
        )
        blocked_nickname_notice = blocked_nickname.replies[-1][0]
        assert "مجاز نیست" in blocked_nickname_notice
        assert not bool(word_filter.match_global(blocked_nickname_notice))
        assert store.get_group_state("g", session_key)["step"] == "profile_nickname"
        locked_nickname = Event("fox king", actor_id=actor)
        assert await _dispatch(locked_nickname, store, word_filter=word_filter)
        assert "آیتم‌های فروشگاه" in locked_nickname.replies[-1][0]
        assert store.get_group_state("g", session_key)["step"] == "profile_nickname"

        finish = Event("۰", actor_id=actor)
        assert await _dispatch(finish, store, word_filter=word_filter)
        profile = store.get_group_state("g", f"economy_profile:{actor}")
        assert profile["registered"] is True
        assert (profile["name"], profile["city"], profile["age"], profile["nickname"]) == (
            "Valid Name", "Nuremberg", 120, None,
        )
        assert store.get_group_state("other", f"economy_profile:{actor}") is None
        assert store.get_economy_profile("g", actor)["display_name"] == "Test User"

        word_filter.set_group_terms("g", ("group-only",))
        assert await _dispatch(
            Event("پرفایلم", actor_id=actor), store, word_filter=word_filter,
        )
        assert await _dispatch(Event("۲", actor_id=actor), store, word_filter=word_filter)
        assert await _dispatch(Event("۱", actor_id=actor), store, word_filter=word_filter)
        group_only_name = Event("group-only", actor_id=actor)
        assert await _dispatch(group_only_name, store, word_filter=word_filter)
        assert store.get_group_state("g", f"economy_profile:{actor}")["name"] == "group-only"
        store.close()

    asyncio.run(scenario())


def test_unknown_wallet_message_does_not_extend_source_session_timeout(monkeypatch) -> None:
    from types import SimpleNamespace

    from foxbot import economy_commands

    clock = [1_000.0]
    monkeypatch.setattr(
        economy_commands, "time", SimpleNamespace(time=lambda: clock[0])
    )

    async def scenario() -> None:
        store = BotStore(":memory:")
        assert await _dispatch(Event("موجودی"), store)
        key = "economy_session:user"
        initial = store.get_group_state("g", key)
        assert initial["updated_at"] == clock[0]

        clock[0] += 170
        assert not await _dispatch(Event("سلام"), store)
        assert store.get_group_state("g", key)["updated_at"] == initial["updated_at"]

        clock[0] += 11
        assert not await _dispatch(Event("۷"), store)
        assert store.get_group_state("g", key) is None
        assert store.get_economy_profile("g", "user")["bronze"] == 0
        store.close()

    asyncio.run(scenario())


def test_profile_item_list_prompt_are_separate_and_cancel_returns_to_profile_menu() -> None:
    async def scenario() -> None:
        from foxbot import shop_catalog

        store = BotStore(":memory:")
        shop_catalog.add_item(
            store, "profile-only-special", "آیتم ویژه", 1, "bronze", stock=2,
        )
        assert await _dispatch(Event("ثبت پرفایل", actor_id="profilebuyer"), store)
        for value in ("Profile Buyer", "Nuremberg", "۲۸", "ندارم"):
            assert await _dispatch(Event(value, actor_id="profilebuyer"), store)
        profile = store.get_group_state("g", "economy_profile:profilebuyer")
        assert profile["nickname"] is None
        assert store.get_economy_profile("g", "profilebuyer")["display_name"] == "Test User"

        assert await _dispatch(Event("پرفایلم", actor_id="profilebuyer"), store)
        open_buy = Event("۱", actor_id="profilebuyer")
        assert await _dispatch(open_buy, store)
        assert len(open_buy.replies) == 2
        assert "نشان‌ها" in open_buy.replies[0][0]
        assert "آیتم ویژه" not in open_buy.replies[0][0]
        assert "برای خرید، شمارهٔ آیتم را بفرستید." in open_buy.replies[0][0]
        assert "🛍 خرید آیتم" in open_buy.replies[1][0]

        cancel = Event("۰", actor_id="profilebuyer")
        assert await _dispatch(cancel, store)
        assert cancel.replies[-1][0] == "لغو شد."
        assert store.get_group_state("g", "economy_session:profilebuyer")["step"] == "profile_menu"

        edit = Event("۲", actor_id="profilebuyer")
        assert await _dispatch(edit, store)
        assert "ویرایش اطلاعات" in edit.replies[-1][0]
        choose_name = Event("۱", actor_id="profilebuyer")
        assert await _dispatch(choose_name, store)
        save_name = Event("Updated Buyer", actor_id="profilebuyer")
        assert await _dispatch(save_name, store)
        assert len(save_name.replies) == 2
        assert save_name.replies[0][0] == "✅ ذخیره شد."
        assert "Updated Buyer" in save_name.replies[1][0]
        store.close()

    asyncio.run(scenario())


def test_alternate_profile_spellings_are_preserved() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        register = Event("ثبت پروفایل", actor_id="alternate")
        assert await _dispatch(register, store)
        assert register.replies[-1][0] == "👤 ثبت پروفایل\n\nاسم خود را بفرستید:"
        for value in ("Alternate User", "Nuremberg", "۳۰", "۰"):
            assert await _dispatch(Event(value, actor_id="alternate"), store)

        show = Event("پروفایلم", actor_id="alternate")
        assert await _dispatch(show, store)
        assert "Alternate User" in show.replies[-1][0]

        delete = Event("حذف پروفایل", actor_id="alternate")
        assert await _dispatch(delete, store)
        profile = store.get_group_state("g", "economy_profile:alternate")
        assert profile["registered"] is False
        store.close()

    asyncio.run(scenario())


def test_game_help_covers_every_routed_game_alias() -> None:
    assert "خون\u200cآشام" in GAME_COMMANDS
    assert public_commands._normalize("خون\u200cآشام") == "خون آشام"
    help_text = normalize_game(public_commands._GAME_LIST)
    missing = [
        normalize_game(command)
        for command in GAME_COMMANDS
        if normalize_game(command) not in help_text
    ]
    assert not missing
    assert "شرکت" in help_text
    assert "شروع دوباره حدس ایموجی" in help_text
    assert "ریست حدس ایموجی" in help_text


def test_help_lists_dare_truth_and_identity_commands() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        for command, expected in (
            ("راهنما", "لیست کاربران"),
            ("لیست کاربران", "بیوگرافی"),
            ("لیست کاربران", "/game | /site"),
            ("لیست کاربران", "تست دکمه"),
            ("لیست کاربران", "لیست بازی ها"),
            ("لیست ادمینی", "ثبت ادمین"),
            ("لیست ادمینی", "!addword/!addban"),
            ("لیست ادمینی", "!photo/!عکس"),
            ("لیست ادمینی", "رفع سکوت همه"),
            ("لیست ادمینی", "جستجو عضو/بیو"),
            ("لیست ادمینی", "(ریپلای) اخطار"),
            ("لیست ادمینی", "سرگرمی فعال"),
            ("لیست ادمینی", "مهلت گروه"),
            ("لیست ادمینی", "فهرست/لیست فیلتر نام/اسم"),
            ("لیست ادمینی", "ثبت مالک (ریپلای)"),
            ("لیست ادمینی", "پینگ ربات"),
            ("لیست ادمینی", "اطلاع رسانی/اطلاعرسانی/اعلان"),
            ("لیست بازی‌ها", "جرعت"),
            ("جرئت", "🎯 جرعت:"),
            ("حقیقت بگو", "🧠 حقیقت:"),
            ("روباه", "سلام، من روباه هستم"),
        ):
            event = Event(command)
            assert await _dispatch(event, store)
            assert expected in event.replies[-1][0]
        store.close()

    asyncio.run(scenario())


def test_game_site_requires_group_activation_and_spends_twenty_bronze() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        inactive = Event("سایت بازی")
        assert await _dispatch(inactive, store)
        assert "ابتدا روباه باید" in inactive.replies[-1][0]

        store.award_group_coins("g", "user", 25, reference="game-reward", display_name="Test User")
        store.set_group_state("g", "group_registry", {"active": True, "title": "Test group"})
        site = Event("سایت بازی")
        assert await _dispatch(site, store)
        assert "https://fox-game.aifox-chat.workers.dev/" in site.replies[-1][0]
        assert "Test group" in site.replies[-1][0]
        assert store.get_economy_profile("g", "user")["bronze"] == 5

        insufficient = Event("سایت", actor_id="new-user")
        assert await _dispatch(insufficient, store)
        assert "حداقل ۲۰ برنز" in insufficient.replies[-1][0]
        store.close()

    asyncio.run(scenario())


def test_font_command_returns_all_variants_and_keeps_persian_text() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        event = Event("فونت Hello فارسی 123!")
        assert await _dispatch(event, store)
        output = event.replies[-1][0]
        assert output.startswith("✨ فونت‌های ساخته شده:")
        assert output.count("فارسی") == 14
        assert "𝐇𝐞𝐥𝐥𝐨 فارسی 123!" in output
        assert "Ｈｅｌｌｏ فارسی １２３！" in output
        store.close()

    asyncio.run(scenario())


def test_search_result_parser_unwraps_duckduckgo_redirect(monkeypatch) -> None:
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _limit):
            return (
                b'<html><a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.test%2Fpage">'
                b'A &amp; B</a></html>'
            )

    monkeypatch.setattr(public_commands, "urlopen", lambda *_args, **_kwargs: Response())
    result = public_commands._search_web_search_locked("query")
    assert "A & B" in result
    assert "https://example.test/page" in result
    assert "/l/?uddg=" not in result


def test_jalali_date_conversion_known_new_year() -> None:
    assert _jalali_today(date(2024, 3, 20)) == "1403/01/01"


def test_profile_edit_unknown_selection_falls_through_to_later_routes() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        assert await _dispatch(Event("ثبت پرفایل", actor_id="editunknown"), store)
        for value in ("Edit Test", "Nuremberg", "۲۵", "۰"):
            assert await _dispatch(Event(value, actor_id="editunknown"), store)
        assert await _dispatch(Event("پرفایلم", actor_id="editunknown"), store)
        assert await _dispatch(Event("۲", actor_id="editunknown"), store)

        # The reference handler returns False for a non-selection so later
        # public/admin handlers can still see the message.
        unrelated = Event("unhandled economy input", actor_id="editunknown")
        assert not await _dispatch(unrelated, store)
        assert unrelated.replies == []
        assert store.get_group_state(
            "g", "economy_session:editunknown"
        )["step"] == "profile_edit_choose"
        store.close()

    asyncio.run(scenario())


def test_wallet_menu_silver_conversion_and_gold_transfer_options() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        store.award_group_coins(
            "g", "user", 140, "silver", reference="wallet-silver-convert-seed"
        )
        store.award_group_coins(
            "g", "user", 5, "bronze", reference="wallet-bronze-transfer-seed"
        )
        store.record_group_message(
            "g", "target", username="targetuser", display_name="Target User"
        )

        assert await _dispatch(Event("موجودی"), store)
        convert = Event("۲")
        assert await _dispatch(convert, store)
        profile = store.get_economy_profile("g", "user")
        assert (profile["silver"], profile["gold"]) == (70, 10)
        assert "۷۰ نقره به ۱۰ طلا تبدیل شد" in convert.replies[-1][0]

        assert await _dispatch(Event("موجودی"), store)
        select_gold = Event("۵")
        assert await _dispatch(select_gold, store)
        assert "انتقال طلا" in select_gold.replies[-1][0]
        assert await _dispatch(Event("@targetuser"), store)
        amount = Event("۱")
        assert await _dispatch(amount, store)
        assert store.get_economy_profile("g", "user")["gold"] == 9
        assert store.get_economy_profile("g", "target")["gold"] == 1

        assert await _dispatch(Event("موجودی"), store)
        select_bronze = Event("۳")
        assert await _dispatch(select_bronze, store)
        assert "انتقال برنز" in select_bronze.replies[-1][0]
        assert await _dispatch(Event("@targetuser"), store)
        bronze_amount = Event("۱")
        assert await _dispatch(bronze_amount, store)
        assert store.get_economy_profile("g", "user")["bronze"] == 4
        assert store.get_economy_profile("g", "target")["bronze"] == 1
        store.close()

    asyncio.run(scenario())


def test_shop_confirmation_reports_insufficient_balance_after_atomic_recheck() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        store.award_group_coins(
            "g", "buyer", 100, "silver", reference="shop-race-seed"
        )
        assert await _dispatch(Event("فروشگاه", actor_id="buyer"), store)
        assert await _dispatch(Event("۱", actor_id="buyer"), store)
        assert await _dispatch(Event("۱", actor_id="buyer"), store)
        confirmation = store.spend_group_coins(
            "g", "buyer", 100, "silver", reference="shop-race-spend"
        )
        assert confirmation[1] == "spent"

        result = Event("تایید", actor_id="buyer")
        assert await _dispatch(result, store)
        assert result.replies[-1][0] == "❌ موجودی نقره کافی نیست: ۰ < ۱۰۰"
        profile = store.get_group_state("g", "economy_profile:buyer", {})
        assert profile.get("badges", []) == []
        assert store.get_economy_profile("g", "buyer")["silver"] == 0
        store.close()

    asyncio.run(scenario())

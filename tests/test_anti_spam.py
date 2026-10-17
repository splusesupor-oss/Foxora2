from __future__ import annotations

import asyncio

from foxbot.anti_spam import SpamGuard, is_long_repetitive_payload
from foxbot.message_actions import FairDeleteQueue
from foxbot.soroush_events import _is_likely_command


def _inspect(
    guard: SpamGuard,
    message_id: int,
    text: str = "",
    *,
    group: str = "g",
    user: str = "u",
    media_kind: str | None = None,
    forwarded: bool = False,
    grouped_id: int | None = None,
    is_command: bool = False,
    now: float | None = None,
):
    return guard.inspect(
        group,
        user,
        message_id,
        text,
        media_kind=media_kind,
        forwarded=forwarded,
        grouped_id=grouped_id,
        is_command=is_command,
        now=now,
    )


def test_repeat_is_third_message_then_incident_captures_followups() -> None:
    guard = SpamGuard()
    for message_id in range(1, 3):
        assert _inspect(
            guard, message_id, "این یک پیام معمولی برای آزمایش است", now=message_id
        ) is None

    decision = _inspect(
        guard, 3, "این یک پیام معمولی برای آزمایش است", now=3
    )
    assert decision is not None
    assert decision.kind == "repeat"
    assert decision.policy == "ban"
    assert decision.action == "ban"
    assert decision.message_ids == ("1", "2", "3")

    followup = _inspect(guard, 4, "یک پیام دیگر", now=4)
    assert followup is not None and followup.kind == "incident"
    assert followup.message_ids == ("4",)


def test_long_repetitive_payload_uses_bounded_shape_check() -> None:
    assert is_long_repetitive_payload("same " * 80)
    assert not is_long_repetitive_payload("same " * 50)
    diverse = " ".join(f"word{index}" for index in range(20)) * 4
    assert not is_long_repetitive_payload(diverse)
    assert not is_long_repetitive_payload("same " * 2000)


def test_generic_repeat_fingerprint_collapses_stretched_letters() -> None:
    guard = SpamGuard()
    variants = ("heyyyy there", "heyyyyy there", "heyyy there", "heyyyy there")
    decision = None
    for message_id, text in enumerate(variants[:3], start=1):
        decision = _inspect(guard, message_id, text, now=float(message_id))
        assert decision is None
    decision = _inspect(guard, 4, variants[3], now=4.0)
    assert decision is not None and decision.kind == "repeat"
    assert decision.policy == "ban"

    normalized_repeat = SpamGuard()
    variants = ("heyy there", "hey there", "heyy there", "hey there")
    for message_id, text in enumerate(variants, start=1):
        assert _inspect(normalized_repeat, message_id, text, now=message_id) is None

    spacing = SpamGuard()
    variants = (
        "heyyyy there",
        "heyyyyy  there",
        "heyyy there",
        "heyyyy there",
    )
    for message_id, text in enumerate(variants, start=1):
        assert _inspect(spacing, message_id, text, now=message_id) is None


def test_repeat_fingerprint_ignores_punctuation_and_emoji_decoration() -> None:
    guard = SpamGuard()
    variants = (
        "این پیام را بارها و بارها می‌فرستم",
        "این پیام را بارها و بارها می‌فرستم!!!",
        "این پیام را بارها و بارها می‌فرستم 🔥",
        "این، پیام را بارها و بارها می‌فرستم",
    )
    decisions = [
        _inspect(guard, index + 1, text, now=index + 1)
        for index, text in enumerate(variants)
    ]
    assert decisions[:2] == [None, None]
    assert decisions[2] is not None and decisions[2].kind == "repeat"
    assert decisions[3] is not None and decisions[3].kind == "incident"


def test_repeat_key_preserves_source_arabic_letter_distinctions() -> None:
    guard = SpamGuard()
    texts = (
        "یک پیام معمولی برای آزمون",
        "يك پیام معمولی برای آزمون",
        "یک پیام معمولی برای آزمون",
    )
    for message_id, text in enumerate(texts, start=1):
        assert _inspect(guard, message_id, text, now=message_id) is None

    decision = _inspect(guard, 4, texts[0], now=4)
    assert decision is not None and decision.kind == "repeat"
    assert decision.message_ids == ("1", "3", "4")


def test_source_live_ad_emoji_signal_is_warning_level_without_new_filter_terms() -> None:
    guard = SpamGuard()
    decision = _inspect(guard, 1, "لایو گذاشتم 👙🍑")
    assert decision is not None and decision.kind == "live_ad_emoji"
    assert decision.policy == "warning" and decision.action is None
    assert decision.message_ids == ("1",)

    assert _inspect(SpamGuard(), 1, "لایو مسابقهٔ فوتبال 🙂") is None
    live = _inspect(SpamGuard(), 1, "LIVE stream 😈")
    assert live is not None and live.kind == "live_ad_emoji"
    assert _inspect(SpamGuard(), 1, "جلسهٔ زنده 👙") is None


def test_contact_and_group_mention_checks_are_warning_signals() -> None:
    phone_guard = SpamGuard()
    phone = _inspect(phone_guard, 1, "شماره تماس ۰۹۱۲۳۴۵۶۷۸۹")
    assert phone is not None and phone.kind == "phone_number"
    assert phone.policy == "warning" and not phone.action_required
    assert _inspect(phone_guard, 2, "کد سفارش 1234567890") is None

    mention_guard = SpamGuard()
    mention = _inspect(mention_guard, 1, "اطلاعیه برای @all")
    assert mention is not None and mention.kind == "group_mention"
    assert mention.policy == "warning"
    everyone = _inspect(SpamGuard(), 1, "برای @everyone")
    assert everyone is not None and everyone.kind == "public_username"
    assert _inspect(SpamGuard(), 1, "ایمیل mail@gmail.com") is None


def test_phone_username_and_forward_checks_are_configurable() -> None:
    guard = SpamGuard(
        check_phone_numbers=False,
        check_usernames=False,
        check_forwarded=False,
    )
    assert _inspect(guard, 1, "تماس ۰۹۱۲۳۴۵۶۷۸۹") is None
    assert _inspect(guard, 2, "پیام به @public_user") is None
    assert _inspect(guard, 3, forwarded=True) is None


def test_source_site_link_policy_is_a_warning_signal() -> None:
    unknown = _inspect(SpamGuard(), 1, "برای جزئیات https://newsite.example/path")
    assert unknown is not None and unknown.kind == "site_link"
    assert unknown.policy == "warning" and not unknown.action_required
    assert "newsite.example" in unknown.reason

    blocked = _inspect(SpamGuard(), 1, "https://xvideos.com/watch")
    assert blocked is not None and blocked.kind == "site_link"
    assert blocked.policy == "warning" and "مسدود" in blocked.reason

    assert _inspect(SpamGuard(), 1, "جلسه https://splus.ir/meet/room-1") is None
    assert _inspect(SpamGuard(), 1, "لینک https://github.com/fox") is None


def test_site_link_policy_toggle_is_runtime_injectable() -> None:
    settings = {"enabled": False}
    guard = SpamGuard(site_link_policy_enabled=lambda: settings["enabled"])
    assert _inspect(guard, 1, "https://quiet-one.example/path") is None

    settings["enabled"] = True
    enabled = _inspect(guard, 2, "https://newsite.example/path")
    assert enabled is not None and enabled.kind == "site_link"

    settings["enabled"] = False
    assert _inspect(guard, 3, "https://quiet-three.example/path") is None


def test_public_username_is_a_warning_signal_not_a_ban_detector() -> None:
    guard = SpamGuard()
    decision = _inspect(guard, 1, "برای هماهنگی پیام بدهید به @public_user")
    assert decision is not None and decision.kind == "public_username"
    assert decision.policy == "warning"
    assert not decision.action_required
    assert decision.message_ids == ("1",)
    assert _inspect(guard, 2, "ایمیل user@gmail.com") is None


def test_source_username_ad_multiple_id_and_short_handle_rules() -> None:
    long_plain = (
        "این یک پیام عادی و نسبتاً طولانی درباره برنامه هفته است که فقط "
        "برای نمونه شناسه @public_user را دارد."
    )
    assert len(long_plain) >= 50
    assert _inspect(SpamGuard(), 1, long_plain) is None

    ad = "برای عضویت در کانال با @public_user تماس بگیرید؛ توضیحات بیشتر اینجاست."
    ad_decision = _inspect(SpamGuard(), 1, ad)
    assert ad_decision is not None and ad_decision.kind == "username_ad"
    assert ad_decision.policy == "warning"

    multiple = (
        "این پیام توضیحی نسبتاً طولانی است؛ برای اطلاعات به @one_user و "
        "@two_user مراجعه کنید."
    )
    multiple_decision = _inspect(SpamGuard(), 1, multiple)
    assert multiple_decision is not None
    assert multiple_decision.kind == "multiple_usernames"
    assert multiple_decision.policy == "warning"

    persian = _inspect(SpamGuard(), 1, "پیام به @کاربر")
    assert persian is not None and persian.kind == "public_username"


def test_active_public_and_admin_commands_bypass_repeat_detection() -> None:
    commands = (
        "لیست بازی",
        "قفل",
        "تغییر مجازات",
        "ثبت ادمین",
        "تایید عملیات job-1",
        "آزاد @user",
        "فیلتر اسم نامناسب",
        "فعال کلمات ممنوعه",
        "ثبت پروفایل",
        "سایت بازی",
        "حدس ایموجی",
        "/فیلترها",
    )
    assert all(_is_likely_command(command) for command in commands)
    assert not _is_likely_command("این یک پیام معمولی دربارهٔ فیلتر اسم است")


def test_event_dedup_and_group_user_state_isolation() -> None:
    guard = SpamGuard()
    assert _inspect(guard, 1, "پیام برای آزمایش گروه", now=1) is None
    assert _inspect(guard, 1, "پیام برای آزمایش گروه", now=2) is None
    for message_id in range(2, 4):
        assert _inspect(
            guard, message_id, "پیام برای آزمایش گروه", group="other", now=message_id
        ) is None
    for message_id in range(2, 4):
        assert _inspect(
            guard, message_id, "پیام برای آزمایش گروه", user="other", now=message_id
        ) is None
    assert guard.tracked_sender_count >= 3


def test_similar_promotional_payload_hits_but_unrelated_campaigns_do_not_merge() -> None:
    guard = SpamGuard()
    assert _inspect(guard, 1, "بیوچک بیا پیوی") is None
    decision = _inspect(guard, 2, "بیوچک بیا پیوی!!! 🔥")
    assert decision is not None and decision.kind == "promo_wave"
    assert decision.policy == "ban"
    assert decision.action == "ban"
    assert decision.message_ids == ("1", "2")

    separate = SpamGuard()
    assert _inspect(separate, 1, "بیوچک بیا پیوی") is None
    assert _inspect(separate, 2, "جوین کانال") is None

    similar_anchor_only = SpamGuard()
    assert _inspect(similar_anchor_only, 1, "بیوچک بیا پیوی") is None
    shared_marker = _inspect(similar_anchor_only, 2, "بیوچک عکس جدید")
    assert shared_marker is not None and shared_marker.kind == "promo_wave"
    assert shared_marker.message_ids == ("1", "2")

    clean_campaign = SpamGuard()
    assert _inspect(clean_campaign, 1, "بیوچک بیا پیوی") is None
    assert _inspect(clean_campaign, 2, "جوین کانال") is None
    result = _inspect(clean_campaign, 3, "بیوچک بیا پیوی")
    assert result is not None and result.kind == "promo_wave"
    assert result.message_ids == ("1", "2", "3")


def test_promo_similarity_accepts_a_shared_meaningful_phrase() -> None:
    guard = SpamGuard()
    assert _inspect(guard, 1, "بیوچک عکس تازه پیام یک") is None
    decision = _inspect(guard, 2, "فالوکن عکس تازه لینک امروز")
    assert decision is not None and decision.kind == "promo_wave"
    assert decision.message_ids == ("1", "2")


def test_promo_similarity_uses_normalized_sequence_matching() -> None:
    guard = SpamGuard()
    assert _inspect(guard, 1, "پیام بیوچک تازه") is None
    decision = _inspect(guard, 2, "پیام چکبیو تازه")
    assert decision is not None and decision.kind == "promo_wave"
    assert decision.message_ids == ("1", "2")


def test_promo_comparison_normalizes_stretched_letters() -> None:
    guard = SpamGuard()
    assert _inspect(guard, 1, "بیوچک بیا پیوی") is None
    decision = _inspect(guard, 2, "بیوووچک بیا پییوی")
    assert decision is not None and decision.kind == "promo_wave"
    assert decision.message_ids == ("1", "2")


def test_source_short_markers_and_emoji_campaigns_are_recognized() -> None:
    marker = SpamGuard()
    assert _inspect(marker, 1, "فیلم جدید") is None
    result = _inspect(marker, 2, "فیلم جدید")
    assert result is not None and result.kind == "promo_wave"

    weak_marker = SpamGuard(generic_exemption=lambda _group, _user: True)
    for message_id in range(1, 4):
        assert _inspect(weak_marker, message_id, "بکوب") is None
    weak_result = _inspect(weak_marker, 4, "بکوب")
    assert weak_result is not None and weak_result.kind == "promo_wave"

    emoji = SpamGuard()
    for message_id in range(1, 4):
        assert _inspect(emoji, message_id, "🔞") is None
    result = _inspect(emoji, 4, "🔞")
    assert result is not None and result.kind == "promo_wave"


def test_short_promo_wave_and_generic_repeat_thresholds_are_separate() -> None:
    # Isolate the source's four-message short-promo rule from its separate
    # three-copy generic-repeat rule.
    guard = SpamGuard(generic_exemption=lambda _group, _user: True)
    for message_id in range(1, 4):
        assert _inspect(guard, message_id, "جوین") is None
    decision = _inspect(guard, 4, "جوین")
    assert decision is not None and decision.kind == "promo_wave"
    assert decision.message_ids == ("1", "2", "3", "4")

    natural = SpamGuard()
    assert _inspect(natural, 1, "این فیلم را دیشب دیدم") is None
    assert _inspect(natural, 2, "این فیلم را دیشب دیدم") is None
    natural_repeat = _inspect(natural, 3, "این فیلم را دیشب دیدم")
    assert natural_repeat is not None and natural_repeat.kind == "repeat"

    interleaved = SpamGuard()
    sequence = ("جوین", "سلام", "جوین", "سلام", "جوین")
    interleaved_decision = None
    for message_id, text in enumerate(sequence, start=1):
        interleaved_decision = _inspect(
            interleaved, message_id, text, now=message_id
        )
        if message_id < len(sequence):
            assert interleaved_decision is None
    assert interleaved_decision is not None
    assert interleaved_decision.kind == "repeat"
    assert interleaved_decision.message_ids == ("1", "3", "5")


def test_trivial_repeated_chat_is_not_punished_and_game_exempts_generic_repeat() -> None:
    guard = SpamGuard()
    for message_id in range(1, 12):
        assert _inspect(guard, message_id, "سلام") is None

    greeting = SpamGuard()
    for message_id in range(1, 8):
        assert _inspect(greeting, message_id, "hello!!!") is None
        assert _inspect(greeting, message_id + 20, "سلام دوستان!") is None

    for benign in (
        "چخبر", "چخبرا", "فعلا", "بای", "خدانگهدار", "عشقم", "رل بزنیم",
        "زندگیم", "دوست دارم", "حالت چطوره", "چه کاره ای", "کی هستی", "هعی",
    ):
        phrase_guard = SpamGuard()
        for message_id in range(1, 8):
            assert _inspect(phrase_guard, message_id, benign) is None

    game_guard = SpamGuard(generic_exemption=lambda _group, _user: True)
    for message_id in range(1, 7):
        assert _inspect(
            game_guard,
            message_id,
            "این یک پاسخ عادی برای بازی است",
            now=message_id,
        ) is None


def test_low_entropy_and_intra_message_repetition_are_bounded_signals() -> None:
    low_entropy = SpamGuard()
    assert _inspect(low_entropy, 1, "aaaaaaaaaaaa") is None
    assert _inspect(low_entropy, 2, "bbbbbbbbbbbb") is None
    decision = _inspect(low_entropy, 3, "cccccccccccc")
    assert decision is not None and decision.kind == "low_entropy"
    assert decision.message_ids == ("1", "2", "3")

    packed = SpamGuard()
    decision = _inspect(
        packed,
        1,
        "تبلیغ تبلیغ تبلیغ تبلیغ تبلیغ تبلیغ",
    )
    assert decision is not None and decision.kind == "packed_repeat"
    assert decision.policy == "ban"

    promo_repeat = SpamGuard()
    decision = _inspect(
        promo_repeat,
        1,
        "بیو چک بیا پیوی بیوچک بیا پیوی",
    )
    assert decision is not None and decision.kind == "packed_repeat"
    assert decision.policy == "ban"


def test_decorative_pattern_is_detected_without_flagging_ordinary_punctuation() -> None:
    assert _inspect(SpamGuard(), 1, "!!!!!") is None
    assert _inspect(SpamGuard(), 1, "😂😂😂😂😂") is None

    decision = _inspect(SpamGuard(), 1, "•••••")
    assert decision is not None and decision.kind == "decorative_spam"
    assert decision.message_ids == ("1",)

    hangul = _inspect(SpamGuard(), 1, "웃웃웃웃")
    assert hangul is not None and hangul.kind == "decorative_spam"
    repeated_symbol = _inspect(SpamGuard(), 1, "\\\\\\\\\\\\")
    assert repeated_symbol is not None and repeated_symbol.kind == "decorative_spam"
    shaped = _inspect(SpamGuard(), 1, "▃▅▆█ 웃 - 웃 █▆▅▃")
    assert shaped is not None and shaped.kind == "decorative_spam"
    assert shaped.policy == "ban" and shaped.action == "ban"


def test_repeated_text_in_two_message_flood_bans_and_game_exempts_repeat() -> None:
    guard = SpamGuard()
    texts = (
        "این پیام معمولی را برای آزمایش می‌فرستم",
        "یک گفتگوی کوتاه و عادی در گروه",
    )
    for message_id in range(1, 5):
        assert _inspect(
            guard,
            message_id,
            texts[(message_id - 1) % 2],
            now=float(message_id),
        ) is None
    decision = _inspect(
        guard, 5, texts[0], now=5.0
    )
    assert decision is not None and decision.kind == "repeat"
    assert decision.policy == "ban"
    assert decision.action_required
    assert decision.message_ids == ("1", "3", "5")

    game_guard = SpamGuard(generic_exemption=lambda _group, _user: True)
    for message_id in range(1, 7):
        assert _inspect(
            game_guard,
            message_id,
            texts[(message_id - 1) % 2],
            now=float(message_id),
        ) is None


def test_three_copy_repeat_preempts_four_key_flood_and_game_exempts_it() -> None:
    messages = (
        "متن آزمایشی برای بررسی امروز",
        "پیام معمولی در گفتگوی گروهی",
        "یک نمونه تازه برای آزمایش",
        "کاربر درباره موضوعی جدید نوشت",
    )
    guard = SpamGuard()
    decisions = []
    for message_id in range(1, 11):
        decisions.append(
            _inspect(
                guard,
                message_id,
                messages[(message_id - 1) % len(messages)],
                now=float(message_id),
            )
        )
    assert decisions[8] is not None and decisions[8].kind == "repeat"
    assert decisions[9] is not None and decisions[9].kind == "incident"

    game_guard = SpamGuard(generic_exemption=lambda _group, _user: True)
    for message_id in range(1, 11):
        assert _inspect(
            game_guard,
            message_id,
            messages[(message_id - 1) % len(messages)],
            now=float(message_id),
        ) is None


def test_gif_threshold_flags_one_hour_and_failed_mute_keeps_only_user_flagged() -> None:
    guard = SpamGuard()
    for message_id in range(1, 6):
        assert _inspect(guard, message_id, media_kind="gif", now=message_id) is None
    decision = _inspect(guard, 6, media_kind="gif", now=6)
    assert decision is not None and decision.kind == "gif_burst"
    assert decision.policy == "fixed_mute"
    assert decision.duration_seconds == 3600
    assert decision.action_required is True
    assert decision.message_ids == tuple(str(value) for value in range(2, 7))

    next_gif = _inspect(guard, 7, media_kind="gif", now=7)
    assert next_gif is not None and not next_gif.action_required
    assert next_gif.message_ids == ("7",)
    guard.reset_gif_history("g", "u")
    after_reset = _inspect(guard, 8, media_kind="gif", now=8)
    assert after_reset is not None and not after_reset.action_required
    assert after_reset.message_ids == ("8",)
    guard.resolve_action("g", "u", "gif_burst", success=False)
    after_failed_mute = _inspect(guard, 9, media_kind="gif", now=9)
    assert after_failed_mute is not None
    assert after_failed_mute.kind == "gif_burst"
    assert not after_failed_mute.action_required
    assert after_failed_mute.message_ids == ("9",)
    assert _inspect(guard, 1, media_kind="gif", group="other", now=8) is None


def test_gif_run_has_no_two_minute_cutoff_and_flag_expires_after_one_hour() -> None:
    guard = SpamGuard()
    times = (1, 122, 243, 364, 485, 606)
    decision = None
    for message_id, timestamp in enumerate(times, start=1):
        decision = _inspect(
            guard, message_id, media_kind="gif", now=float(timestamp)
        )
        if message_id < 6:
            assert decision is None
    assert decision is not None and decision.kind == "gif_burst"
    assert decision.message_ids == ("2", "3", "4", "5", "6")

    active = _inspect(guard, 7, media_kind="gif", now=4205.0)
    assert active is not None and not active.action_required
    expired = _inspect(guard, 8, media_kind="gif", now=4206.0)
    assert expired is None

    idle = SpamGuard()
    for message_id in range(1, 6):
        assert _inspect(idle, message_id, media_kind="gif", now=message_id) is None
    # Source drops an unflagged counter after one hour idle, not after 120s.
    assert _inspect(idle, 6, media_kind="gif", now=3606.0) is None


def test_forward_metadata_album_siblings_and_24_hour_mute_threshold() -> None:
    guard = SpamGuard(check_forwarded=True)
    assert _inspect(guard, 1, grouped_id=77, now=1) is None

    tagged = _inspect(guard, 2, grouped_id=77, forwarded=True, now=2)
    assert tagged is not None and tagged.kind == "forward"
    assert tagged.message_ids == ("1", "2")
    assert tagged.policy == "delete_only"

    sibling = _inspect(guard, 3, grouped_id=77, now=3)
    assert sibling is not None and sibling.kind == "forward"
    assert sibling.message_ids == ("1", "2", "3")
    assert sibling.action_required is False

    third = _inspect(guard, 4, forwarded=True, now=4)
    assert third is not None and third.action_required
    assert third.policy == "fixed_mute"
    assert third.action == "mute"
    assert third.duration_seconds == 24 * 60 * 60

    guard.resolve_action("g", "u", "forward", success=False)
    assert _inspect(guard, 5, forwarded=True, now=5).action_required is False


def test_forward_counter_is_deduplicated_and_uses_a_rolling_ten_minute_window() -> None:
    guard = SpamGuard(check_forwarded=True)
    first = _inspect(guard, 1, forwarded=True, now=1.0)
    assert first is not None and not first.action_required
    assert _inspect(guard, 1, forwarded=True, now=2.0) is None

    second = _inspect(guard, 2, forwarded=True, now=599.0)
    assert second is not None and not second.action_required
    third = _inspect(guard, 3, forwarded=True, now=602.0)
    assert third is not None and not third.action_required
    fourth = _inspect(guard, 4, forwarded=True, now=603.0)
    assert fourth is not None and fourth.action_required
    assert fourth.message_ids == ("4",)


def test_non_forward_counters_are_not_used_and_history_capacity_is_hard_bounded() -> None:
    guard = SpamGuard(max_senders=1, max_history_per_sender=8)
    # A forwarded-view count is deliberately not an input to the detector.
    assert _inspect(guard, 1, "regular text", now=1) is None
    assert _inspect(guard, 2, "another regular text", group="other", now=2) is None
    assert guard.tracked_sender_count <= 1


def test_fair_delete_queue_serializes_each_group_deduplicates_and_retries() -> None:
    async def scenario() -> None:
        active_by_group: dict[str, int] = {}
        max_active_by_group: dict[str, int] = {}
        calls: list[tuple[str, str]] = []
        attempts: dict[tuple[str, str], int] = {}

        async def delete(group_id: str, message_id: str) -> bool:
            key = (group_id, message_id)
            attempts[key] = attempts.get(key, 0) + 1
            calls.append(key)
            active_by_group[group_id] = active_by_group.get(group_id, 0) + 1
            max_active_by_group[group_id] = max(
                max_active_by_group.get(group_id, 0), active_by_group[group_id]
            )
            await asyncio.sleep(0)
            active_by_group[group_id] -= 1
            return attempts[key] >= 3 if message_id == "retry" else True

        queue = FairDeleteQueue(delete, max_pending_total=20, max_pending_per_group=10)
        assert queue.enqueue("g", [1, 1, 2], max_attempts=2) == 3
        retry_result = asyncio.create_task(queue.delete_one("g", "retry"))
        await queue.drain()
        assert await retry_result is True
        assert max_active_by_group["g"] == 1
        assert calls.count(("g", "1")) == 1
        assert attempts[("g", "retry")] == 3
        assert queue.pending_count == 0
        await queue.close()

    asyncio.run(scenario())

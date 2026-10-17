"""🛡️ فیلتر نام و لقب + قالب‌بندی راهنما.

پوشش:
    • تشخیص فحش فارسی، انگلیسی و فینگلیش
    • دور زدن با فاصله، نقطه، تکرار حرف و leetspeak
    • نام‌های واقعی نباید قربانی شوند (کسری، مکسیم، Kosar…)
    • «پهلوی» پیام مخصوص خودش را دارد
    • فیلتر روی ثبت اسم، پروفایل و لقب اعمال شود
    • راهنما: فقط عنوان و توضیح Bold، دستورها عادی، بدون Markdown

    python tests/test_name_filter_and_help.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import economy.shop.store as store
import economy.storage as storage
import modules.group_storage as group_storage
import test_economy_routing as routing
from economy import profiles
from economy import name_filter
from modules.group_memory import extract_name
from test_economy_routing import build_handler

PASSED = FAILED = 0
CHAT = -1009999888877


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


def fresh():
    temp = Path(tempfile.mkdtemp())
    storage.use_file(temp / "economy.json")
    store.ITEMS_FILE = temp / "shop.json"
    store._cache = None
    store._cache_mtime = None
    group_storage.activate_group(CHAT, "گروه تست")
    return temp


class HelpEvent(routing.Event):
    """رویدادی که entityها را هم نگه می‌دارد."""

    def __init__(self, text, user_id):
        super().__init__(text, user_id)
        self.captured = []

    async def reply(self, text, **kwargs):
        self.captured.append((text, kwargs.get("formatting_entities") or []))
        self.replies.append(text)
        return None


# ===========================================================================
# فیلتر: فحش فارسی
# ===========================================================================
def test_persian_profanity_blocked():
    print("\n### 🛡️ فحش فارسی رد می‌شود")
    for word in ("کیر", "کس", "کون", "جنده", "کسکش", "حرومزاده",
                 "مادرجنده", "بیناموس", "کونی", "گوه", "لاشی",
                 "پدرسگ", "دیوث", "جاکش", "فاحشه", "بیشرف"):
        check(f"«{word}» رد می‌شود",
              name_filter.classify(word) == name_filter.BANNED,
              f"-> {name_filter.classify(word)}")


def test_english_profanity_blocked():
    print("\n### 🛡️ فحش انگلیسی رد می‌شود")
    for word in ("fuck", "shit", "bitch", "asshole", "bastard",
                 "whore", "slut", "cunt", "dick", "nigger", "retard",
                 "motherfucker", "FUCK", "Bitch"):
        check(f"«{word}» رد می‌شود",
              name_filter.classify(word) == name_filter.BANNED,
              f"-> {name_filter.classify(word)}")


def test_finglish_profanity_blocked():
    print("\n### 🛡️ فحش فینگلیش رد می‌شود")
    for word in ("kir", "kos", "koon", "gooh", "koskesh", "jende",
                 "haromzade", "kooni", "pedarsag", "jakesh"):
        check(f"«{word}» رد می‌شود",
              name_filter.classify(word) == name_filter.BANNED,
              f"-> {name_filter.classify(word)}")


# ===========================================================================
# فیلتر: دور زدن
# ===========================================================================
def test_spacing_evasion_blocked():
    print("\n### 🛡️ دور زدن با فاصله و نقطه")
    for word in ("ک ی ر", "ک.ی.ر", "ک-ی-ر", "ک_ی_ر", "ک،ی،ر",
                 "f u c k", "f-u-c-k", "f.u.c.k", "s h i t",
                 "j e n d e", "ج ن د ه"):
        check(f"«{word}» رد می‌شود",
              name_filter.classify(word) == name_filter.BANNED,
              f"-> {name_filter.classify(word)}")


def test_repetition_evasion_blocked():
    print("\n### 🛡️ دور زدن با تکرار حرف")
    for word in ("کییییر", "کییر", "fuuuck", "shiiit", "jendeee",
                 "kiiir"):
        check(f"«{word}» رد می‌شود",
              name_filter.classify(word) == name_filter.BANNED,
              f"-> {name_filter.classify(word)}")


def test_leetspeak_evasion_blocked():
    print("\n### 🛡️ دور زدن با عدد و نماد")
    for word in ("sh1t", "f4ck", "fu(k".replace("(", "c"), "b1tch",
                 "k1r", "a55hole", "d1ck", "@sshole"):
        check(f"«{word}» رد می‌شود",
              name_filter.classify(word) == name_filter.BANNED,
              f"-> {name_filter.classify(word)}")


def test_invisible_character_evasion_blocked():
    print("\n### 🛡️ دور زدن با نویسهٔ نامرئی")
    for word in ("ک\u200cی\u200cر", "f\u200bu\u200bc\u200bk",
                 "کی\u200dر", "ک\u0640ی\u0640ر"):
        check("نویسهٔ نامرئی کارساز نیست",
              name_filter.classify(word) == name_filter.BANNED,
              f"-> {word!r} {name_filter.classify(word)}")


def test_embedded_profanity_blocked():
    print("\n### 🛡️ فحش داخل جملهٔ بلندتر")
    for phrase in ("من کیر هستم", "آقای جنده", "the fuck king",
                   "Mr Bitch", "سلام کسکش"):
        check(f"«{phrase}» رد می‌شود",
              name_filter.classify(phrase) == name_filter.BANNED,
              f"-> {name_filter.classify(phrase)}")


# ===========================================================================
# فیلتر: نام‌های واقعی نباید قربانی شوند
# ===========================================================================
def test_real_names_allowed():
    print("\n### ✅ نام واقعی رد نمی‌شود")
    for name in ("علی", "محمد", "محمد رضا", "امیرحسین", "کسری", "کسرا",
                 "مکسیم", "کیانا", "کوروش", "کوثر", "سارا", "زهرا",
                 "نگار", "بهاره", "سینا", "نیلوفر", "فاطمه", "مهسا",
                 "پارسا", "آرمان", "نصرت الله", "سید علی"):
        check(f"«{name}» مجاز است", name_filter.is_allowed(name),
              f"-> {name_filter.classify(name)}")


def test_finglish_real_names_allowed():
    print("\n### ✅ نام واقعی فینگلیش رد نمی‌شود")
    for name in ("Kosar", "Kasra", "Kian", "Kiana", "Koorosh",
                 "Kourosh", "Maksim", "Koosha", "Gohar", "Sina",
                 "Mohammad", "Sara", "Ali", "Nika", "Kimia"):
        check(f"«{name}» مجاز است", name_filter.is_allowed(name),
              f"-> {name_filter.classify(name)}")


def test_harmless_nicknames_allowed():
    print("\n### ✅ لقب بی‌ضرر مجاز است")
    for nick in ("داداش", "گلی", "Fox King", "Shadow", "آقا رضا",
                 "کاپیتان", "استاد", "بهاری", "Champion"):
        check(f"«{nick}» مجاز است", name_filter.is_allowed(nick),
              f"-> {name_filter.classify(nick)}")


# ===========================================================================
# «پهلوی»
# ===========================================================================
def test_pahlavi_restricted():
    print("\n### 🚫 «پهلوی» پیام مخصوص دارد")
    for word in ("پهلوی", "پهلوي", "پ.ه.ل.و.ی", "پ ه ل و ی",
                 "pahlavi", "PAHLAVI", "pahlevi", "رضا پهلوی",
                 "محمدرضا پهلوی"):
        kind = name_filter.classify(word)
        check(f"«{word}» محدود است", kind == name_filter.RESTRICTED,
              f"-> {kind}")

    ok, message = name_filter.check("پهلوی")
    check("ثبت انجام نمی‌شود", ok is False)
    check("پیام دقیقاً مطابق خواسته است",
          message == "شما دیگر نمی‌توانید از این بخش استفاده کنید. "
                     "این مورد غیرمجاز است.",
          f"-> {message}")


def test_banned_message_text():
    print("\n### 🚫 متن پیام فحش")
    ok, message = name_filter.check("کیر")
    check("ثبت انجام نمی‌شود", ok is False)
    check("پیام دقیقاً مطابق خواسته است",
          message == "این نام یا لقب غیرمجاز است و امکان استفاده از آن "
                     "وجود ندارد.",
          f"-> {message}")


def test_clean_input_passes():
    print("\n### ✅ ورودی سالم پیام خطا نمی‌گیرد")
    ok, message = name_filter.check("علی")
    check("مجاز است", ok is True)
    check("پیامی ندارد", message is None)


# ===========================================================================
# اعمال روی پروفایل
# ===========================================================================
def test_profile_name_filtered():
    print("\n### 🛡️ نام پروفایل فیلتر می‌شود")
    fresh()
    for bad in ("کیر", "fuck", "جنده", "ک.ی.ر"):
        try:
            profiles.validate_name(bad)
            check(f"«{bad}» رد می‌شود", False)
        except profiles.ProfileError as error:
            check(f"«{bad}» رد می‌شود",
                  str(error) == name_filter.MESSAGE_BANNED, f"-> {error}")
    check("نام سالم پذیرفته می‌شود",
          profiles.validate_name("علی") == "علی")


def test_profile_nickname_filtered():
    print("\n### 🛡️ لقب پروفایل فیلتر می‌شود")
    fresh()
    for bad in ("جنده", "bitch", "کسکش"):
        try:
            profiles.validate_nickname(bad)
            check(f"«{bad}» رد می‌شود", False)
        except profiles.ProfileError as error:
            check(f"«{bad}» رد می‌شود",
                  str(error) == name_filter.MESSAGE_BANNED, f"-> {error}")
    check("لقب سالم پذیرفته می‌شود",
          profiles.validate_nickname("داداش") == "داداش")


def test_profile_pahlavi_restricted():
    print("\n### 🚫 «پهلوی» در پروفایل")
    fresh()
    for field, validator in (("نام", profiles.validate_name),
                             ("لقب", profiles.validate_nickname)):
        try:
            validator("پهلوی")
            check(f"«پهلوی» در {field} رد می‌شود", False)
        except profiles.ProfileError as error:
            check(f"«پهلوی» در {field} رد می‌شود",
                  str(error) == name_filter.MESSAGE_RESTRICTED,
                  f"-> {error}")


def test_profile_register_blocks_bad_name():
    print("\n### 🛡️ ثبت پروفایل با نام بد انجام نمی‌شود")
    fresh()
    try:
        profiles.register(CHAT, 10, name="کیر", city="تهران", age=20)
        check("ثبت رد می‌شود", False)
    except profiles.ProfileError:
        check("ثبت رد می‌شود", True)
    check("هیچ پروفایلی ساخته نشد",
          profiles.get(CHAT, 10)["registered"] is False)


def test_profile_update_blocks_bad_nickname():
    print("\n### 🛡️ ویرایش لقب با کلمهٔ بد انجام نمی‌شود")
    fresh()
    profiles.register(CHAT, 11, name="سارا", city="یزد", age=22)
    try:
        profiles.update(CHAT, 11, nickname="جنده")
        check("ویرایش رد می‌شود", False)
    except profiles.ProfileError:
        check("ویرایش رد می‌شود", True)
    check("لقب قبلی دست‌نخورده ماند",
          profiles.get(CHAT, 11)["nickname"] is None)
    check("بقیهٔ اطلاعات سالم‌اند",
          profiles.get(CHAT, 11)["name"] == "سارا")


# ===========================================================================
# اعمال روی «ثبت اسم» حافظهٔ گروه
# ===========================================================================
def test_group_memory_filtered():
    print("\n### 🛡️ حافظهٔ گروه فیلتر می‌شود")
    check("فحش فارسی رد می‌شود", extract_name("کیر")[1] is not None)
    check("فحش انگلیسی کد banned می‌دهد",
          extract_name("fuck")[1] == "banned")
    check("«پهلوی» کد restricted می‌دهد",
          extract_name("پهلوی")[1] == "restricted")
    check("دور زدن با نقطه گرفته می‌شود",
          extract_name("ک.ی.ر")[1] == "banned")
    check("نام سالم پذیرفته می‌شود", extract_name("علی")[0] == "علی")
    check("نام مرکب سالم پذیرفته می‌شود",
          extract_name("محمد رضا")[0] is not None)


def test_group_memory_through_handler():
    print("\n### 🔌 «ثبت اسم» از مسیر واقعی هندلر")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        results = {}
        for label, text in (("bad", "ثبت اسم کیر"),
                            ("pahlavi", "ثبت اسم پهلوی"),
                            ("good", "ثبت اسم علی")):
            event = routing.Event(text, 20)
            await handler(event)
            results[label] = event
        return bot, results

    bot, results = asyncio.run(scenario())
    check("نام بد پیام غیرمجاز می‌گیرد",
          results["bad"].said(name_filter.MESSAGE_BANNED),
          f"-> {results['bad'].replies}")
    check("«پهلوی» پیام مخصوص می‌گیرد",
          results["pahlavi"].said(name_filter.MESSAGE_RESTRICTED),
          f"-> {results['pahlavi'].replies}")
    check("نام سالم ثبت می‌شود", results["good"].said("ثبت شد"),
          f"-> {results['good'].replies}")
    check("هیچ خطایی رخ نداد", not bot.logger.errors,
          f"-> {[e[:100] for e in bot.logger.errors][:1]}")


def test_main_antispam_untouched():
    """فیلتر فقط برای نام و لقب است، نه پیام‌های عادی گروه."""
    print("\n### 🔒 سیستم اصلی ربات دست‌نخورده است")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        event = routing.Event("سلام بچه‌ها چطورید", 21)
        await handler(event)
        return event

    event = asyncio.run(scenario())
    check("پیام عادی گروه پاسخ فیلتر نمی‌گیرد",
          not event.said(name_filter.MESSAGE_BANNED))
    check("پیام عادی محدود نمی‌شود",
          not event.said(name_filter.MESSAGE_RESTRICTED))


# ===========================================================================
# قالب‌بندی راهنما
# ===========================================================================
def _help_output(command="لیست کاربران"):
    async def scenario():
        group_storage.activate_group(CHAT, "گروه تست")
        bot, handler = await build_handler()
        event = HelpEvent(command, 30)
        await handler(event)
        return event

    event = asyncio.run(scenario())
    return event.captured[0] if event.captured else ("", [])


def _decode(text, entity):
    raw = text.encode("utf-16-le")
    return raw[entity.offset * 2:
               (entity.offset + entity.length) * 2].decode("utf-16-le")


def test_help_sections_present():
    print("\n### 📌 بخش‌های راهنما")
    text, _ = _help_output()
    for section in ("👤 کاربران:", "🎮 لیست بازی‌ها:",
                    "🎵 جستجوی آهنگ و مطالب:", "✍️ ساخت فونت:",
                    "🌐 ترجمه انگلیسی به فارسی:", "🏆 امتیاز و رتبه:",
                    "⏰ یادآوری:", "🧠 حافظه گروه:", "🧩 تحلیل نام:",
                    "📚 دانستنی:", "🛒 اقتصاد و آیتم‌ها:",
                    "🛡️ امنیت گروه:"):
        check(f"بخش «{section}» هست", section in text)
    snippet = "برای بازی خصوصی و انلاین بنویسید 🪄\nسایت بازی"
    check("راهنمای سایت بازی در لیست کاربران هست", snippet in text)
    check("فاصلهٔ خالی اضافه بین دو خط سایت بازی نیست",
          "برای بازی خصوصی و انلاین بنویسید 🪄\n\nسایت بازی" not in text)
    games_text, _ = _help_output("لیست بازی")
    check("راهنمای سایت بازی در لیست بازی هست", snippet in games_text)
    check("فاصلهٔ خالی اضافه در لیست بازی نیست",
          "برای بازی خصوصی و انلاین بنویسید 🪄\n\nسایت بازی" not in games_text)


def test_help_economy_section():
    print("\n### 📌 بخش اقتصاد در راهنما")
    text, _ = _help_output()
    check("راهنمای فروشگاه هست",
          "برای دیدن لیست خرید و آیتم‌ها بنویسید:\nفروشگاه" in text)
    check("راهنمای موجودی هست",
          "برای انتقال سکه، تبدیل سکه‌ها و مشاهده امکانات مالی بنویسید:"
          "\nموجودی" in text)
    check("راهنمای پروفایل هست",
          "برای ثبت و مدیریت پروفایل خود بنویسید:\n"
          "ثبت پرفایل | پرفایلم | حذف پرفایل" in text)


def test_help_explanations_are_bold():
    print("\n### 📌 جمله‌های توضیحی Bold هستند")
    from splusthon.tl.types import MessageEntityBold
    text, entities = _help_output()
    bolds = {_decode(text, e) for e in entities
             if isinstance(e, MessageEntityBold)}
    for phrase in ("👤 کاربران:", "🛒 اقتصاد و آیتم‌ها:",
                   "برای دیدن لیست خرید و آیتم‌ها بنویسید:",
                   "برای انتقال سکه، تبدیل سکه‌ها و مشاهده امکانات مالی "
                   "بنویسید:",
                   "برای ثبت و مدیریت پروفایل خود بنویسید:",
                   "برای ثبت اصل بنویسید:", "برای گرفتن بیوگرافی:",
                   "برای مشاهده بازی‌ها بنویسید:",
                   "برای بازی خصوصی و انلاین بنویسید",
                   "برای دریافت یک دانستنی:", "🛡️ امنیت گروه:"):
        check(f"«{phrase[:38]}» Bold است", phrase in bolds)


def test_help_commands_are_plain():
    print("\n### 📌 دستورها عادی می‌مانند")
    from splusthon.tl.types import MessageEntityBold
    text, entities = _help_output()
    bolds = {_decode(text, e) for e in entities
             if isinstance(e, MessageEntityBold)}
    for command in ("ثبت اصل", "اصلم", "آمارم", "بیوگرافی", "لیست بازی",
                    "ترجمه", "فروشگاه", "موجودی", "دانستنی", "رتبه ها",
                    "حافظه من", "امتیاز من", "یاد آوری", "سایت بازی",
                    "ثبت پرفایل | پرفایلم | حذف پرفایل"):
        check(f"«{command}» Bold نیست", command not in bolds)


def test_help_uses_no_markdown():
    print("\n### 📌 هیچ Markdown ای در راهنما نیست")
    text, entities = _help_output()
    for marker in ("**", "__", "```", "*"):
        check(f"«{marker}» در متن نیست", marker not in text)
    check("Bold با entity ساخته می‌شود", len(entities) > 0)


def test_help_entities_are_valid():
    print("\n### 📌 offsetهای راهنما درست‌اند")
    text, entities = _help_output()
    length = len(text.encode("utf-16-le")) // 2
    for entity in entities:
        check("offset داخل متن است",
              entity.offset >= 0
              and entity.offset + entity.length <= length,
              f"-> {entity.offset}+{entity.length} > {length}")
        fragment = _decode(text, entity)
        check("span به متن واقعی اشاره می‌کند",
              bool(fragment) and fragment in text)


def test_admin_help_still_works():
    print("\n### 📌 راهنمای ادمین دست‌نخورده است")
    text, entities = _help_output("لیست ادمینی")
    check("بخش ادمین می‌آید", "👑 دستورات ادمین‌ها:" in text)
    check("دستور قفل هست", "قفل" in text)
    check("بخش کاربران در آن نیست", "👤 کاربران:" not in text)
    check("entity دارد", len(entities) > 0)


# ===========================================================================
def main():
    test_persian_profanity_blocked()
    test_english_profanity_blocked()
    test_finglish_profanity_blocked()
    test_spacing_evasion_blocked()
    test_repetition_evasion_blocked()
    test_leetspeak_evasion_blocked()
    test_invisible_character_evasion_blocked()
    test_embedded_profanity_blocked()
    test_real_names_allowed()
    test_finglish_real_names_allowed()
    test_harmless_nicknames_allowed()
    test_pahlavi_restricted()
    test_banned_message_text()
    test_clean_input_passes()
    test_profile_name_filtered()
    test_profile_nickname_filtered()
    test_profile_pahlavi_restricted()
    test_profile_register_blocks_bad_name()
    test_profile_update_blocks_bad_nickname()
    test_group_memory_filtered()
    test_group_memory_through_handler()
    test_main_antispam_untouched()
    test_help_sections_present()
    test_help_economy_section()
    test_help_explanations_are_bold()
    test_help_commands_are_plain()
    test_help_uses_no_markdown()
    test_help_entities_are_valid()
    test_admin_help_still_works()
    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

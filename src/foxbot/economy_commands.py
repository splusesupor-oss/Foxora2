from __future__ import annotations

import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from foxbot import shop_catalog
from foxbot.economy_settings import load as _load_economy_settings
from foxbot.filters import WordFilter
from foxbot.storage import BotStore

ReplyCallback = Callable[..., Awaitable[Any]]

_COIN_LABELS = {"bronze": "برنز", "silver": "نقره", "gold": "طلا"}
_COIN_ORDER = {"bronze": 0, "silver": 1, "gold": 2}
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_PROFILE_AGE_DIGITS = _DIGITS | str.maketrans("𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵", "0123456789")
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_SESSION_PREFIX = "economy_session:"
_PROFILE_PREFIX = "economy_profile:"

_BADGES = (
    ("badge_fox", "🦊", "نشان روباه", 100, "silver"),
    ("badge_lion", "🦁", "نشان شیر", 120, "silver"),
    ("badge_heart", "🫀", "نشان قلب", 300, "bronze"),
    ("badge_king", "👑", "نشان پادشاه", 300, "silver"),
    ("badge_bolt", "⚡", "نشان صاعقه", 150, "silver"),
    ("badge_skull", "💀", "نشان اسکلت", 200, "silver"),
    ("badge_wolf", "🐺", "نشان گرگ", 180, "silver"),
    ("badge_dragon", "🐉", "نشان اژدها", 500, "silver"),
    ("badge_legend", "☠️", "نشان افسانه", 700, "silver"),
    ("badge_galaxy", "🌌", "نشان کهکشانی", 900, "silver"),
)
_STARS = (
    (1, "یک ستاره", 200), (2, "دو ستاره", 400), (3, "سه ستاره", 800),
    (4, "چهار ستاره", 1000), (5, "پنج ستاره", 1200), (6, "شش ستاره", 1400),
    (7, "هفت ستاره", 1800),
)
_TITLES = (
    ("title_fox_king", "👑", "𝙁𝙤𝙭 𝙆𝙞𝙣𝙜", "fox king"),
    ("title_dark_lord", "⚡", "𝘿𝙖𝙧𝙠 𝙇𝙤𝙧𝙙", "dark lord"),
    ("title_royal", "💎", "𝙍𝙤𝙮𝙖𝙡", "royal"),
    ("title_fox_boy", "🦊", "𝙁𝙤𝙭 𝘽𝙤𝙮", "fox boy"),
    ("title_killer", "☠️", "𝙆𝙞𝙡𝙡𝙚𝙧", "killer"),
    ("title_overlord", "👑", "𝙊𝙫𝙚𝙧𝙇𝙤𝙧𝙙", "overlord"),
    ("title_warrior", "⚔️", "𝙒𝙖𝙧𝙧𝙞𝙤𝙧", "warrior"),
    ("title_moon", "🌙", "𝙈𝙤𝙤𝙣", "moon"),
    ("title_devil", "😈", "𝘿𝙚𝙫𝙞𝙡", "devil"),
    ("title_reaper", "💀", "𝙍𝙚𝙖𝙥𝙚𝙧", "reaper"),
    ("title_phantom", "🎭", "𝙋𝙝𝙖𝙣𝙩𝙤𝙢", "phantom"),
    ("title_lone_wolf", "🐺", "𝙇𝙤𝙣𝙚 𝙒𝙤𝙡𝙛", "lone wolf"),
    ("title_dragon", "🐉", "𝘿𝙧𝙖𝙜𝙤𝙣", "dragon"),
    ("title_emperor", "⚜️", "𝙀𝙢𝙥𝙚𝙧𝙤𝙧", "emperor"),
    ("title_star_boy", "🌠", "𝙎𝙩𝙖𝙧𝘽𝙤𝙮", "starboy"),
)


def _items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item_id, emoji, name, price, coin in _BADGES:
        items.append({
            "id": item_id, "kind": "badge", "emoji": emoji, "name": name,
            "label": f"{emoji} {name}", "price": price, "coin_type": coin,
            "aliases": (name, name.removeprefix("نشان ").strip(), emoji),
        })
    for level, name, price in _STARS:
        items.append({
            "id": f"star_{level}", "kind": "star", "emoji": "⭐" * level,
            "name": name, "label": f"{'⭐' * level} {name}", "level": level,
            "price": price, "coin_type": "silver",
            "aliases": (name, f"سطح {level}", f"ستاره {level}", "⭐" * level),
        })
    for item_id, emoji, title, alias in _TITLES:
        items.append({
            "id": item_id, "kind": "title", "emoji": emoji, "title": title,
            "name": title, "alias": alias, "label": f"{emoji} {title}",
            "price": 200, "coin_type": "bronze",
            "aliases": (title, alias, alias.replace(" ", "")),
        })
    return items


_ITEMS = _items()
_ITEM_BY_ID = {item["id"]: item for item in _ITEMS}


def _normalize(value: str) -> str:
    text = str(value or "")
    for source, target in (("\u200c", " "), ("\u200f", ""), ("\u200e", ""), ("\ufe0f", ""), ("ي", "ی"), ("ك", "ک")):
        text = text.replace(source, target)
    return " ".join(text.split())


def _english_digits(value: str) -> str:
    return str(value or "").translate(_DIGITS).strip()


def _fa(value: Any) -> str:
    return str(value).translate(_FA_DIGITS)


def _u16(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _format_entities(
    text: str,
    *,
    bold: tuple[str, ...] = (),
    blockquotes: tuple[str, ...] = (),
) -> list[Any]:
    try:
        from splusthon.tl import types
    except ImportError:
        return []
    entities: list[Any] = []
    seen: set[tuple[str, int, int]] = set()
    for entity_name, values in (
        ("MessageEntityBold", bold), ("MessageEntityBlockquote", blockquotes),
    ):
        entity_cls = getattr(types, entity_name, None)
        if entity_cls is None:
            continue
        for value in values:
            start = text.find(value)
            if start < 0:
                continue
            offset, length = _u16(text[:start]), _u16(value)
            identity = (entity_name, offset, length)
            if identity not in seen:
                entities.append(entity_cls(offset=offset, length=length))
                seen.add(identity)
    return entities


def _profile_entities(text: str, *, title: str, star_line: str) -> list[Any]:
    labels = (
        "👤 نام:", "🏷 لقب:", "🛡 نشان‌ها:", "🥉 برنز:", "🥈 نقره:",
        "🥇 طلا:", "📍 شهر:", "🎂 سن:", "🎮 برد:", "🏅 رتبه:",
    )
    menu_block = (
        "برای انتخاب، شماره گزینه را بفرستید:\n\n"
        "۱) 🛍 لیست آیتم ها و خرید\n"
        "۲) ✏️ ویرایش اطلاعات\n"
        "۰) بستن"
    )
    return _format_entities(
        text,
        bold=(title, *labels, star_line, menu_block),
        blockquotes=(star_line, menu_block),
    )


def _profile_key(actor_id: str | int) -> str:
    return f"{_PROFILE_PREFIX}{actor_id}"


def _session_key(actor_id: str | int) -> str:
    return f"{_SESSION_PREFIX}{actor_id}"


def _blank_profile() -> dict[str, Any]:
    return {
        "registered": False, "name": None, "city": None, "age": None,
        "nickname": None, "badges": [], "stars": 0, "titles": [],
    }


def _get_profile(store: BotStore, group_id: str | int, actor_id: str | int) -> dict[str, Any]:
    value = store.get_group_state(group_id, _profile_key(actor_id), {})
    profile = _blank_profile()
    if isinstance(value, dict):
        profile.update(value)
    for key in ("badges", "titles"):
        if not isinstance(profile.get(key), list):
            profile[key] = []
    try:
        profile["stars"] = max(0, min(7, int(profile.get("stars", 0) or 0)))
    except (TypeError, ValueError):
        profile["stars"] = 0
    return profile


def _get_session(store: BotStore, group_id: str | int, actor_id: str | int) -> dict[str, Any] | None:
    value = store.get_group_state(group_id, _session_key(actor_id))
    if not isinstance(value, dict):
        return None
    try:
        started = float(value.get("updated_at", 0))
    except (TypeError, ValueError):
        started = 0
    step = str(value.get("step", ""))
    profile_flow = (
        step.startswith("profile_")
        or step == "profile_menu"
        or value.get("return_to") in {"profile_buy", "profile_menu"}
    )
    timeout = 300 if profile_flow else 180
    if started <= 0 or time.time() - started > timeout:
        store.delete_group_state(group_id, _session_key(actor_id))
        return None
    return value


def _set_session(
    store: BotStore, group_id: str | int, actor_id: str | int,
    step: str, **values: Any,
) -> dict[str, Any]:
    session = {"step": step, "updated_at": time.time(), **values}
    store.set_group_state(group_id, _session_key(actor_id), session)
    return session


def _clear_session(store: BotStore, group_id: str | int, actor_id: str | int) -> None:
    store.delete_group_state(group_id, _session_key(actor_id))


def _restore_shop_buy(
    store: BotStore, group_id: str | int, actor_id: str | int,
    return_to: Any,
) -> dict[str, Any]:
    if str(return_to or "") in {"profile_buy", "profile_menu"}:
        return _set_session(
            store, group_id, actor_id, "shop_buy", return_to="profile_buy"
        )
    return _set_session(store, group_id, actor_id, "shop_buy")


def _event_message_id(event: Any) -> str | None:
    message = getattr(event, "message", None)
    value = getattr(message, "id", None) or getattr(event, "id", None)
    return str(value) if value is not None else None


def _display_name(event: Any, actor_id: str | int | None) -> str:
    sender = getattr(event, "sender", None)
    parts = [
        str(part).strip() for part in
        (getattr(sender, "first_name", None), getattr(sender, "last_name", None))
        if part and str(part).strip()
    ]
    username = str(getattr(sender, "username", None) or "").strip().lstrip("@")
    return " ".join(parts) or (f"@{username}" if username else str(actor_id or ""))


async def _send(
    event: Any,
    reply: ReplyCallback | None,
    text: str,
    *,
    formatting_entities: list[Any] | None = None,
) -> Any:
    if reply is not None:
        return await reply(event, text, formatting_entities=formatting_entities)
    method = getattr(event, "reply", None) or getattr(event, "respond", None)
    if not callable(method):
        raise TypeError("event has no supported reply method")
    kwargs = {"formatting_entities": formatting_entities} if formatting_entities else {}
    result = method(text, **kwargs)
    return await result if hasattr(result, "__await__") else result


def _wallet_text(store: BotStore, group_id: str | int, actor_id: str | int) -> str:
    profile = store.get_economy_profile(group_id, actor_id)
    rank = store.group_coin_rank(group_id, actor_id)
    config = _load_economy_settings()
    rank_text = _fa(rank) if rank else "ندارد"
    return (
        "💰 کیف پول شما\n\n"
        f"🥉 برنز: {_fa(profile['bronze'])}\n"
        f"🥈 نقره: {_fa(profile['silver'])}\n"
        f"🥇 طلا: {_fa(profile['gold'])}\n\n"
        f"💎 ارزش کل: {_fa(profile['total_coin_value'])}\n"
        f"🏆 رتبه: {rank_text}\n\n"
        "برای انتخاب، شمارهٔ گزینه را بفرستید:\n\n"
        f"۱) تبدیل برنز به نقره ({_fa(config['BronzeToSilverCost'])} ➜ {_fa(config['BronzeToSilverGain'])})\n"
        f"۲) تبدیل نقره به طلا ({_fa(config['SilverToGoldCost'])} ➜ {_fa(config['SilverToGoldGain'])})\n"
        "۳) انتقال برنز\n۴) انتقال نقره\n۵) انتقال طلا\n"
        "۶) تاریخچه تراکنش‌ها\n۷) جایزه روزانه\n۰) بستن"
    )


def _wallet_entities(text: str) -> list[Any]:
    labels = (
        "💰 کیف پول شما", "🥉 برنز:", "🥈 نقره:", "🥇 طلا:",
        "💎 ارزش کل:", "🏆 رتبه:",
    )
    return _format_entities(text, bold=labels)


def _duration_text(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{_fa(hours)} ساعت و {_fa(minutes)} دقیقه"
    if hours:
        return f"{_fa(hours)} ساعت"
    if minutes:
        return f"{_fa(minutes)} دقیقه"
    return "کمتر از یک دقیقه"


def _conversion_result(
    profile: dict[str, Any], from_coin: str, to_coin: str,
    cost: int, gain: int,
) -> str:
    labels = {"bronze": "🥉 برنز", "silver": "🥈 نقره", "gold": "🥇 طلا"}
    return (
        f"✅ {_fa(cost)} {_COIN_LABELS[from_coin]} به "
        f"{_fa(gain)} {_COIN_LABELS[to_coin]} تبدیل شد.\n\n"
        f"{labels[from_coin]}: {_fa(profile[from_coin])}\n"
        f"{labels[to_coin]}: {_fa(profile[to_coin])}\n"
        f"💎 ارزش کل: {_fa(profile['total_coin_value'])}"
    )


def _transfer_result(profile: dict[str, Any], amount: int, coin: str) -> str:
    return (
        f"✅ {_fa(amount)} {_COIN_LABELS[coin]} منتقل شد.\n\n"
        "موجودی شما:\n"
        f"🥉 {_fa(profile['bronze'])} | "
        f"🥈 {_fa(profile['silver'])} | "
        f"🥇 {_fa(profile['gold'])}\n"
        f"💎 ارزش کل: {_fa(profile['total_coin_value'])}"
    )


def _transfer_prompt(
    profile: dict[str, Any], coin: str, *, username: str | None = None,
) -> str:
    if username is None:
        return (
            f"📤 انتقال {_COIN_LABELS[coin]}\n\n"
            f"موجودی شما: {_fa(profile[coin])}\n\n"
            "یوزرنیم کاربر مقصد را ارسال کنید:\n"
            "مثال:\n"
            "@username\n\n"
            "برای لغو، ۰ بفرستید."
        )
    return (
        f"📤 انتقال {_COIN_LABELS[coin]} به @{username}\n\n"
        f"موجودی شما: {_fa(profile[coin])}\n\n"
        f"مقدار {_COIN_LABELS[coin]} برای انتقال را ارسال کنید:\n\n"
        "مثال:\n"
        "10\n\n"
        "برای لغو، ۰ بفرستید."
    )


def _buy_prompt() -> tuple[str, list[Any]]:
    text = (
        "🛍 خرید آیتم\n\n"
        f"شمارهٔ آیتم موردنظر را بفرستید (۱ تا {_fa(len(_ITEMS))}).\n"
        "برای لغو، ۰ بفرستید."
    )
    return text, _format_entities(text, bold=("🛍 خرید آیتم",))


async def _send_item_list(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    actor_id: str | int,
    reply: ReplyCallback | None,
    include_dynamic: bool = False,
) -> Any:
    text = _item_text(
        store, group_id, actor_id, include_dynamic=include_dynamic
    )
    return await _send(
        event, reply, text, formatting_entities=_format_entities(text, bold=(text,))
    )


def _item_text(
    store: BotStore,
    group_id: str | int,
    actor_id: str | int,
    *,
    include_dynamic: bool = False,
) -> str:
    profile = _get_profile(store, group_id, actor_id)
    owned_badges = set(profile["badges"])
    owned_titles = set(profile["titles"])
    lines = ["📦 لیست آیتم‌ها", "", "🛡 نشان‌ها"]
    for index, item in enumerate(_ITEMS, 1):
        if item["kind"] == "star":
            owned = int(profile["stars"]) >= int(item["level"])
            price_coin = "نقره"
        else:
            owned = item["id"] in (owned_badges if item["kind"] == "badge" else owned_titles)
            price_coin = _COIN_LABELS[item["coin_type"]]
        if item["kind"] == "star" and index == 11:
            lines += ["", "⭐ خرید سطح"]
        if item["kind"] == "title" and index == 18:
            lines += ["", "🏷 خرید لقب اختصاصی", "قیمت همه لقب‌ها: ۲۰۰ برنز"]
        label = item.get("title") or item["name"]
        emoji = item.get("emoji", "")
        if item["kind"] == "star":
            emoji = "⭐" * int(item["level"])
        lines.append(
            f"{'✅ ' if owned else ''}{_fa(index)}) {emoji} {label}"
            if item["kind"] == "title"
            else f"{'✅ ' if owned else ''}{_fa(index)}) {emoji} {label} — {_fa(item['price'])} {price_coin}"
        )
    lines += ["", "برای خرید، شمارهٔ آیتم را بفرستید."]
    dynamic_items = shop_catalog.list_items(store) if include_dynamic else []
    if dynamic_items:
        lines += ["", "🛒 آیتم‌های ویژه", ""]
        for item in dynamic_items:
            stock = item.get("stock")
            stock_text = (
                f"\n📦 موجودی: {_fa(stock)}" if stock is not None and stock > 0
                else "\n📦 ناموجود" if stock is not None else ""
            )
            description = f"\n{item['description']}" if item.get("description") else ""
            coin = _COIN_LABELS.get(str(item.get("coin_type")), "")
            lines.append(
                f"🔖 {item['title']}\n🆔 {item['id']}\n"
                f"💵 {_fa(item['price'])} {coin}{description}{stock_text}"
            )
    return "\n".join(lines)


def _shop_entry(store: BotStore, group_id: str | int, actor_id: str | int) -> tuple[str, list[Any]]:
    profile = store.get_economy_profile(group_id, actor_id)
    item_count = len(_ITEMS) + len(shop_catalog.list_items(store))
    header = "🛒 فروشگاه"
    menu_block = (
        "برای انتخاب، شماره گزینه را بفرستید:\n\n"
        "۱) 🛍 لیست آیتم ها و خرید\n"
        "۰) بستن"
    )
    menu = (
        f"{header}\n\n"
        "موجودی شما:\n"
        f"🥉 {_fa(profile['bronze'])} | 🥈 {_fa(profile['silver'])} | "
        f"🥇 {_fa(profile['gold'])}\n"
        f"💎 ارزش کل: {_fa(profile['total_coin_value'])}\n\n"
        f"آیتم‌های موجود: {_fa(item_count)}\n\n"
        f"{menu_block}"
    )
    items = _item_text(store, group_id, actor_id, include_dynamic=True)
    text = f"{menu}\n\n{items}"
    entities = _format_entities(
        text,
        bold=(header, "💎 ارزش کل:", menu_block, items),
        blockquotes=(menu_block,),
    )
    return text, entities


def _resolve_item(value: str) -> dict[str, Any] | None:
    normalized = _normalize(value).translate(_DIGITS).lower()
    if normalized.isdigit():
        index = int(normalized)
        return _ITEMS[index - 1] if 1 <= index <= len(_ITEMS) else None
    for item in _ITEMS:
        candidates = {
            str(item["id"]).lower(), str(item["label"]).lower(),
            str(item["name"]).lower(),
            *(str(alias).lower() for alias in item.get("aliases", ())),
        }
        if item.get("title"):
            candidates.add(str(item["title"]).lower())
        if item.get("alias"):
            candidates.add(str(item["alias"]).lower())
        if normalized in candidates:
            return item
    return None


def _format_history(store: BotStore, group_id: str | int, actor_id: str | int) -> str:
    # A conversion is two ledger rows locally (debit + credit), but one source
    # transaction. Read extra rows so a pair at the limit is never split.
    rows = store.economy_transaction_history(group_id, actor_id, limit=40)
    if not rows:
        return "🧾 تاریخچه تراکنش‌ها\n\nهنوز تراکنشی ثبت نشده است."

    labels = {
        "transfer_in": "📥 دریافت انتقال", "transfer_out": "📤 ارسال انتقال",
        "daily": "📅 جایزه روزانه", "debit": "➖ خرج", "credit": "➕ دریافت",
        "purchase": "🛒 خرید",
    }
    game_labels = {
        "riddle": "حدس چیستان", "maemma": "معما", "emoji": "حدس ایموجی",
        "flag": "حدس پرچم", "name_family": "اسم فامیل",
        "correction": "تصحیح کلمات", "quiz": "چهار گزینه‌ای",
        "fill_blank": "جای خالی", "laugh_or_lose": "بخند یا بباز",
        "lucky_box": "جعبه شانسی", "best_answer": "بهترین جواب",
        "battle": "نبرد", "sentence_guess": "ساخت جمله",
        "minesweeper": "مین یاب", "who_knows": "کی بیشتر بلده",
        "truth_lie": "دروغ یا حقیقت", "karagah": "کارگاه",
        "karagah_thief": "کارگاه — دزد", "survival": "بقا",
        "survival_step": "بقا — پاسخ صحیح", "vampire": "خون‌آشام",
    }

    actions: list[dict[str, Any]] = []
    conversion_indexes: dict[str, int] = {}
    daily_indexes: dict[str, int] = {}
    for row in rows:
        note = str(row.get("note") or "")
        reference = str(row.get("reference") or "")
        if note == "daily" and reference.startswith("daily:"):
            suffix = reference.rsplit(":", 1)[-1]
            base_reference = (
                reference.rsplit(":", 1)[0]
                if suffix in {"bronze", "silver", "gold"}
                else reference
            )
            index = daily_indexes.get(base_reference)
            if index is None:
                actions.append({
                    "type": "daily", "reference": base_reference,
                    "changes": {}, "created_at": row.get("created_at"),
                })
                index = len(actions) - 1
                daily_indexes[base_reference] = index
            actions[index]["changes"][str(row.get("coin_type"))] = int(
                row.get("amount", 0) or 0
            )
            continue
        if note.startswith("conversion:") and reference.endswith((":debit", ":credit")):
            base_reference = reference.rsplit(":", 1)[0]
            index = conversion_indexes.get(base_reference)
            if index is None:
                actions.append({
                    "type": "conversion", "reference": base_reference,
                    "changes": {}, "created_at": row.get("created_at"),
                })
                index = len(actions) - 1
                conversion_indexes[base_reference] = index
            suffix = reference.rsplit(":", 1)[-1]
            sign = -1 if suffix == "debit" else 1
            actions[index]["changes"][str(row.get("coin_type"))] = (
                sign * int(row.get("amount", 0) or 0)
            )
            continue
        actions.append({"type": "single", **row})

    lines = ["🧾 تاریخچه تراکنش‌ها", ""]
    for action in actions[:10]:
        when = str(action.get("created_at") or "")[:16].replace("T", " ")
        if action["type"] == "daily":
            changes = []
            for coin, amount in sorted(
                action["changes"].items(),
                key=lambda item: (item[1] < 0, _COIN_ORDER.get(item[0], 99)),
            ):
                if not amount:
                    continue
                sign = "−" if amount < 0 else "+"
                changes.append(
                    f"{_COIN_LABELS.get(coin, coin)} {sign}{_fa(abs(amount))}"
                )
            detail = " ، ".join(changes) or "بدون تغییر در موجودی"
            lines.append(f"📅 جایزه روزانه: {detail}\n🕐 {when}")
            continue
        if action["type"] == "conversion":
            changes = []
            for coin, amount in sorted(
                action["changes"].items(), key=lambda item: item[1] >= 0,
            ):
                sign = "−" if amount < 0 else "+"
                changes.append(
                    f"{_COIN_LABELS.get(coin, coin)} {sign}{_fa(abs(amount))}"
                )
            lines.append(f"🔄 تبدیل: {' ، '.join(changes)}\n🕐 {when}")
            continue

        note = str(action.get("note") or "")
        kind = note
        detail = ""
        if note.startswith("purchase:"):
            kind = "purchase"
            detail = f" — {note.split(':', 1)[1]}"
        elif note.startswith("game:"):
            game = note.split(":", 1)[1]
            detail = f" — {game_labels.get(game, game)}"
            kind = "reward"
        elif note.startswith(("ورود به سایت", "دانلود عکس", "مین یاب")):
            kind = "debit"
            detail = f" — {note}"
        label = labels.get(kind, "🎁 جایزه")
        is_spend = (
            kind in {"transfer_out", "debit", "purchase"}
            or note.startswith(("purchase:", "ورود به سایت"))
        )
        direction = "−" if is_spend else "+"
        amount = _fa(action.get("amount", 0))
        coin = _COIN_LABELS.get(
            str(action.get("coin_type")), str(action.get("coin_type"))
        )
        lines.append(f"{label}: {coin} {direction}{amount}{detail}\n🕐 {when}")
    return "\n\n".join(lines)


def _confirmation(item: dict[str, Any]) -> str:
    coin = _COIN_LABELS[item["coin_type"]]
    label = item.get("label") or item.get("title") or item.get("id")
    return (
        f"🛍 {label}\n"
        f"💵 قیمت: {_fa(item['price'])} {coin}\n\n"
        "آیا از خرید این آیتم مطمئن هستید؟\n\n"
        "✅ تایید\n❌ لغو"
    )


def _confirmation_entities(item: dict[str, Any], text: str) -> list[Any]:
    label = str(item.get("label") or item.get("title") or item.get("id"))
    return _format_entities(
        text,
        bold=(label, "💵 قیمت:", "آیا از خرید این آیتم مطمئن هستید؟", "✅ تایید", "❌ لغو"),
    )


def _is_yes(value: str) -> bool:
    return _normalize(value).lower() in {"1", "بله", "آره", "اره", "تایید", "تأیید", "ok", "yes", "✅"}


def _is_no(value: str) -> bool:
    return _normalize(value).lower() in {"0", "لغو", "نه", "خیر", "cancel", "no", "❌"}


def _render_profile(
    store: BotStore,
    group_id: str | int,
    actor_id: str | int,
    event: Any,
) -> tuple[str, list[Any]]:
    profile = _get_profile(store, group_id, actor_id)
    balance = store.get_economy_profile(group_id, actor_id)
    rank = store.group_coin_rank(group_id, actor_id)
    sender = getattr(event, "sender", None)
    username = str(getattr(sender, "username", None) or "").strip().lstrip("@")
    display = " ".join(
        str(part).strip()
        for part in (getattr(sender, "first_name", None), getattr(sender, "last_name", None))
        if part and str(part).strip()
    )
    nickname = str(profile.get("nickname") or "").strip()
    name = str(profile.get("name") or display or (f"@{username}" if username else "—"))
    title = nickname or (f"@{username}" if username else "") or display or str(profile.get("name") or "") or "کاربر"
    badges = [
        str(item.get("emoji", ""))
        for item_id in profile["badges"]
        if (item := _ITEM_BY_ID.get(str(item_id)))
    ]
    if badges:
        title = f"{badges[0]} {title} {badges[0]}"
    badge_line = " ".join(badges) or "ندارد"
    stars = max(0, min(7, int(profile.get("stars", 0) or 0)))
    star_line = "⭐ سطح: " + ("★" * stars + "☆" * (7 - stars))
    age = f"{_fa(profile['age'])} سال" if profile.get("age") else "—"
    lines = [
        "╔════════════════════╗", title, "╚════════════════════╝", "",
        f"👤 نام: {name}",
    ]
    if nickname:
        lines.append(f"🏷 لقب: {nickname}")
    lines += [
        "", "🛡 نشان‌ها:", badge_line, "", star_line, "",
        f"🥉 برنز: {_fa(balance['bronze'])}",
        f"🥈 نقره: {_fa(balance['silver'])}",
        f"🥇 طلا: {_fa(balance['gold'])}", "",
        f"📍 شهر: {profile.get('city') or '—'}",
        f"🎂 سن: {age}", "",
        f"🎮 برد: {_fa(balance['wins'])}",
        f"🏅 رتبه: {('#' + _fa(rank)) if rank else 'ندارد'}",
    ]
    menu_block = (
        "برای انتخاب، شماره گزینه را بفرستید:\n\n"
        "۱) 🛍 لیست آیتم ها و خرید\n"
        "۲) ✏️ ویرایش اطلاعات\n"
        "۰) بستن"
    )
    card = "\n".join(lines)
    text = f"{card}\n\n{menu_block}"
    return text, _profile_entities(text, title=title, star_line=star_line)


async def _send_profile(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    actor_id: str | int,
    reply: ReplyCallback | None,
) -> Any:
    text, entities = _render_profile(
        store, group_id, actor_id, event
    )
    return await _send(event, reply, text, formatting_entities=entities)


def _validate_field(
    field: str,
    value: str,
    profile: dict[str, Any],
    word_filter: WordFilter | None = None,
) -> tuple[Any | None, str | None]:
    cleaned = _normalize(value)
    if field in {"name", "city", "nickname"}:
        if field == "nickname" and (
            cleaned.translate(_DIGITS) == "0"
            or cleaned.lower() in {"ندارم", "رد", "بیخیال", "بی خیال", "خالی", "-"}
        ):
            return None, None
        if not cleaned and field == "nickname":
            return None, None
        maximum = 32
        if len(cleaned) > maximum:
            label = {"name": "اسم", "city": "شهر", "nickname": "لقب"}[field]
            return None, f"❌ {label} نباید بیشتر از {maximum} نویسه باشد."
        if any(ord(char) < 32 for char in cleaned):
            return None, "❌ متن واردشده معتبر نیست."
        if (
            field in {"name", "nickname"}
            and word_filter is not None
            and word_filter.match_global(cleaned) is not None
        ):
            return None, "❌ استفاده از این اسم یا لقب مجاز نیست."
        if field == "nickname":
            for item_id, _emoji, title, alias in _TITLES:
                blocked_aliases = {
                    title.lower(), alias.lower(), alias.replace(" ", "").lower(),
                }
                if cleaned.lower() in blocked_aliases and item_id not in profile["titles"]:
                    return None, "❌ این لقب از آیتم‌های فروشگاه است؛ برای استفاده باید آن را بخرید."
        return cleaned or None, None
    if field == "age":
        digits = cleaned.translate(_PROFILE_AGE_DIGITS).strip()
        if not digits.isdigit():
            return None, "❌ سن باید فقط عدد باشد."
        age = int(digits)
        if not 5 <= age <= 120:
            return None, "❌ سن باید بین ۵ و ۱۲۰ باشد."
        return age, None
    return None, "❌ مرحلهٔ ثبت نامعتبر است."


async def _handle_session(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    actor_id: str | int,
    value: str,
    reply: ReplyCallback | None,
    raw_value: str | None = None,
    word_filter: WordFilter | None = None,
) -> bool:
    state = _get_session(store, group_id, actor_id)
    if state is None:
        return False
    step = str(state.get("step", ""))
    numeric = _english_digits(value)
    profile = _get_profile(store, group_id, actor_id)
    if step == "wallet_menu":
        if numeric == "0":
            _clear_session(store, group_id, actor_id)
            await _send(event, reply, "بسته شد.")
            return True
        if numeric == "1" or numeric == "2":
            config = _load_economy_settings()
            from_coin, to_coin, cost, gain, note = (
                (
                    "bronze", "silver", config["BronzeToSilverCost"],
                    config["BronzeToSilverGain"], "bronze_to_silver",
                )
                if numeric == "1" else (
                    "silver", "gold", config["SilverToGoldCost"],
                    config["SilverToGoldGain"], "silver_to_gold",
                )
            )
            msg_id = _event_message_id(event) or uuid.uuid4().hex
            result, outcome = store.convert_group_coins(
                group_id, actor_id, from_coin, to_coin, cost, gain,
                reference=f"convert:{group_id}:{actor_id}:{msg_id}",
                display_name=_display_name(event, actor_id), note=f"conversion:{note}",
            )
            if outcome == "insufficient":
                current = int((result or {}).get(from_coin, 0) or 0)
                await _send(
                    event, reply,
                    f"❌ موجودی {_COIN_LABELS[from_coin]} کافی نیست: "
                    f"{_fa(current)} < {_fa(cost)}",
                )
            else:
                profile = result or store.get_economy_profile(group_id, actor_id)
                await _send(
                    event, reply,
                    _conversion_result(profile, from_coin, to_coin, cost, gain),
                )
            return True
        if numeric in {"3", "4", "5"}:
            coin = {"3": "bronze", "4": "silver", "5": "gold"}[numeric]
            balance = store.get_economy_profile(group_id, actor_id)
            _set_session(store, group_id, actor_id, "transfer_username", coin_type=coin)
            await _send(event, reply, _transfer_prompt(balance, coin))
            return True
        if numeric == "6":
            await _send(event, reply, _format_history(store, group_id, actor_id))
            return True
        if numeric == "7":
            config = _load_economy_settings()
            rewards = {
                "bronze": config["DailyRewardBronze"],
                "silver": config["DailyRewardSilver"],
                "gold": config["DailyRewardGold"],
            }
            result, outcome, wait = store.claim_group_daily_coins(
                group_id,
                actor_id,
                rewards["bronze"],
                now_epoch=time.time(),
                cooldown_seconds=config["DailyRewardCooldownSeconds"],
                display_name=_display_name(event, actor_id),
                rewards=rewards,
            )
            if outcome == "cooldown":
                await _send(
                    event, reply,
                    "⏳ جایزه روزانه را قبلاً دریافت کرده‌اید.\n\n"
                    f"نوبت بعدی: {_duration_text(wait)} دیگر",
                )
            else:
                labels = {"bronze": "برنز", "silver": "نقره", "gold": "طلا"}
                parts = [
                    f"{labels[coin]} +{_fa(amount)}"
                    for coin, amount in rewards.items() if amount > 0
                ]
                profile = result or store.get_economy_profile(group_id, actor_id)
                reward_text = "، ".join(parts) if parts else "بدون جایزه"
                message = (
                    "🎁 جایزه روزانه دریافت شد!\n\n"
                    f"{reward_text}\n\n"
                    f"💎 ارزش کل: {_fa(profile['total_coin_value'])}"
                )
                await _send(event, reply, message)
            return True
        return False

    if step == "transfer_username":
        if numeric == "0":
            _clear_session(store, group_id, actor_id)
            await _send(event, reply, "لغو شد.")
            return True
        raw = str(raw_value if raw_value is not None else value).strip()
        cleaned = _english_digits(raw).strip()
        if cleaned.isdigit():
            await _send(
                event, reply,
                "❌ آیدی عددی پذیرفته نمی‌شود.\n"
                "فقط یوزرنیم کاربر مقصد را بفرستید. مثال:\n@username",
            )
            return True
        username = raw
        for invisible in ("\u200c", "\u200f", "\u200e"):
            username = username.replace(invisible, "")
        username = username.strip().lstrip("@").strip().lower()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,31}", username):
            await _send(
                event, reply,
                "❌ یوزرنیم معتبر نیست.\n"
                "یوزرنیم باید با حرف انگلیسی شروع شود. مثال:\n@username",
            )
            return True
        target = store.find_group_user_by_username(group_id, username)
        if target is None:
            await _send(
                event, reply,
                f"❌ کاربری با یوزرنیم @{username} در این گروه پیدا نشد.\n"
                "کاربر مقصد باید حداقل یک پیام در این گروه فرستاده باشد.",
            )
            return True
        if str(target["user_id"]) == str(actor_id):
            await _send(event, reply, "❌ انتقال به خودتان ممکن نیست.")
            return True
        state = _set_session(
            store, group_id, actor_id, "transfer_amount",
            coin_type=state.get("coin_type"), target_id=str(target["user_id"]),
            username=username,
        )
        coin = str(state.get("coin_type"))
        balance = store.get_economy_profile(group_id, actor_id)
        await _send(
            event, reply,
            _transfer_prompt(balance, coin, username=username),
        )
        return True

    if step == "transfer_amount":
        if numeric == "0":
            _clear_session(store, group_id, actor_id)
            await _send(event, reply, "لغو شد.")
            return True
        if not numeric.isdigit() or int(numeric) <= 0:
            await _send(
                event, reply,
                "❌ مقدار باید یک عدد مثبت باشد. مثال:\n10",
            )
            return True
        amount = int(numeric)
        coin = str(state.get("coin_type"))
        target_id = str(state.get("target_id"))
        message_id = _event_message_id(event) or uuid.uuid4().hex
        reference = (
            f"transfer:{group_id}:{actor_id}:{target_id}:"
            f"{coin}:{amount}:{message_id}"
        )
        sender_profile, _receiver_profile, outcome = store.transfer_group_coins(
            group_id,
            actor_id,
            target_id,
            coin,
            amount,
            reference=reference,
            sender_name=_display_name(event, actor_id),
            receiver_name=(
                f"@{state['username']}" if state.get("username") else ""
            ),
        )
        _clear_session(store, group_id, actor_id)
        if outcome == "insufficient":
            balance = int((sender_profile or {}).get(coin, 0) or 0)
            await _send(
                event, reply,
                f"❌ موجودی {_COIN_LABELS[coin]} کافی نیست: "
                f"{_fa(balance)} < {_fa(amount)}",
            )
        else:
            profile = sender_profile or store.get_economy_profile(group_id, actor_id)
            await _send(event, reply, _transfer_result(profile, amount, coin))
        return True

    if step == "shop_menu":
        if numeric == "0":
            _clear_session(store, group_id, actor_id)
            await _send(event, reply, "بسته شد.")
            return True
        if numeric == "1":
            _set_session(store, group_id, actor_id, "shop_buy")
            prompt, entities = _buy_prompt()
            await _send(
                event, reply, prompt, formatting_entities=entities,
            )
            return True
        return False

    if step == "shop_buy":
        if numeric == "0":
            if state.get("return_to") in {"profile_buy", "profile_menu"}:
                _set_session(store, group_id, actor_id, "profile_menu")
                await _send(event, reply, "لغو شد.")
            else:
                _clear_session(store, group_id, actor_id)
                await _send(event, reply, "بسته شد.")
            return True
        item = _resolve_item(value)
        dynamic = False
        from_profile = state.get("return_to") in {"profile_buy", "profile_menu"}
        if item is None and not from_profile:
            item = shop_catalog.get_item(store, _normalize(value))
            dynamic = item is not None
        if item is None:
            await _send(event, reply, f"❌ چنین آیتمی نیست. شمارهٔ آیتم را از ۱ تا {_fa(len(_ITEMS))} بفرستید.")
            return True
        if dynamic:
            stock = item.get("stock")
            if stock is not None and stock <= 0:
                await _send(event, reply, "❌ موجودی این آیتم تمام شده است.")
                return True
        else:
            owned = (
                item["id"] in profile["badges"] if item["kind"] == "badge"
                else int(profile["stars"]) >= int(item["level"]) if item["kind"] == "star"
                else item["id"] in profile["titles"]
            )
            if owned:
                await _send(event, reply, "❌ این آیتم را قبلاً خریده‌اید.")
                return True
        balance = store.get_economy_profile(group_id, actor_id)
        if int(balance[item["coin_type"]]) < int(item["price"]):
            missing = int(item["price"]) - int(balance[item["coin_type"]])
            await _send(event, reply, f"❌ موجودی سکه کافی نیست؛ برای این آیتم به {_fa(missing)} {_COIN_LABELS[item['coin_type']]} دیگر نیاز دارید.")
            return True
        selected_message_id = _event_message_id(event) or uuid.uuid4().hex
        _set_session(
            store, group_id, actor_id, "shop_confirm", item_id=item["id"],
            dynamic=dynamic, selection_id=selected_message_id,
            return_to=state.get("return_to"),
        )
        confirmation = _confirmation(item)
        await _send(
            event, reply, confirmation,
            formatting_entities=_confirmation_entities(item, confirmation),
        )
        return True

    if step == "shop_confirm":
        dynamic = bool(state.get("dynamic"))
        item = (
            shop_catalog.get_item(store, str(state.get("item_id")))
            if dynamic else _ITEM_BY_ID.get(str(state.get("item_id")))
        )
        if item is None:
            _restore_shop_buy(
                store, group_id, actor_id, state.get("return_to")
            )
            await _send(event, reply, "❌ آیتم انتخاب‌شده پیدا نشد؛ دوباره انتخاب کنید.")
            return True
        if _is_no(value):
            _restore_shop_buy(
                store, group_id, actor_id, state.get("return_to")
            )
            await _send(event, reply, "❌ خرید لغو شد. هیچ سکه‌ای کسر نشد.")
            return True
        if not _is_yes(value):
            await _send(event, reply, "برای تایید ✅ یا ۱ و برای لغو ❌ یا ۰ بفرستید.")
            return True
        reference = f"shop:{group_id}:{actor_id}:{item['id']}:{state.get('selection_id')}"
        if dynamic:
            item, balance, outcome = store.buy_dynamic_shop_item(
                group_id, actor_id, str(item["id"]), reference=reference,
                display_name=_display_name(event, actor_id),
            )
        else:
            _profile, balance, outcome = store.buy_group_profile_item(
                group_id,
                actor_id,
                item,
                reference=reference,
                display_name=_display_name(event, actor_id),
            )
        _restore_shop_buy(store, group_id, actor_id, state.get("return_to"))
        if outcome == "insufficient":
            current = int((balance or {}).get(str(item["coin_type"]), 0) or 0)
            await _send(
                event, reply,
                f"❌ موجودی {_COIN_LABELS[str(item['coin_type'])]} کافی نیست: "
                f"{_fa(current)} < {_fa(item['price'])}",
            )
        elif outcome == "owned":
            await _send(event, reply, "❌ این آیتم را قبلاً خریده‌اید.")
        elif outcome == "out_of_stock":
            await _send(event, reply, "❌ موجودی این آیتم تمام شده است.")
        elif outcome == "not_found":
            await _send(event, reply, "❌ آیتم انتخاب‌شده دیگر در فروشگاه نیست.")
        elif dynamic:
            await _send(
                event, reply,
                f"✅ «{item['title']}» خریداری شد.\n\n"
                f"💵 پرداخت: {_fa(item['price'])} {_COIN_LABELS[item['coin_type']]}\n\n"
                f"موجودی شما:\n🥉 {_fa(balance['bronze'])} | "
                f"🥈 {_fa(balance['silver'])} | 🥇 {_fa(balance['gold'])}",
            )
        else:
            if item["kind"] == "badge":
                applied = f"🛡 نشان {item['emoji']} به پروفایل شما اضافه شد."
            elif item["kind"] == "star":
                stars = max(0, min(7, int((_profile or {}).get("stars", 0) or 0)))
                applied = f"⭐ سطح شما: {'★' * stars}{'☆' * (7 - stars)}"
            else:
                applied = f"🏷 لقب شما: {item['title']}"
            await _send(
                event, reply,
                f"✅ «{item['label']}» خریداری شد.\n\n"
                f"{applied}\n\n"
                f"💵 پرداخت: {_fa(item['price'])} {_COIN_LABELS[item['coin_type']]}\n\n"
                f"🥉 {_fa(balance['bronze'])} | "
                f"🥈 {_fa(balance['silver'])} | "
                f"🥇 {_fa(balance['gold'])}",
            )
        return True

    if step == "profile_name":
        saved, error = _validate_field("name", value, profile, word_filter)
        if error or not saved:
            await _send(event, reply, error or "❌ اسم نمی‌تواند خالی باشد.")
            return True
        state["draft"] = {"name": saved}
        _set_session(store, group_id, actor_id, "profile_city", draft=state["draft"])
        await _send(event, reply, "🏙 شهر خود را بفرستید:")
        return True

    if step == "profile_city":
        saved, error = _validate_field("city", value, profile)
        if error or not saved:
            await _send(event, reply, error or "❌ شهر نمی‌تواند خالی باشد.")
            return True
        draft = dict(state.get("draft") or {})
        draft["city"] = saved
        _set_session(store, group_id, actor_id, "profile_age", draft=draft)
        await _send(event, reply, "🎂 سن خود را بفرستید:")
        return True

    if step == "profile_age":
        saved, error = _validate_field("age", value, profile)
        if error:
            await _send(event, reply, error)
            return True
        draft = dict(state.get("draft") or {})
        draft["age"] = saved
        _set_session(store, group_id, actor_id, "profile_nickname", draft=draft)
        await _send(event, reply, "🏷 لقب خود را بفرستید. این مورد اختیاری است؛ برای رد شدن ۰ بفرستید.")
        return True

    if step == "profile_nickname":
        saved, error = _validate_field("nickname", value, profile, word_filter)
        if error:
            await _send(event, reply, error)
            return True
        updated = dict(profile)
        updated.update(dict(state.get("draft") or {}))
        updated.update({"nickname": saved, "registered": True})
        store.set_group_state(group_id, _profile_key(actor_id), updated)
        _set_session(store, group_id, actor_id, "profile_menu")
        await _send(event, reply, "✅ پروفایل شما ثبت شد.")
        await _send_profile(
            event, store, group_id=group_id, actor_id=actor_id, reply=reply,
        )
        return True

    if step == "profile_menu":
        if numeric == "0":
            _clear_session(store, group_id, actor_id)
            await _send(event, reply, "بسته شد.")
            return True
        if numeric == "1":
            _set_session(
                store, group_id, actor_id, "shop_buy", return_to="profile_buy"
            )
            await _send_item_list(
                event, store, group_id=group_id, actor_id=actor_id,
                reply=reply, include_dynamic=False,
            )
            prompt, entities = _buy_prompt()
            await _send(event, reply, prompt, formatting_entities=entities)
            return True
        if numeric == "2":
            _set_session(store, group_id, actor_id, "profile_edit_choose")
            await _send(event, reply, "✏️ ویرایش اطلاعات\n\n۱) اسم\n۲) شهر\n۳) سن\n۴) لقب\n۰) بازگشت")
            return True
        return False

    if step == "profile_edit_choose":
        if numeric == "0":
            _set_session(store, group_id, actor_id, "profile_menu")
            await _send_profile(
                event, store, group_id=group_id, actor_id=actor_id, reply=reply
            )
            return True
        fields = {
            "1": ("name", "اسم"), "2": ("city", "شهر"),
            "3": ("age", "سن"), "4": ("nickname", "لقب"),
        }
        selection = fields.get(numeric)
        if selection is None:
            # The reference step passes unrecognized input to later handlers.
            return False
        field, label = selection
        _set_session(
            store, group_id, actor_id, "profile_edit_value",
            field=field, field_label=label,
        )
        await _send(event, reply, f"✏️ مقدار جدید «{label}» را بفرستید:")
        return True

    if step == "profile_edit_value":
        field = str(state.get("field") or "")
        saved, error = _validate_field(field, value, profile, word_filter)
        if error or (field != "nickname" and saved is None):
            await _send(event, reply, error or "❌ مقدار نمی‌تواند خالی باشد.")
            return True
        profile[field] = saved
        store.set_group_state(group_id, _profile_key(actor_id), profile)
        _set_session(store, group_id, actor_id, "profile_menu")
        await _send(event, reply, "✅ ذخیره شد.")
        await _send_profile(
            event, store, group_id=group_id, actor_id=actor_id, reply=reply,
        )
        return True

    return False


async def handle_economy_command(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    actor_id: str | int,
    text: str,
    reply: ReplyCallback | None = None,
    word_filter: WordFilter | None = None,
) -> bool:
    value = _normalize(text)
    register_commands = {"ثبت پرفایل", "ثبت پروفایل"}
    show_commands = {"پرفایلم", "پروفایلم"}
    delete_commands = {"حذف پرفایل", "حذف پروفایل"}
    if value in register_commands | show_commands | delete_commands | {
        "موجودی", "کیف پول", "انتقال سکه", "فروشگاه"
    }:
        display_name = _display_name(event, None)
        if display_name:
            store.set_economy_display_name(group_id, actor_id, display_name)
    if value in register_commands:
        _clear_session(store, group_id, actor_id)
        profile = _get_profile(store, group_id, actor_id)
        if profile.get("registered"):
            _set_session(store, group_id, actor_id, "profile_menu")
            await _send(event, reply, "ℹ️ پروفایل شما قبلاً ثبت شده است. برای دیدن آن «پرفایلم» و برای حذف «حذف پرفایل» را بفرستید.")
        else:
            _set_session(store, group_id, actor_id, "profile_name")
            await _send(event, reply, "👤 ثبت پروفایل\n\nاسم خود را بفرستید:")
        return True
    if value in delete_commands:
        _clear_session(store, group_id, actor_id)
        profile = _get_profile(store, group_id, actor_id)
        if not profile.get("registered"):
            await _send(event, reply, "❌ هنوز پروفایلی ثبت نکرده‌اید. برای ثبت «ثبت پرفایل» را بفرستید.")
        else:
            profile.update({"registered": False, "name": None, "city": None, "age": None, "nickname": None})
            store.set_group_state(group_id, _profile_key(actor_id), profile)
            await _send(event, reply, "🗑 پروفایل شما حذف شد.\n\nآیتم‌های خریداری‌شده و سکه‌هایتان دست‌نخورده باقی می‌مانند.\nبرای ثبت دوباره «ثبت پرفایل» را بفرستید.")
        return True
    if value in show_commands:
        _clear_session(store, group_id, actor_id)
        profile = _get_profile(store, group_id, actor_id)
        if not profile.get("registered"):
            await _send(event, reply, "❌ هنوز پروفایلی ثبت نکرده‌اید. برای ثبت «ثبت پرفایل» را بفرستید.")
        else:
            _set_session(store, group_id, actor_id, "profile_menu")
            await _send_profile(
                event, store, group_id=group_id, actor_id=actor_id, reply=reply
            )
        return True
    if value in {"موجودی", "کیف پول", "انتقال سکه"}:
        _set_session(store, group_id, actor_id, "wallet_menu")
        wallet = _wallet_text(store, group_id, actor_id)
        if value == "انتقال سکه":
            wallet += "\n\nبرای انتقال سکه، گزینهٔ ۳، ۴ یا ۵ را انتخاب کنید."
        await _send(
            event, reply, wallet,
            formatting_entities=_wallet_entities(wallet),
        )
        return True
    if value == "فروشگاه":
        _set_session(store, group_id, actor_id, "shop_menu")
        text_out, entities = _shop_entry(store, group_id, actor_id)
        await _send(event, reply, text_out, formatting_entities=entities)
        return True
    return await _handle_session(
        event, store, group_id=group_id, actor_id=actor_id,
        value=value, reply=reply, raw_value=text, word_filter=word_filter,
    )

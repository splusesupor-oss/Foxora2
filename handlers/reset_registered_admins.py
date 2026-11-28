"""Text-only owner workflow for resetting bot-registered group admins.

Only admins.json is modified. Never calls Soroush EditAdmin, kick, or native
role APIs; a native Soroush administrator remains a native administrator.
"""
from __future__ import annotations

import copy
import re
import time

from modules import admin_storage
from modules.group_id import normalize_group_id
from modules.group_storage import get_group_owner
from modules.owner_check import get_owner, is_global_owner
from modules.admin_help_builder import MessageEntityBold, MessageEntityBlockquote, _u16

COMMAND = "ریست ادمین ها"
TTL = 600
_sessions = {}  # (group, actor) -> {rows, selected, expires, confirming}
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_CHOOSE = re.compile(r"^(?:گزینه|گزينه|تایید گزینه|تأیید گزینه)\s+([0-9۰-۹٠-٩\s]+)$")
_REMOVE = re.compile(r"^لغو انتخاب\s+([0-9۰-۹٠-٩\s]+)$")


def normalize(text):
    return " ".join(str(text or "").replace("\u200c", " ").replace("ي", "ی")
                    .replace("ك", "ک").split())


def _key(chat_id, user_id):
    return normalize_group_id(chat_id), str(user_id)


def is_awaiting(chat_id, user_id):
    if not _sessions or chat_id is None or user_id is None:
        return False
    state = _sessions.get(_key(chat_id, user_id))
    return bool(state and state['expires'] > time.monotonic())


def _allowed(chat_id, user_id):
    if user_id is None:
        return False
    if is_global_owner(user_id):
        return True
    owner = get_group_owner(chat_id)
    return owner is not None and str(owner) == str(user_id)


def _protected(chat_id):
    owner = get_group_owner(chat_id)
    global_owner = get_owner().get('user_id')
    return {str(value) for value in (owner, global_owner) if value is not None}


def _eligible(chat_id, data=None):
    data = admin_storage.load_admins() if data is None else data
    protected = _protected(chat_id)
    rows = []
    seen = set()
    for entry in data.get(normalize_group_id(chat_id), []):
        if not isinstance(entry, dict):
            continue  # legacy username-only entries have no verifiable ID
        user_id = str(entry.get('user_id') or '')
        if not user_id.isdecimal() or int(user_id) <= 0 or user_id in protected or user_id in seen:
            continue
        seen.add(user_id)
        name = str(entry.get('name') or '').strip() or (
            '@' + str(entry.get('username') or '').strip().lstrip('@'))
        name = ' '.join(name.split())[:64].strip() or f'کاربر {user_id}'
        rows.append((user_id, name))
    return rows


def _entity(cls, text, part, *, start=0):
    pos = text.find(part, start)
    return cls(offset=_u16(text[:pos]), length=_u16(part)) if pos >= 0 else None


def _panel(state):
    rows = state['rows']
    lines = ['⚠️ : لیست ادمین های ثبت شده', '']
    for number, (user_id, name) in enumerate(rows, 1):
        marker = '☑️' if user_id in state['selected'] else '▫️'
        lines.append(f'{marker} {number}- {name} | شناسه: {user_id}')
    lines += [
        '',
        '🔺 اگر میخواهید فقط چند نفر را از ادمین ثبت شده خارج کنید گزینه هارو یکجا بنویسید',
        'گزینه ۱ ۲ ۳',
        '',
        '🔺 اگر میخواهید همه خارج شوند بنویسید',
        'تایید همه',
        '',
        'انتخاب همه | لغو انتخاب‌ها | لغو انتخاب ۱ | ریست ادمین‌های انتخاب‌شده | بازگشت',
    ]
    text = '\n'.join(lines)
    entities = [_entity(MessageEntityBold, text, lines[0])]
    for part in (lines[-7], 'گزینه ۱ ۲ ۳', lines[-4], 'تایید همه'):
        entities.append(_entity(MessageEntityBlockquote, text, part))
    for part in ('گزینه ۱ ۲ ۳', 'تایید همه'):
        entities.append(_entity(MessageEntityBold, text, part))
    return text, [e for e in entities if e is not None]


async def _show(event, state):
    text, entities = _panel(state)
    await event.reply(text, formatting_entities=entities)


def _numbers(raw, rows):
    parts = raw.translate(_DIGITS).split()
    if not parts or any(not value.isdigit() or int(value) < 1 or int(value) > len(rows)
                        for value in parts):
        return None
    return {rows[int(value) - 1][0] for value in parts}


def _invalidate(chat_id, ids):
    # This existing 45-second cache otherwise keeps removed admins authorized.
    from handlers.message_handler import ADMIN_PERMISSION_CACHE
    group = normalize_group_id(chat_id)
    for key in tuple(ADMIN_PERMISSION_CACHE):
        if key[0] == group and str(key[1]) in ids:
            ADMIN_PERMISSION_CACHE.pop(key, None)


def _reset(chat_id, ids):
    """One atomic save, fresh recheck, no partial writes and no native RPC."""
    data = copy.deepcopy(admin_storage.load_admins())
    gid = normalize_group_id(chat_id)
    eligible = dict(_eligible(chat_id, data))
    chosen = set(ids) & set(eligible)
    if not chosen:
        return []
    existing = data.get(gid, [])
    removed = [(user_id, eligible[user_id]) for user_id, _ in _eligible(chat_id, data)
               if user_id in chosen]
    usernames = {str(entry.get('username') or '').strip().lstrip('@').lower()
                 for entry in existing if isinstance(entry, dict)
                 and str(entry.get('user_id')) in chosen and entry.get('username')}
    data[gid] = [entry for entry in existing if not (
        (isinstance(entry, dict) and str(entry.get('user_id')) in chosen)
        or (isinstance(entry, str) and entry.strip().lstrip('@').lower() in usernames)
    )]
    admin_storage.save_admins(data)
    _invalidate(chat_id, chosen)
    return removed


async def handle(bot, event, chat_id, user_id, text):
    if chat_id is None or getattr(event, 'is_private', False):
        return False
    if (getattr(event, 'is_edit', False) or getattr(event, '_is_edited_message', False)
            or getattr(getattr(event, 'message', None), 'edit_date', None)):
        return False
    value = normalize(text)
    if value != COMMAND and not _sessions:
        return False
    key = _key(chat_id, user_id)
    state = _sessions.get(key)
    if value != COMMAND and not state:
        return False
    if not _allowed(chat_id, user_id):
        _sessions.pop(key, None)
        if value == COMMAND:
            await event.reply('⛔ فقط مالک اصلی ربات یا مالک ثبت‌شدهٔ همین گروه اجازهٔ ریست ادمین‌ها را دارد.')
            return True
        return False
    if value == COMMAND:
        rows = _eligible(chat_id)
        if not rows:
            _sessions.pop(key, None)
            await event.reply('ℹ️ ادمین ثبت‌شدهٔ قابل‌ریستی برای این گروه وجود ندارد.')
            return True
        state = {'rows': rows, 'selected': set(), 'confirming': False,
                 'expires': time.monotonic() + TTL}
        _sessions[key] = state
        await _show(event, state)
        return True
    if state['expires'] <= time.monotonic():
        _sessions.pop(key, None)
        return False
    if value in ('بازگشت', 'لغو', 'لغو ریست ادمین ها'):
        _sessions.pop(key, None)
        await event.reply('✅ ریست ادمین‌ها لغو شد؛ هیچ تغییری اعمال نشد.')
        return True
    if value in ('لغو انتخاب‌ها', 'لغو انتخاب ها'):
        state['selected'].clear()
        state['confirming'] = False
        await _show(event, state)
        return True
    if value == 'انتخاب همه':
        state['selected'] = {uid for uid, _ in state['rows']}
        state['confirming'] = False
        await _show(event, state)
        return True
    remove = _REMOVE.fullmatch(value)
    choose = _CHOOSE.fullmatch(value)
    if remove or choose:
        numbers = _numbers((remove or choose).group(1), state['rows'])
        if numbers is None:
            await event.reply('❌ شمارهٔ گزینه معتبر نیست؛ شماره‌های داخل پنل را وارد کنید.')
            return True
        if remove:
            state['selected'].difference_update(numbers)
            state['confirming'] = False
            await _show(event, state)
            return True
        state['selected'] = numbers
        state['confirming'] = True
        await event.reply(f'🔺 {len(numbers)} ادمین انتخاب شد. برای حذف نهایی بنویسید «تایید»؛ برای تغییر، گزینه‌های دیگری بفرستید یا «بازگشت» بنویسید.')
        return True
    if value == 'ریست ادمین های انتخاب شده':
        if not state['selected']:
            await event.reply('❌ ابتدا «گزینه ۱ ۲» یا «انتخاب همه» را بنویسید.')
            return True
        state['confirming'] = True
        await event.reply(f'🔺 حذف دسترسی ثبت‌شدهٔ {len(state["selected"])} ادمین؟ برای اجرای نهایی «تایید» را بنویسید؛ «بازگشت» یعنی لغو.')
        return True
    if value not in ('تایید همه', 'تأیید همه', 'تایید', 'تأیید',
                     'تایید ریست ادمین ها', 'تأیید ریست ادمین ها'):
        return False
    if value in ('تایید همه', 'تأیید همه'):
        chosen = {uid for uid, _ in state['rows']}
    else:
        if not state['confirming'] or not state['selected']:
            await event.reply('❌ ابتدا ادمین‌های موردنظر را انتخاب کنید.')
            return True
        chosen = set(state['selected'])
    try:
        removed = _reset(chat_id, chosen)
    except Exception as error:
        bot.logger.log_error(f'RESET REGISTERED ADMINS FAILED group={chat_id} error={error!r}')
        await event.reply('❌ ذخیره‌سازی انجام نشد؛ هیچ ادمینی ریست نشد. دوباره تلاش کنید.')
        return True
    _sessions.pop(key, None)
    if not removed:
        await event.reply('ℹ️ ادمین‌های انتخاب‌شده دیگر در فهرست ثبت‌شدهٔ این گروه نیستند؛ تغییری اعمال نشد.')
        return True
    names = '\n'.join(f'• {name} | شناسه: {uid}' for uid, name in removed)
    actor = getattr(event, 'sender', None)
    actor_label = ('@' + str(getattr(actor, 'username', '') or '').lstrip('@')
                   if getattr(actor, 'username', None) else
                   ' '.join(str(getattr(actor, 'first_name', '') or '').split())
                   or str(user_id))
    text = (f'🔺 ادمین های ثبت شده ریست شدند\n\nتعداد : « {len(removed)} نفر »\n\n'
            f'افراد ریست‌شده:\n{names}\n\nانجام دهنده : {actor_label}\n'
            'دسترسی ثبت‌شدهٔ ربات لغو شد؛ نقش واقعی سروش تغییر نکرد.')
    header = '🔺 ادمین های ثبت شده ریست شدند'
    await event.reply(text, formatting_entities=[
        _entity(MessageEntityBlockquote, text, header),
        _entity(MessageEntityBold, text, header),
    ])
    return True

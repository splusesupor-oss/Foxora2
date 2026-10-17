"""رفع اخراج‌های دائمیِ ثبت‌شده توسط سیستم ربات، بدون دست‌زدن به اخراج دستی."""


def _entry_source(entry):
    """منبع رکوردهای قدیمی و جدید را به‌صورت سازگار تشخیص می‌دهد."""
    if not isinstance(entry, dict):
        return "system"
    if entry.get("source"):
        return entry["source"]
    # رکوردهای قدیمیِ اخراج دستی پیش از افزوده‌شدن فیلد source نیز نباید
    # در فرمان ریست اخراجی‌ها وارد شوند.
    if entry.get("reason") == "اخراج دستی توسط مالک یا ادمین":
        return "manual"
    return "system"


def _entry_user_id(entry):
    return entry.get("user_id") if isinstance(entry, dict) else entry


def _participant_user_id(participant):
    user_id = getattr(participant, "user_id", None)
    if user_id is None:
        user_id = getattr(getattr(participant, "peer", None), "user_id", None)
    return user_id


async def _fetch_kicked_users(client, chat_peer, expected, logger):
    """Fetch kicked users in bounded pages instead of resolving every stored ID."""
    from splusthon import types
    from splusthon.tl import functions

    kicked = {}
    offset = 0
    limit = 200
    # Stop once all stored IDs were found, while still handling stale records.
    wanted = {str(item) for item in expected if item is not None}
    while True:
        result = await client(
            functions.channels.GetParticipantsRequest(
                channel=chat_peer,
                filter=types.ChannelParticipantsKicked(""),
                offset=offset,
                limit=limit,
                hash=0,
            )
        )
        users = {
            str(getattr(user, "id", "")): user
            for user in (getattr(result, "users", ()) or ())
            if getattr(user, "id", None) is not None
        }
        participants = list(getattr(result, "participants", ()) or ())
        for participant in participants:
            user_id = _participant_user_id(participant)
            if user_id is None:
                continue
            key = str(user_id)
            user = users.get(key)
            if user is not None:
                kicked[key] = user
        if len(participants) < limit or (wanted and wanted.issubset(kicked)):
            break
        offset += len(participants)
    logger.log_info(
        f"RESET REMOVED SNAPSHOT kicked={len(kicked)} pages={offset // limit + 1}"
    )
    return kicked


async def reset_system_removed_users(
    client, chat_id, entries, logger, resolved_chat=None
):
    """Unban recorded system removals without per-record entity lookups.

    The old implementation called ``get_entity`` and then the unsupported
    ``GetParticipantRequest`` for every stored ID. A large list therefore held
    the shared Soroush connection for minutes while producing one error every
    second. Soroush supports the filtered kicked-participant list, which also
    returns full user entities/access hashes. One bounded snapshot replaces all
    pre-check RPCs; only real current bans receive an unban RPC.
    """
    system_entries = [
        entry for entry in list(entries) if _entry_source(entry) == "system"
    ]
    expected_ids = [_entry_user_id(entry) for entry in system_entries]
    if not system_entries:
        return 0, list(entries)

    try:
        if resolved_chat is not None:
            from splusthon import utils
            chat_peer = utils.get_input_peer(resolved_chat)
        else:
            chat_peer = await client.get_input_entity(chat_id)
        if chat_peer is None:
            raise ValueError("resolved chat has no InputPeer")
        kicked_users = await _fetch_kicked_users(
            client, chat_peer, expected_ids, logger
        )
    except Exception as error:
        # One failure log and no destructive storage change. Never fan one
        # unsupported/cold-cache error into hundreds of entity RPCs.
        logger.log_error(
            f"خطا در دریافت فهرست اخراجی‌های گروه {chat_id}: {error}"
        )
        return 0, list(entries)

    released_ids = set()
    removable_entries = []
    for entry in system_entries:
        target_id = _entry_user_id(entry)
        if target_id is None:
            continue
        target = kicked_users.get(str(target_id))

        # Already released elsewhere: remove only the stale system record.
        if target is None:
            removable_entries.append(entry)
            continue

        try:
            # Full entity from GetParticipants carries the access hash, so this
            # conversion is local and does not repeat get_entity/GetUsers.
            await client.edit_permissions(
                chat_peer, target, until_date=None
            )
        except Exception as error:
            logger.log_error(f"خطا در رفع اخراجی واقعی {target_id}: {error}")
            continue

        removable_entries.append(entry)
        released_ids.add(str(getattr(target, "id", target_id)))

    remaining_entries = [
        entry for entry in entries if entry not in removable_entries
    ]
    return len(released_ids), remaining_entries

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self

from foxbot.economy_settings import coin_values
from foxbot.filters import (
    MAX_GROUP_WORD_LIST_BYTES,
    MAX_GROUP_WORD_TERM_LENGTH,
    MAX_GROUP_WORD_TERMS,
)

_SCHEMA_VERSION = 9
MAX_GROUP_HISTORY_USERS = 25
MAX_GROUP_HISTORY_RECORDS_PER_USER = 50
MAX_GROUP_AUDIT_EVENTS = 500
MAX_GROUP_COMMAND_EVENTS = (
    MAX_GROUP_HISTORY_USERS * MAX_GROUP_HISTORY_RECORDS_PER_USER
    + MAX_GROUP_AUDIT_EVENTS
)
_OWNER_SILVER_AMOUNT = 200_000
_MAX_OWNER_SILVER_GROUP_IDS = 64
_CHANNEL_ID_OFFSET = 1_000_000_000_000


def _economy_chat_key(group_id: str | int) -> str:
    """Normalize SPlus group-ID spellings for configured group-scoped bonuses."""
    try:
        value = int(group_id)
    except (TypeError, ValueError):
        return str(group_id)
    if value <= -_CHANNEL_ID_OFFSET:
        value = abs(value) - _CHANNEL_ID_OFFSET
    elif value <= -1_000_000_000 and str(value).startswith("-100"):
        value = abs(value) - 10_000_000_000
    elif value < 0:
        value = abs(value)
    return str(value)


def _economy_total(bronze: int, silver: int, gold: int) -> int:
    values = coin_values()
    return (
        int(bronze) * values["bronze"]
        + int(silver) * values["silver"]
        + int(gold) * values["gold"]
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _iso(value: datetime | str | int | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat(timespec="seconds")
    return str(value)


def _trim_group_command_events(db: sqlite3.Connection, group_id: str) -> None:
    db.execute(
        "DELETE FROM group_command_events WHERE group_id=? AND occurred_at < ?",
        (group_id, _iso(datetime.now(UTC) - timedelta(hours=24))),
    )
    db.execute(
        "WITH warning_users AS ("
        " SELECT user_key, last_seen, ROW_NUMBER() OVER "
        " (ORDER BY last_seen DESC, user_key) AS user_rank FROM ("
        "  SELECT COALESCE(NULLIF(user_id,''), NULLIF(display_name,''), 'unknown') AS user_key,"
        "  MAX(occurred_at) AS last_seen FROM group_command_events"
        "  WHERE group_id=? AND event_type='warning' GROUP BY user_key"
        " )"
        "), warning_rows AS ("
        " SELECT event_id, COALESCE(NULLIF(user_id,''), NULLIF(display_name,''), 'unknown') AS user_key,"
        " ROW_NUMBER() OVER (PARTITION BY COALESCE(NULLIF(user_id,''), NULLIF(display_name,''), 'unknown')"
        " ORDER BY occurred_at DESC,event_id DESC) AS event_rank"
        " FROM group_command_events WHERE group_id=? AND event_type='warning'"
        ") DELETE FROM group_command_events WHERE group_id=? AND event_type='warning'"
        " AND event_id IN (SELECT warning_rows.event_id FROM warning_rows"
        " JOIN warning_users USING(user_key) WHERE event_rank>? OR user_rank>?)",
        (
            group_id,
            group_id,
            group_id,
            MAX_GROUP_HISTORY_RECORDS_PER_USER,
            MAX_GROUP_HISTORY_USERS,
        ),
    )
    db.execute(
        "DELETE FROM group_command_events WHERE group_id=? AND event_type!='warning' "
        "AND event_id NOT IN (SELECT event_id FROM group_command_events "
        "WHERE group_id=? AND event_type!='warning' "
        "ORDER BY occurred_at DESC,event_id DESC LIMIT ?)",
        (group_id, group_id, MAX_GROUP_AUDIT_EVENTS),
    )


class BotStore:
    """Group-scoped durable state for mute, kick and the bot's active-ban index.

    Historical events are append-only from this module's point of view. Clearing
    the bot's active-ban index archives those rows and never invokes a platform
    unban operation.
    """

    def __init__(
        self,
        path: str | Path = "data/foxbot.sqlite3",
        *,
        global_owner_check: Callable[[str | int], bool] | None = None,
        owner_silver_group_ids: Iterable[str | int] = (),
    ) -> None:
        self.path = str(path)
        self._global_owner_check = global_owner_check
        normalized_groups: set[str] = set()
        for index, group_id in enumerate(owner_silver_group_ids):
            if index >= _MAX_OWNER_SILVER_GROUP_IDS:
                raise ValueError("at most 64 owner-silver group IDs are allowed")
            normalized = _economy_chat_key(group_id)
            if normalized:
                normalized_groups.add(normalized)
        self._owner_silver_group_ids = frozenset(normalized_groups)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(
            self.path,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.execute("PRAGMA busy_timeout=30000")
            if self.path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute("PRAGMA synchronous=NORMAL")
            self._migrate()

    def _migrate(self) -> None:
        version = int(self._db.execute("PRAGMA user_version").fetchone()[0])
        if version > _SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema {version} is newer than supported {_SCHEMA_VERSION}"
            )
        if version == _SCHEMA_VERSION:
            return
        if version == 1:
            self._db.executescript(
                "BEGIN IMMEDIATE;"
                "ALTER TABLE mute_state ADD COLUMN previous_until TEXT;"
                "PRAGMA user_version=2;"
                "COMMIT;"
            )
            version = 2
        if version == 2:
            self._db.executescript(
                "BEGIN IMMEDIATE;"
                "CREATE TABLE IF NOT EXISTS group_word_terms ("
                "term_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "group_id TEXT NOT NULL,"
                "term TEXT NOT NULL CHECK(length(trim(term)) > 0),"
                "created_by TEXT NOT NULL,"
                "created_at TEXT NOT NULL,"
                "UNIQUE(group_id, term));"
                "CREATE INDEX IF NOT EXISTS idx_group_word_terms_group "
                "ON group_word_terms(group_id, term_id);"
                "PRAGMA user_version=3;"
                "COMMIT;"
            )
            version = 3
        if version == 3:
            self._db.executescript(
                "BEGIN IMMEDIATE;"
                "CREATE TABLE IF NOT EXISTS group_filter_settings ("
                "group_id TEXT PRIMARY KEY,"
                "strict_words_enabled INTEGER NOT NULL DEFAULT 1 "
                "CHECK(strict_words_enabled IN (0,1)),"
                "updated_at TEXT NOT NULL);"
                "PRAGMA user_version=4;"
                "COMMIT;"
            )
            version = 4
        if version == 4:
            self._migrate_v5()
            return
        if version == 5:
            self._migrate_v6()
            return
        if version == 6:
            self._migrate_v7()
            return
        if version == 7:
            self._migrate_v8()
            return
        if version == 8:
            self._migrate_v9()
            return
        self._db.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS mute_state (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                username TEXT,
                reason TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                expires_at TEXT,
                actor_id TEXT,
                actor_kind TEXT NOT NULL,
                source TEXT NOT NULL,
                previous_rights_json TEXT,
                previous_until TEXT,
                active INTEGER NOT NULL CHECK (active IN (0, 1)),
                last_result TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (group_id, user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_mute_state_active_group
                ON mute_state(group_id, active, expires_at);

            CREATE TABLE IF NOT EXISTS mute_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                event_type TEXT NOT NULL CHECK (event_type IN ('mute', 'unmute')),
                occurred_at TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                actor_id TEXT,
                actor_kind TEXT NOT NULL,
                source TEXT NOT NULL,
                result TEXT NOT NULL,
                error_code TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_mute_events_group_time
                ON mute_events(group_id, occurred_at DESC);

            CREATE TABLE IF NOT EXISTS kick_events (
                event_key TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                user_id TEXT,
                display_name TEXT NOT NULL DEFAULT '',
                occurred_at TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                actor_id TEXT,
                actor_kind TEXT NOT NULL,
                source TEXT NOT NULL,
                result TEXT NOT NULL,
                membership_status TEXT NOT NULL DEFAULT 'unknown',
                ban_status TEXT NOT NULL DEFAULT 'unknown'
            );
            CREATE INDEX IF NOT EXISTS idx_kick_events_group_time
                ON kick_events(group_id, occurred_at DESC);

            CREATE TABLE IF NOT EXISTS active_bot_bans (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                username TEXT,
                reason TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'bot',
                created_at TEXT NOT NULL,
                PRIMARY KEY (group_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS bot_ban_archive (
                archive_id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                username TEXT,
                reason TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                archived_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ban_archive_group_time
                ON bot_ban_archive(group_id, archived_at DESC);

            CREATE TABLE IF NOT EXISTS group_owners (
                group_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS group_word_terms (
                term_id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                term TEXT NOT NULL CHECK(length(trim(term)) > 0),
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(group_id, term)
            );
            CREATE INDEX IF NOT EXISTS idx_group_word_terms_group
                ON group_word_terms(group_id, term_id);
            CREATE TABLE IF NOT EXISTS group_filter_settings (
                group_id TEXT PRIMARY KEY,
                strict_words_enabled INTEGER NOT NULL DEFAULT 1
                    CHECK(strict_words_enabled IN (0,1)),
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bulk_jobs (
                job_id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                query TEXT NOT NULL,
                match_mode TEXT NOT NULL,
                include_bio INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                examined INTEGER NOT NULL DEFAULT 0,
                matched INTEGER NOT NULL DEFAULT 0,
                succeeded INTEGER NOT NULL DEFAULT 0,
                already_done INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                scan_complete INTEGER NOT NULL DEFAULT 0,
                error_code TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_bulk_jobs_group_status
                ON bulk_jobs(group_id, status, created_at);
            CREATE TABLE IF NOT EXISTS bulk_targets (
                job_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                error_code TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (job_id, user_id),
                FOREIGN KEY (job_id) REFERENCES bulk_jobs(job_id) ON DELETE CASCADE
            );

            PRAGMA user_version=4;
            COMMIT;
            """
        )
        self._migrate_v5()

    def _migrate_v5(self) -> None:
        self._db.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS group_command_state (
                group_id TEXT NOT NULL,
                state_key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (group_id, state_key)
            );
            CREATE TABLE IF NOT EXISTS group_command_members (
                group_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                user_id TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                username TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (group_id, kind, user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_group_command_members_kind
                ON group_command_members(group_id,kind,created_at);
            CREATE TABLE IF NOT EXISTS group_warning_counts (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                warning_count INTEGER NOT NULL DEFAULT 0 CHECK(warning_count >= 0),
                updated_at TEXT NOT NULL,
                PRIMARY KEY(group_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS group_command_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                user_id TEXT,
                display_name TEXT NOT NULL DEFAULT '',
                actor_id TEXT,
                actor_display TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '',
                message_id TEXT,
                message_excerpt TEXT NOT NULL DEFAULT '',
                occurred_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_group_command_events_group_time
                ON group_command_events(group_id,occurred_at DESC,event_id DESC);
            CREATE TABLE IF NOT EXISTS group_stats (
                group_id TEXT PRIMARY KEY,
                messages INTEGER NOT NULL DEFAULT 0,
                deleted INTEGER NOT NULL DEFAULT 0,
                kicked INTEGER NOT NULL DEFAULT 0,
                muted INTEGER NOT NULL DEFAULT 0,
                members INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS group_user_stats (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                username TEXT,
                messages INTEGER NOT NULL DEFAULT 0,
                deleted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(group_id,user_id)
            );
            PRAGMA user_version=5;
            COMMIT;
            """
        )
        self._migrate_v6()

    def _migrate_v6(self) -> None:
        self._db.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS group_user_activity (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                first_seen REAL NOT NULL,
                last_seen REAL NOT NULL,
                gifs INTEGER NOT NULL DEFAULT 0 CHECK(gifs >= 0),
                videos INTEGER NOT NULL DEFAULT 0 CHECK(videos >= 0),
                PRIMARY KEY(group_id,user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_group_user_activity_recent
                ON group_user_activity(group_id,last_seen DESC);
            CREATE TABLE IF NOT EXISTS group_economy (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                bronze INTEGER NOT NULL DEFAULT 0 CHECK(bronze >= 0),
                silver INTEGER NOT NULL DEFAULT 0 CHECK(silver >= 0),
                gold INTEGER NOT NULL DEFAULT 0 CHECK(gold >= 0),
                wins INTEGER NOT NULL DEFAULT 0 CHECK(wins >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(group_id,user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_group_economy_rank
                ON group_economy(group_id,gold DESC,silver DESC,bronze DESC,updated_at);
            CREATE TABLE IF NOT EXISTS economy_transactions (
                transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                reference TEXT,
                coin_type TEXT NOT NULL CHECK(coin_type IN ('bronze','silver','gold')),
                amount INTEGER NOT NULL CHECK(amount > 0),
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(group_id,reference)
            );
            PRAGMA user_version=6;
            COMMIT;
            """
        )
        self._migrate_v7()

    def _migrate_v7(self) -> None:
        self._db.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS name_family_learning (
                category TEXT NOT NULL,
                letter TEXT NOT NULL,
                normalized_answer TEXT NOT NULL,
                answer TEXT NOT NULL,
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                observed_at REAL NOT NULL,
                PRIMARY KEY(category,letter,normalized_answer,group_id,user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_name_family_learning_answer
                ON name_family_learning(category,letter,normalized_answer);
            CREATE INDEX IF NOT EXISTS idx_name_family_learning_time
                ON name_family_learning(observed_at);
            PRAGMA user_version=7;
            COMMIT;
            """
        )
        self._migrate_v8()

    def _migrate_v8(self) -> None:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                "ALTER TABLE group_economy ADD COLUMN value_reached_seq INTEGER NOT NULL DEFAULT 0"
            )
            self._db.execute(
                "CREATE TABLE economy_rank_clock ("
                "singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
                "sequence INTEGER NOT NULL CHECK(sequence>=0))"
            )
            self._db.execute(
                "INSERT INTO economy_rank_clock(singleton,sequence) VALUES(1,0)"
            )
            rows = self._db.execute(
                "SELECT group_id,user_id,bronze,silver,gold FROM group_economy "
                "ORDER BY updated_at,group_id,user_id"
            ).fetchall()
            sequence = 0
            for row in rows:
                if _economy_total(
                    int(row["bronze"]), int(row["silver"]), int(row["gold"])
                ) <= 0:
                    continue
                sequence += 1
                self._db.execute(
                    "UPDATE group_economy SET value_reached_seq=? "
                    "WHERE group_id=? AND user_id=?",
                    (sequence, row["group_id"], row["user_id"]),
                )
            self._db.execute(
                "UPDATE economy_rank_clock SET sequence=? WHERE singleton=1",
                (sequence,),
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_group_economy_value_rank "
                "ON group_economy(group_id,value_reached_seq)"
            )
            self._db.execute("PRAGMA user_version=8")
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._migrate_v9()

    def _migrate_v9(self) -> None:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                "CREATE TABLE economy_transactions_v9 ("
                "transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "group_id TEXT NOT NULL,"
                "user_id TEXT NOT NULL,"
                "reference TEXT,"
                "coin_type TEXT NOT NULL CHECK(coin_type IN ('bronze','silver','gold')),"
                "amount INTEGER NOT NULL CHECK(amount >= 0),"
                "note TEXT NOT NULL DEFAULT '',"
                "created_at TEXT NOT NULL,"
                "UNIQUE(group_id,reference),"
                "CHECK(amount > 0 OR (amount = 0 AND note = 'daily')))"
            )
            self._db.execute(
                "INSERT INTO economy_transactions_v9 "
                "(transaction_id,group_id,user_id,reference,coin_type,amount,note,created_at) "
                "SELECT transaction_id,group_id,user_id,reference,coin_type,amount,note,created_at "
                "FROM economy_transactions"
            )
            self._db.execute("DROP TABLE economy_transactions")
            self._db.execute(
                "ALTER TABLE economy_transactions_v9 RENAME TO economy_transactions"
            )
            self._db.execute("PRAGMA user_version=9")
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

    @staticmethod
    def _group(group_id: str | int) -> str:
        return str(group_id)

    @staticmethod
    def _user(user_id: str | int) -> str:
        return str(user_id)

    def record_mute(
        self,
        *,
        group_id: str | int,
        user_id: str | int,
        display_name: str = "",
        username: str | None = None,
        reason: str = "",
        actor_id: str | int | None = None,
        actor_kind: str = "unknown",
        source: str = "bot",
        expires_at: datetime | str | None = None,
        previous_rights: dict[str, Any] | None = None,
        previous_until: datetime | str | int | None = None,
        succeeded: bool,
        error_code: str = "",
        occurred_at: datetime | str | None = None,
    ) -> None:
        gid, uid = self._group(group_id), self._user(user_id)
        ts = _iso(occurred_at) or _utc_now()
        expiry = _iso(expires_at)
        previous_until_value = _iso(previous_until)
        result = "success" if succeeded else "failed"
        previous_json = (
            json.dumps(previous_rights, ensure_ascii=False, separators=(",", ":"))
            if previous_rights is not None
            else None
        )
        with self._transaction() as db:
            db.execute(
                "INSERT INTO mute_events(group_id,user_id,display_name,event_type,occurred_at,"
                "reason,actor_id,actor_kind,source,result,error_code) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    gid, uid, display_name or "", "mute", ts, reason or "",
                    None if actor_id is None else str(actor_id), actor_kind, source,
                    result, error_code,
                ),
            )
            if succeeded:
                db.execute(
                    "INSERT INTO mute_state(group_id,user_id,display_name,username,reason,"
                    "started_at,expires_at,actor_id,actor_kind,source,previous_rights_json,previous_until,"
                    "active,last_result,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(group_id,user_id) DO UPDATE SET "
                    "display_name=excluded.display_name,username=excluded.username,"
                    "reason=excluded.reason,started_at=excluded.started_at,"
                    "expires_at=excluded.expires_at,actor_id=excluded.actor_id,"
                    "actor_kind=excluded.actor_kind,source=excluded.source,"
                    "previous_rights_json=excluded.previous_rights_json,"
                    "previous_until=excluded.previous_until,active=1,"
                    "last_result='success',updated_at=excluded.updated_at",
                    (
                        gid, uid, display_name or "", username, reason or "", ts, expiry,
                        None if actor_id is None else str(actor_id), actor_kind, source,
                        previous_json, previous_until_value, 1, "success", ts,
                    ),
                )

    def record_unmute(
        self,
        *,
        group_id: str | int,
        user_id: str | int,
        display_name: str = "",
        reason: str = "",
        actor_id: str | int | None = None,
        actor_kind: str = "unknown",
        source: str = "bot",
        succeeded: bool,
        error_code: str = "",
        occurred_at: datetime | str | None = None,
    ) -> None:
        gid, uid = self._group(group_id), self._user(user_id)
        ts = _iso(occurred_at) or _utc_now()
        result = "success" if succeeded else "failed"
        with self._transaction() as db:
            db.execute(
                "INSERT INTO mute_events(group_id,user_id,display_name,event_type,occurred_at,"
                "reason,actor_id,actor_kind,source,result,error_code) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    gid, uid, display_name or "", "unmute", ts, reason or "",
                    None if actor_id is None else str(actor_id), actor_kind, source,
                    result, error_code,
                ),
            )
            if succeeded:
                db.execute(
                    "UPDATE mute_state SET active=0,last_result='success',updated_at=? "
                    "WHERE group_id=? AND user_id=?",
                    (ts, gid, uid),
                )

    def list_active_mutes(
        self, group_id: str | int, *, now: datetime | str | None = None
    ) -> list[dict[str, Any]]:
        gid = self._group(group_id)
        current = _iso(now) or _utc_now()
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM mute_state WHERE group_id=? AND active=1 "
                "AND (expires_at IS NULL OR expires_at>?) ORDER BY started_at,user_id",
                (gid, current),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_mute_inactive(self, group_id: str | int, user_id: str | int) -> None:
        """Reconcile local active state after the platform confirms no mute remains."""
        with self._transaction() as db:
            db.execute(
                "UPDATE mute_state SET active=0,last_result='reconciled_inactive',updated_at=? "
                "WHERE group_id=? AND user_id=?",
                (_utc_now(), self._group(group_id), self._user(user_id)),
            )

    def get_mute_state(self, group_id: str | int, user_id: str | int) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM mute_state WHERE group_id=? AND user_id=?",
                (self._group(group_id), self._user(user_id)),
            ).fetchone()
        return dict(row) if row else None

    def mute_event_count(self, group_id: str | int, user_id: str | int | None = None) -> int:
        sql = "SELECT COUNT(*) FROM mute_events WHERE group_id=?"
        params: list[Any] = [self._group(group_id)]
        if user_id is not None:
            sql += " AND user_id=?"
            params.append(self._user(user_id))
        with self._lock:
            return int(self._db.execute(sql, params).fetchone()[0])

    def record_kick_event(
        self,
        *,
        event_key: str,
        group_id: str | int,
        user_id: str | int | None,
        display_name: str = "",
        reason: str = "",
        actor_id: str | int | None = None,
        actor_kind: str = "unknown",
        source: str = "observed",
        result: str = "success",
        membership_status: str = "unknown",
        ban_status: str = "unknown",
        occurred_at: datetime | str | None = None,
    ) -> bool:
        """Insert an observed event once; return False for an existing event key."""
        with self._transaction() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO kick_events(event_key,group_id,user_id,display_name,"
                "occurred_at,reason,actor_id,actor_kind,source,result,membership_status,ban_status) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(event_key), self._group(group_id),
                    None if user_id is None else self._user(user_id), display_name or "",
                    _iso(occurred_at) or _utc_now(), reason or "",
                    None if actor_id is None else str(actor_id), actor_kind, source,
                    result, membership_status, ban_status,
                ),
            )
            return cursor.rowcount == 1

    def list_kick_events(
        self,
        group_id: str | int,
        *,
        limit: int = 100,
        offset: int = 0,
        result: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit < 1 or offset < 0:
            raise ValueError("limit must be positive and offset cannot be negative")
        sql = "SELECT * FROM kick_events WHERE group_id=?"
        params: list[Any] = [self._group(group_id)]
        if result is not None:
            sql += " AND result=?"
            params.append(result)
        sql += " ORDER BY occurred_at DESC,event_key LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def list_mute_events(
        self,
        group_id: str | int,
        *,
        since: datetime | str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= MAX_GROUP_COMMAND_EVENTS:
            raise ValueError(
                f"limit must be between 1 and {MAX_GROUP_COMMAND_EVENTS}"
            )
        sql = "SELECT * FROM mute_events WHERE group_id=? AND result='success'"
        params: list[Any] = [self._group(group_id)]
        if since is not None:
            sql += " AND occurred_at>=?"
            params.append(_iso(since))
        sql += " ORDER BY occurred_at DESC,event_id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def add_bot_ban(
        self,
        *,
        group_id: str | int,
        user_id: str | int,
        display_name: str = "",
        username: str | None = None,
        reason: str = "",
        source: str = "bot",
        created_at: datetime | str | None = None,
    ) -> None:
        with self._transaction() as db:
            db.execute(
                "INSERT INTO active_bot_bans(group_id,user_id,display_name,username,reason,source,created_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(group_id,user_id) DO UPDATE SET "
                "display_name=excluded.display_name,username=excluded.username,reason=excluded.reason,"
                "source=excluded.source,created_at=excluded.created_at",
                (
                    self._group(group_id), self._user(user_id), display_name or "", username,
                    reason or "", source, _iso(created_at) or _utc_now(),
                ),
            )

    def remove_bot_ban(self, group_id: str | int, user_id: str | int) -> bool:
        with self._transaction() as db:
            cursor = db.execute(
                "DELETE FROM active_bot_bans WHERE group_id=? AND user_id=?",
                (self._group(group_id), self._user(user_id)),
            )
            return cursor.rowcount == 1

    def list_bot_bans(self, group_id: str | int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM active_bot_bans WHERE group_id=? ORDER BY created_at,user_id",
                (self._group(group_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def clear_bot_ban_list(self, group_id: str | int) -> int:
        """Archive and clear only the local active-ban index for one group."""
        gid = self._group(group_id)
        archived_at = _utc_now()
        with self._transaction() as db:
            count = int(
                db.execute("SELECT COUNT(*) FROM active_bot_bans WHERE group_id=?", (gid,)).fetchone()[0]
            )
            db.execute(
                "INSERT INTO bot_ban_archive(group_id,user_id,display_name,username,reason,source,created_at,archived_at) "
                "SELECT group_id,user_id,display_name,username,reason,source,created_at,? "
                "FROM active_bot_bans WHERE group_id=?",
                (archived_at, gid),
            )
            db.execute("DELETE FROM active_bot_bans WHERE group_id=?", (gid,))
        return count

    def list_archived_bot_bans(self, group_id: str | int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM bot_ban_archive WHERE group_id=? ORDER BY archived_at DESC,archive_id DESC",
                (self._group(group_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_group_owner(self, group_id: str | int, user_id: str | int) -> None:
        with self._transaction() as db:
            db.execute(
                "INSERT INTO group_owners(group_id,user_id,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(group_id) DO UPDATE SET user_id=excluded.user_id,updated_at=excluded.updated_at",
                (self._group(group_id), self._user(user_id), _utc_now()),
            )

    def get_group_owner(self, group_id: str | int) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT user_id FROM group_owners WHERE group_id=?",
                (self._group(group_id),),
            ).fetchone()
        return str(row[0]) if row else None

    def remove_group_owner(self, group_id: str | int) -> bool:
        with self._transaction() as db:
            cursor = db.execute(
                "DELETE FROM group_owners WHERE group_id=?",
                (self._group(group_id),),
            )
            return cursor.rowcount == 1

    def set_strict_words_enabled(
        self, group_id: str | int, enabled: bool
    ) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        with self._transaction() as db:
            db.execute(
                "INSERT INTO group_filter_settings(group_id,strict_words_enabled,updated_at) "
                "VALUES(?,?,?) ON CONFLICT(group_id) DO UPDATE SET "
                "strict_words_enabled=excluded.strict_words_enabled,"
                "updated_at=excluded.updated_at",
                (self._group(group_id), int(enabled), _utc_now()),
            )

    def strict_words_enabled(self, group_id: str | int) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT strict_words_enabled FROM group_filter_settings WHERE group_id=?",
                (self._group(group_id),),
            ).fetchone()
        return True if row is None else bool(row[0])

    def disabled_strict_word_groups(self) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT group_id FROM group_filter_settings "
                "WHERE strict_words_enabled=0 ORDER BY group_id"
            ).fetchall()
        return [str(row[0]) for row in rows]

    def add_group_word(
        self, group_id: str | int, term: str, actor_id: str | int
    ) -> bool:
        if not isinstance(term, str):
            raise TypeError("group word must be a string")
        if len(term) > MAX_GROUP_WORD_TERM_LENGTH:
            raise ValueError("group word term exceeds the configured length limit")
        cleaned = term.strip()
        if not cleaned:
            raise ValueError("group word cannot be empty")
        term_bytes = len(cleaned.encode("utf-8"))
        if term_bytes > MAX_GROUP_WORD_LIST_BYTES:
            raise ValueError("group word term exceeds the configured size limit")
        gid = self._group(group_id)
        with self._transaction() as db:
            exists = db.execute(
                "SELECT 1 FROM group_word_terms WHERE group_id=? AND term=?",
                (gid, cleaned),
            ).fetchone()
            if exists is not None:
                return False
            count, total_bytes = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(length(CAST(term AS BLOB))),0) "
                "FROM group_word_terms WHERE group_id=?",
                (gid,),
            ).fetchone()
            if int(count) >= MAX_GROUP_WORD_TERMS:
                raise ValueError("group word list exceeds the configured term limit")
            if int(total_bytes) + term_bytes > MAX_GROUP_WORD_LIST_BYTES:
                raise ValueError("group word list exceeds the configured size limit")
            cursor = db.execute(
                "INSERT INTO group_word_terms(group_id,term,created_by,created_at) "
                "VALUES(?,?,?,?)",
                (gid, cleaned, self._user(actor_id), _utc_now()),
            )
            return cursor.rowcount == 1

    def remove_group_word(self, group_id: str | int, term: str) -> bool:
        with self._transaction() as db:
            cursor = db.execute(
                "DELETE FROM group_word_terms WHERE group_id=? AND term=?",
                (self._group(group_id), term.strip()),
            )
            return cursor.rowcount == 1

    def replace_group_words(
        self, group_id: str | int, terms: list[str], actor_id: str | int
    ) -> int:
        if isinstance(terms, str):
            raise TypeError("group word terms must be a list of strings, not one string")
        cleaned: list[str] = []
        seen: set[str] = set()
        total_bytes = 0
        input_count = 0
        for term in terms:
            input_count += 1
            if input_count > MAX_GROUP_WORD_TERMS:
                raise ValueError("group word input exceeds the configured term limit")
            if not isinstance(term, str):
                raise TypeError("group word terms must be strings")
            if len(term) > MAX_GROUP_WORD_TERM_LENGTH:
                raise ValueError("group word term exceeds the configured length limit")
            value = term.strip()
            if not value or value in seen:
                continue
            total_bytes += len(value.encode("utf-8"))
            if total_bytes > MAX_GROUP_WORD_LIST_BYTES:
                raise ValueError("group word list exceeds the configured size limit")
            if len(cleaned) >= MAX_GROUP_WORD_TERMS:
                raise ValueError("group word list exceeds the configured term limit")
            cleaned.append(value)
            seen.add(value)
        gid, actor, timestamp = self._group(group_id), self._user(actor_id), _utc_now()
        with self._transaction() as db:
            db.execute("DELETE FROM group_word_terms WHERE group_id=?", (gid,))
            db.executemany(
                "INSERT INTO group_word_terms(group_id,term,created_by,created_at) "
                "VALUES(?,?,?,?)",
                [(gid, term, actor, timestamp) for term in cleaned],
            )
        return len(cleaned)

    def list_group_words(self, group_id: str | int) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT term FROM group_word_terms WHERE group_id=? ORDER BY term_id",
                (self._group(group_id),),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def list_group_word_groups(self) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT DISTINCT group_id FROM group_word_terms ORDER BY group_id"
            ).fetchall()
        return [str(row[0]) for row in rows]

    def create_bulk_job(
        self,
        *,
        job_id: str,
        group_id: str | int,
        actor_id: str | int,
        operation: str,
        query: str,
        match_mode: str,
        include_bio: bool,
        created_at: datetime | str,
        expires_at: datetime | str,
    ) -> None:
        with self._transaction() as db:
            db.execute(
                "INSERT INTO bulk_jobs(job_id,group_id,actor_id,operation,query,match_mode,include_bio,"
                "status,created_at,expires_at) VALUES(?,?,?,?,?,?,?,'scanning',?,?)",
                (
                    job_id, self._group(group_id), self._user(actor_id), operation, query,
                    match_mode, int(include_bio), _iso(created_at), _iso(expires_at),
                ),
            )

    def add_bulk_target(self, job_id: str, user_id: str | int, display_name: str) -> bool:
        with self._transaction() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO bulk_targets(job_id,user_id,display_name,status,updated_at) "
                "VALUES(?,?,?,'pending',?)",
                (job_id, self._user(user_id), display_name or "", _utc_now()),
            )
            if cursor.rowcount:
                db.execute(
                    "UPDATE bulk_jobs SET matched=matched+1 WHERE job_id=?",
                    (job_id,),
                )
            return cursor.rowcount == 1

    def add_bulk_targets_batch(self, job_id: str, targets: list[tuple[str | int, str]]) -> int:
        """Insert candidate IDs in one transaction; duplicate IDs do not inflate counts."""
        if not targets:
            return 0
        inserted = 0
        now = _utc_now()
        with self._transaction() as db:
            for user_id, display_name in targets:
                cursor = db.execute(
                    "INSERT OR IGNORE INTO bulk_targets(job_id,user_id,display_name,status,updated_at) "
                    "VALUES(?,?,?,'pending',?)",
                    (job_id, self._user(user_id), display_name or "", now),
                )
                inserted += cursor.rowcount
            if inserted:
                db.execute(
                    "UPDATE bulk_jobs SET matched=matched+? WHERE job_id=?",
                    (inserted, job_id),
                )
        return inserted

    def update_bulk_scan(
        self,
        job_id: str,
        *,
        examined_delta: int = 0,
        status: str | None = None,
        scan_complete: bool | None = None,
        error_code: str | None = None,
    ) -> None:
        assignments = ["examined=examined+?"]
        params: list[Any] = [int(examined_delta)]
        if status is not None:
            assignments.append("status=?")
            params.append(status)
        if scan_complete is not None:
            assignments.append("scan_complete=?")
            params.append(int(scan_complete))
        if error_code is not None:
            assignments.append("error_code=?")
            params.append(error_code)
        params.append(job_id)
        with self._transaction() as db:
            db.execute(
                "UPDATE bulk_jobs SET " + ",".join(assignments) + " WHERE job_id=?",
                params,
            )

    def get_bulk_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM bulk_jobs WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_bulk_targets(self, job_id: str, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        if limit < 1 or offset < 0:
            raise ValueError("limit must be positive and offset cannot be negative")
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM bulk_targets WHERE job_id=? ORDER BY user_id LIMIT ? OFFSET ?",
                (job_id, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_pending_bulk_targets(self, job_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM bulk_targets WHERE job_id=? AND status='pending' "
                "ORDER BY user_id LIMIT ?",
                (job_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_bulk_target(self, job_id: str, user_id: str | int, status: str, error_code: str = "") -> None:
        with self._transaction() as db:
            row = db.execute(
                "SELECT status FROM bulk_targets WHERE job_id=? AND user_id=?",
                (job_id, self._user(user_id)),
            ).fetchone()
            if row is None or row["status"] != "pending":
                return
            db.execute(
                "UPDATE bulk_targets SET status=?,error_code=?,updated_at=? WHERE job_id=? AND user_id=?",
                (status, error_code, _utc_now(), job_id, self._user(user_id)),
            )
            column = {
                "succeeded": "succeeded",
                "already_done": "already_done",
                "skipped": "skipped",
                "failed": "failed",
            }.get(status)
            if column:
                db.execute(
                    f"UPDATE bulk_jobs SET {column}={column}+1 WHERE job_id=?",
                    (job_id,),
                )

    def finish_bulk_job(self, job_id: str, status: str = "completed", error_code: str = "") -> None:
        with self._transaction() as db:
            db.execute(
                "UPDATE bulk_jobs SET status=?,error_code=? WHERE job_id=?",
                (status, error_code, job_id),
            )

    def set_group_state(self, group_id: str | int, key: str, value: Any) -> None:
        if not key:
            raise ValueError("state key cannot be empty")
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self._transaction() as db:
            db.execute(
                "INSERT INTO group_command_state(group_id,state_key,value_json,updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(group_id,state_key) DO UPDATE SET "
                "value_json=excluded.value_json,updated_at=excluded.updated_at",
                (self._group(group_id), key, encoded, _utc_now()),
            )

    def get_group_state(self, group_id: str | int, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._db.execute(
                "SELECT value_json FROM group_command_state WHERE group_id=? AND state_key=?",
                (self._group(group_id), key),
            ).fetchone()
        if row is None:
            return default
        return json.loads(row[0])

    def record_name_family_observation(
        self,
        *,
        category: str,
        letter: str,
        normalized_answer: str,
        answer: str,
        group_id: str | int,
        user_id: str | int,
        now: float | None = None,
    ) -> tuple[int, int, int]:
        category = str(category or "").strip()
        letter = str(letter or "").strip()
        normalized_answer = str(normalized_answer or "").strip()
        answer = str(answer or "").strip()
        if not category or not letter or not normalized_answer or not answer:
            raise ValueError("name-family observation fields cannot be empty")
        timestamp = datetime.now(UTC).timestamp() if now is None else float(now)
        gid, uid = self._group(group_id), self._user(user_id)
        with self._transaction() as db:
            db.execute(
                "DELETE FROM name_family_learning WHERE observed_at<?",
                (timestamp - 180 * 24 * 60 * 60,),
            )
            db.execute(
                "INSERT INTO name_family_learning(category,letter,normalized_answer,answer,"
                "group_id,user_id,observed_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(category,letter,normalized_answer,group_id,user_id) DO UPDATE SET "
                "answer=excluded.answer,observed_at=MAX(name_family_learning.observed_at,excluded.observed_at)",
                (category, letter, normalized_answer, answer, gid, uid, timestamp),
            )
            total_rows = int(
                db.execute("SELECT COUNT(*) FROM name_family_learning").fetchone()[0]
            )
            if total_rows > 100_000:
                db.execute(
                    "DELETE FROM name_family_learning WHERE rowid IN ("
                    "SELECT rowid FROM name_family_learning ORDER BY observed_at ASC LIMIT ?) ",
                    (total_rows - 100_000,),
                )
            row = db.execute(
                "SELECT COUNT(*) AS observations,COUNT(DISTINCT user_id) AS users,"
                "COUNT(DISTINCT group_id) AS groups FROM name_family_learning "
                "WHERE category=? AND letter=? AND normalized_answer=?",
                (category, letter, normalized_answer),
            ).fetchone()
        return int(row["observations"]), int(row["users"]), int(row["groups"])

    def name_family_learning_counts(
        self, *, category: str, letter: str, normalized_answer: str
    ) -> tuple[int, int, int]:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS observations,COUNT(DISTINCT user_id) AS users,"
                "COUNT(DISTINCT group_id) AS groups FROM name_family_learning "
                "WHERE category=? AND letter=? AND normalized_answer=?",
                (str(category), str(letter), str(normalized_answer)),
            ).fetchone()
        return int(row["observations"]), int(row["users"]), int(row["groups"])

    def name_family_is_learned(
        self,
        *,
        category: str,
        letter: str,
        normalized_answer: str,
        min_observations: int = 5,
        min_unique_users: int = 3,
        min_unique_chats: int = 2,
    ) -> bool:
        if min(min_observations, min_unique_users, min_unique_chats) < 1:
            raise ValueError("learning thresholds must be positive")
        observations, users, groups = self.name_family_learning_counts(
            category=category,
            letter=letter,
            normalized_answer=normalized_answer,
        )
        return (
            observations >= min_observations
            and users >= min_unique_users
            and groups >= min_unique_chats
        )

    def list_group_states(self, key: str) -> list[tuple[str, Any]]:
        if not key:
            raise ValueError("state key cannot be empty")
        with self._lock:
            rows = self._db.execute(
                "SELECT group_id,value_json FROM group_command_state WHERE state_key=? ORDER BY group_id",
                (key,),
            ).fetchall()
        return [(str(row["group_id"]), json.loads(row["value_json"])) for row in rows]

    def list_group_states_bounded(
        self, key: str, *, limit: int
    ) -> tuple[list[tuple[str, Any]], bool]:
        """Read at most ``limit`` states and report whether the scan was complete."""
        if not key:
            raise ValueError("state key cannot be empty")
        if not 1 <= limit <= 5000:
            raise ValueError("bounded state limit must be between 1 and 5000")
        with self._lock:
            rows = self._db.execute(
                "SELECT group_id,value_json FROM group_command_state "
                "WHERE state_key=? ORDER BY group_id LIMIT ?",
                (key, limit + 1),
            ).fetchall()
        complete = len(rows) <= limit
        bounded_rows = rows[:limit]
        return (
            [
                (str(row["group_id"]), json.loads(row["value_json"]))
                for row in bounded_rows
            ],
            complete,
        )

    def list_group_states_prefix(
        self, prefix: str, *, limit: int = 2000
    ) -> list[tuple[str, str, Any]]:
        if not prefix:
            raise ValueError("state prefix cannot be empty")
        if not 1 <= limit <= 5000:
            raise ValueError("state prefix limit must be between 1 and 5000")
        with self._lock:
            rows = self._db.execute(
                "SELECT group_id,state_key,value_json FROM group_command_state "
                "WHERE substr(state_key,1,?)=? ORDER BY updated_at,group_id,state_key LIMIT ?",
                (len(prefix), prefix, limit),
            ).fetchall()
        return [
            (str(row["group_id"]), str(row["state_key"]), json.loads(row["value_json"]))
            for row in rows
        ]

    def count_group_states_prefix(self, prefix: str) -> int:
        if not prefix:
            raise ValueError("state prefix cannot be empty")
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS total FROM group_command_state WHERE substr(state_key,1,?)=?",
                (len(prefix), prefix),
            ).fetchone()
        return int(row["total"] if row else 0)

    def delete_group_state(self, group_id: str | int, key: str) -> bool:
        with self._transaction() as db:
            cursor = db.execute(
                "DELETE FROM group_command_state WHERE group_id=? AND state_key=?",
                (self._group(group_id), key),
            )
            return cursor.rowcount == 1

    def consume_daily_game_chance(
        self,
        game: str,
        user_id: str | int,
        day_key: str,
        limit: int,
        *,
        reference: str | None = None,
    ) -> tuple[bool, int]:
        """Atomically consume one persisted per-user game chance for a local calendar day."""
        game_key = str(game or "").strip()
        date_key = str(day_key or "").strip()
        if not game_key or not date_key or limit <= 0:
            raise ValueError("invalid daily game quota")
        uid = self._user(user_id)
        event_reference = str(reference or "").strip()
        state_key = f"game_daily_quota:{game_key}:{uid}"
        now = _utc_now()
        with self._transaction() as db:
            row = db.execute(
                "SELECT value_json FROM group_command_state WHERE group_id=? AND state_key=?",
                ("__global__", state_key),
            ).fetchone()
            try:
                saved = json.loads(row[0]) if row else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                saved = {}
            if not isinstance(saved, dict) or saved.get("date") != date_key:
                used = 0
                events: list[dict[str, Any]] = []
            else:
                try:
                    used = max(0, int(saved.get("count", 0)))
                except (TypeError, ValueError, OverflowError):
                    used = 0
                events = saved.get("events", [])
                if not isinstance(events, list):
                    events = []
            if event_reference:
                for event in events:
                    if isinstance(event, dict) and event.get("reference") == event_reference:
                        try:
                            return True, max(0, int(event.get("remaining", limit - used)))
                        except (TypeError, ValueError, OverflowError):
                            return True, max(0, limit - used)
            used += 1
            remaining = max(0, limit - used)
            if event_reference:
                events.append({"reference": event_reference, "remaining": remaining})
                events = events[-32:]
            encoded = json.dumps(
                {"date": date_key, "count": used, "events": events},
                ensure_ascii=False, separators=(",", ":"),
            )
            db.execute(
                "INSERT INTO group_command_state(group_id,state_key,value_json,updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(group_id,state_key) DO UPDATE SET "
                "value_json=excluded.value_json,updated_at=excluded.updated_at",
                ("__global__", state_key, encoded, now),
            )
        return True, remaining

    def add_group_command_member(
        self,
        group_id: str | int,
        kind: str,
        user_id: str | int,
        *,
        display_name: str = "",
        username: str | None = None,
    ) -> bool:
        if kind not in {"registered_admin", "vip", "ai_allowed"}:
            raise ValueError("unsupported group member category")
        with self._transaction() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO group_command_members(group_id,kind,user_id,display_name,username,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (self._group(group_id), kind, self._user(user_id), display_name or "", username, _utc_now()),
            )
            return cursor.rowcount == 1

    def remove_group_command_member(
        self, group_id: str | int, kind: str, user_id: str | int
    ) -> bool:
        with self._transaction() as db:
            cursor = db.execute(
                "DELETE FROM group_command_members WHERE group_id=? AND kind=? AND user_id=?",
                (self._group(group_id), kind, self._user(user_id)),
            )
            return cursor.rowcount == 1

    def has_group_command_member(
        self, group_id: str | int, kind: str, user_id: str | int
    ) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM group_command_members WHERE group_id=? AND kind=? AND user_id=?",
                (self._group(group_id), kind, self._user(user_id)),
            ).fetchone()
        return row is not None

    def list_group_command_members(
        self, group_id: str | int, kind: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM group_command_members WHERE group_id=? AND kind=? "
                "ORDER BY created_at,user_id LIMIT ?",
                (self._group(group_id), kind, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_warning_count(self, group_id: str | int, user_id: str | int) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT warning_count FROM group_warning_counts WHERE group_id=? AND user_id=?",
                (self._group(group_id), self._user(user_id)),
            ).fetchone()
        return int(row[0]) if row else 0

    def list_warning_counts(self, group_id: str | int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT user_id,warning_count FROM group_warning_counts "
                "WHERE group_id=? AND warning_count>0 ORDER BY warning_count DESC,user_id",
                (self._group(group_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def reset_group_warnings(self, group_id: str | int) -> int:
        with self._transaction() as db:
            cursor = db.execute(
                "UPDATE group_warning_counts SET warning_count=0,updated_at=? "
                "WHERE group_id=? AND warning_count>0",
                (_utc_now(), self._group(group_id)),
            )
            return int(cursor.rowcount)

    def add_warning(self, group_id: str | int, user_id: str | int) -> int:
        with self._transaction() as db:
            db.execute(
                "INSERT INTO group_warning_counts(group_id,user_id,warning_count,updated_at) "
                "VALUES(?,?,1,?) ON CONFLICT(group_id,user_id) DO UPDATE SET "
                "warning_count=warning_count+1,updated_at=excluded.updated_at",
                (self._group(group_id), self._user(user_id), _utc_now()),
            )
            row = db.execute(
                "SELECT warning_count FROM group_warning_counts WHERE group_id=? AND user_id=?",
                (self._group(group_id), self._user(user_id)),
            ).fetchone()
        return int(row[0])

    def add_automatic_warning(
        self,
        group_id: str | int,
        user_id: str | int,
        *,
        display_name: str = "",
        reason: str,
        message_id: str | int | None = None,
    ) -> int:
        """Atomically count and audit a bot-detected violation without a fake actor."""
        gid, uid = self._group(group_id), self._user(user_id)
        timestamp = _utc_now()
        with self._transaction() as db:
            db.execute(
                "INSERT INTO group_warning_counts(group_id,user_id,warning_count,updated_at) "
                "VALUES(?,?,1,?) ON CONFLICT(group_id,user_id) DO UPDATE SET "
                "warning_count=warning_count+1,updated_at=excluded.updated_at",
                (gid, uid, timestamp),
            )
            row = db.execute(
                "SELECT warning_count FROM group_warning_counts WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
            db.execute(
                "INSERT INTO group_command_events(group_id,event_type,user_id,display_name,"
                "actor_display,detail,message_id,occurred_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    gid,
                    "warning",
                    uid,
                    display_name or "",
                    "سامانه",
                    reason or "تخلف خودکار",
                    None if message_id is None else str(message_id),
                    timestamp,
                ),
            )
            _trim_group_command_events(db, gid)
        return int(row[0])

    def reset_warning_count(self, group_id: str | int, user_id: str | int) -> int:
        gid, uid = self._group(group_id), self._user(user_id)
        with self._transaction() as db:
            row = db.execute(
                "SELECT warning_count FROM group_warning_counts WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
            old_count = int(row[0]) if row else 0
            db.execute(
                "INSERT INTO group_warning_counts(group_id,user_id,warning_count,updated_at) "
                "VALUES(?,?,0,?) ON CONFLICT(group_id,user_id) DO UPDATE SET "
                "warning_count=0,updated_at=excluded.updated_at",
                (gid, uid, _utc_now()),
            )
        return old_count

    def record_group_command_event(
        self,
        *,
        group_id: str | int,
        event_type: str,
        user_id: str | int | None = None,
        display_name: str = "",
        actor_id: str | int | None = None,
        actor_display: str = "",
        detail: str = "",
        message_id: str | int | None = None,
        message_excerpt: str = "",
        occurred_at: datetime | str | None = None,
    ) -> int:
        timestamp = _iso(occurred_at) or _utc_now()
        # A short bounded excerpt is retained only for explicit deletion logs.
        excerpt = message_excerpt[:240] if event_type == "message_deleted" else ""
        with self._transaction() as db:
            cursor = db.execute(
                "INSERT INTO group_command_events(group_id,event_type,user_id,display_name,actor_id,"
                "actor_display,detail,message_id,message_excerpt,occurred_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    self._group(group_id), event_type,
                    None if user_id is None else self._user(user_id), display_name or "",
                    None if actor_id is None else self._user(actor_id), actor_display or "",
                    detail or "", None if message_id is None else str(message_id), excerpt, timestamp,
                ),
            )
            gid = self._group(group_id)
            _trim_group_command_events(db, gid)
            return int(cursor.lastrowid)

    def list_group_history_events(
        self,
        group_id: str | int,
        *,
        since: datetime | str,
        max_users: int = MAX_GROUP_HISTORY_USERS,
        records_per_user: int = MAX_GROUP_HISTORY_RECORDS_PER_USER,
    ) -> list[dict[str, Any]]:
        """Return the most recent bounded history across warnings, mutes and kicks."""
        if not 1 <= max_users <= MAX_GROUP_HISTORY_USERS:
            raise ValueError(f"max_users must be between 1 and {MAX_GROUP_HISTORY_USERS}")
        if not 1 <= records_per_user <= MAX_GROUP_HISTORY_RECORDS_PER_USER:
            raise ValueError(
                "records_per_user must be between 1 and "
                f"{MAX_GROUP_HISTORY_RECORDS_PER_USER}"
            )
        gid, since_value = self._group(group_id), _iso(since)
        if since_value is None:
            raise ValueError("since is required")
        sql = """
            WITH history_rows AS (
                SELECT 'warning' AS event_type, user_id, display_name, actor_id,
                       actor_display, detail, occurred_at, CAST(event_id AS TEXT) AS source_id
                FROM group_command_events
                WHERE group_id=? AND event_type='warning' AND occurred_at>=?
                UNION ALL
                SELECT 'kick' AS event_type, user_id, display_name, actor_id,
                       '' AS actor_display, reason AS detail, occurred_at, event_key AS source_id
                FROM kick_events
                WHERE group_id=? AND result='success' AND occurred_at>=?
                UNION ALL
                SELECT 'mute' AS event_type, user_id, display_name, actor_id,
                       '' AS actor_display, reason AS detail, occurred_at, CAST(event_id AS TEXT) AS source_id
                FROM mute_events
                WHERE group_id=? AND event_type='mute' AND result='success' AND occurred_at>=?
            ), identified AS (
                SELECT *, COALESCE(NULLIF(user_id,''), NULLIF(display_name,''), 'unknown') AS user_key
                FROM history_rows
            ), ranked_rows AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY user_key ORDER BY occurred_at DESC, source_id DESC
                       ) AS per_user_rank,
                       MAX(occurred_at) OVER (PARTITION BY user_key) AS user_latest
                FROM identified
            ), ranked_users AS (
                SELECT user_key, MAX(user_latest) AS user_latest,
                       ROW_NUMBER() OVER (
                           ORDER BY MAX(user_latest) DESC, user_key
                       ) AS user_rank
                FROM ranked_rows
                GROUP BY user_key
            )
            SELECT r.event_type, r.user_id, r.display_name, r.actor_id,
                   r.actor_display, r.detail, r.occurred_at
            FROM ranked_rows AS r
            JOIN ranked_users AS u ON u.user_key=r.user_key
            WHERE r.per_user_rank<=? AND u.user_rank<=?
            ORDER BY r.occurred_at DESC, r.source_id DESC
        """
        params = (gid, since_value, gid, since_value, gid, since_value, records_per_user, max_users)
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def list_group_command_events(
        self,
        group_id: str | int,
        *,
        since: datetime | str | None = None,
        event_types: tuple[str, ...] = (),
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= MAX_GROUP_COMMAND_EVENTS:
            raise ValueError(
                f"limit must be between 1 and {MAX_GROUP_COMMAND_EVENTS}"
            )
        sql = "SELECT * FROM group_command_events WHERE group_id=?"
        params: list[Any] = [self._group(group_id)]
        if since is not None:
            sql += " AND occurred_at>=?"
            params.append(_iso(since))
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            sql += f" AND event_type IN ({placeholders})"
            params.extend(event_types)
        sql += " ORDER BY occurred_at DESC,event_id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def record_group_message(
        self,
        group_id: str | int,
        user_id: str | int,
        *,
        display_name: str = "",
        username: str | None = None,
    ) -> None:
        gid, uid, now = self._group(group_id), self._user(user_id), _utc_now()
        now_epoch = datetime.now(UTC).timestamp()
        normalized_username = (
            str(username).strip().lstrip("@").lower() if username is not None else ""
        ) or None
        with self._transaction() as db:
            db.execute(
                "INSERT INTO group_stats(group_id,messages,updated_at) VALUES(?,1,?) "
                "ON CONFLICT(group_id) DO UPDATE SET messages=messages+1,updated_at=excluded.updated_at",
                (gid, now),
            )
            if normalized_username is not None:
                db.execute(
                    "UPDATE group_user_stats SET username=NULL WHERE group_id=? "
                    "AND lower(trim(username,'@'))=? AND user_id<>?",
                    (gid, normalized_username, uid),
                )
            db.execute(
                "INSERT INTO group_user_stats(group_id,user_id,display_name,username,messages,deleted) "
                "VALUES(?,?,?,?,1,0) ON CONFLICT(group_id,user_id) DO UPDATE SET "
                "display_name=CASE WHEN excluded.display_name!='' THEN excluded.display_name ELSE display_name END,"
                "username=COALESCE(excluded.username,username),messages=messages+1",
                (gid, uid, display_name or "", normalized_username),
            )
            db.execute(
                "INSERT INTO group_user_activity(group_id,user_id,first_seen,last_seen) VALUES(?,?,?,?) "
                "ON CONFLICT(group_id,user_id) DO UPDATE SET last_seen=excluded.last_seen",
                (gid, uid, now_epoch, now_epoch),
            )

    def record_group_media_activity(
        self, group_id: str | int, user_id: str | int, media_kind: str
    ) -> None:
        if media_kind not in {"gif", "video"}:
            raise ValueError("media kind must be gif or video")
        gid, uid = self._group(group_id), self._user(user_id)
        now = datetime.now(UTC).timestamp()
        column = "gifs" if media_kind == "gif" else "videos"
        with self._transaction() as db:
            db.execute(
                "INSERT INTO group_user_activity(group_id,user_id,first_seen,last_seen,gifs,videos) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(group_id,user_id) DO UPDATE SET "
                f"last_seen=excluded.last_seen,{column}={column}+1",
                (gid, uid, now, now, int(media_kind == "gif"), int(media_kind == "video")),
            )

    def get_group_user_stats(self, group_id: str | int, user_id: str | int) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM group_user_stats WHERE group_id=? AND user_id=?",
                (self._group(group_id), self._user(user_id)),
            ).fetchone()
        if row is None:
            return {"group_id": self._group(group_id), "user_id": self._user(user_id), "messages": 0, "deleted": 0, "display_name": "", "username": None}
        return dict(row)

    def find_group_user_by_username(
        self, group_id: str | int, username: str
    ) -> dict[str, Any] | None:
        normalized = str(username or "").strip().lstrip("@").casefold()
        if not normalized:
            return None
        with self._lock:
            rows = self._db.execute(
                "SELECT user_id,display_name,username FROM group_user_stats "
                "WHERE group_id=? AND lower(trim(username,'@'))=? ORDER BY user_id LIMIT 2",
                (self._group(group_id), normalized),
            ).fetchall()
        if len(rows) != 1:
            return None
        return dict(rows[0])

    def get_group_user_activity(self, group_id: str | int, user_id: str | int) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM group_user_activity WHERE group_id=? AND user_id=?",
                (self._group(group_id), self._user(user_id)),
            ).fetchone()
        if row is None:
            return {"gifs": 0, "videos": 0, "first": 0.0, "last": 0.0}
        return {
            "gifs": int(row["gifs"]), "videos": int(row["videos"]),
            "first": float(row["first_seen"]), "last": float(row["last_seen"]),
        }

    def _has_owner_silver_bonus(
        self, group_id: str | int, user_id: str | int,
    ) -> bool:
        checker = self._global_owner_check
        if checker is None or _economy_chat_key(group_id) not in self._owner_silver_group_ids:
            return False
        try:
            return bool(checker(self._user(user_id)))
        except Exception:  # noqa: BLE001 - economy exemptions fail closed
            return False

    def _economy_rank_total(
        self,
        group_id: str | int,
        user_id: str | int,
        row: sqlite3.Row | None,
        *,
        owner_silver_bonus: bool | None = None,
    ) -> int:
        if owner_silver_bonus is None:
            owner_silver_bonus = self._has_owner_silver_bonus(group_id, user_id)
        bronze = int(row["bronze"]) if row is not None else 0
        silver = int(row["silver"]) if row is not None else 0
        gold = int(row["gold"]) if row is not None else 0
        if owner_silver_bonus:
            silver = _OWNER_SILVER_AMOUNT
        return _economy_total(bronze, silver, gold)

    @staticmethod
    def _record_economy_rank_change(
        db: sqlite3.Connection,
        group_id: str,
        user_id: str,
        before_total: int,
        after_total: int,
    ) -> None:
        if before_total == after_total:
            return
        db.execute(
            "UPDATE economy_rank_clock SET sequence=sequence+1 WHERE singleton=1"
        )
        row = db.execute(
            "SELECT sequence FROM economy_rank_clock WHERE singleton=1"
        ).fetchone()
        if row is None:
            raise RuntimeError("economy rank sequence is missing")
        db.execute(
            "UPDATE group_economy SET value_reached_seq=? "
            "WHERE group_id=? AND user_id=?",
            (int(row["sequence"]), group_id, user_id),
        )

    def _visible_economy_profile(
        self,
        group_id: str | int,
        user_id: str | int,
        profile: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if profile is None or not self._has_owner_silver_bonus(group_id, user_id):
            return profile
        visible = dict(profile)
        visible["silver"] = _OWNER_SILVER_AMOUNT
        visible["total_coin_value"] = _economy_total(
            int(visible.get("bronze", 0)), _OWNER_SILVER_AMOUNT,
            int(visible.get("gold", 0)),
        )
        return visible

    def get_economy_profile(self, group_id: str | int, user_id: str | int) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (self._group(group_id), self._user(user_id)),
            ).fetchone()
        profile = dict(row) if row is not None else {
            "group_id": self._group(group_id), "user_id": self._user(user_id),
            "display_name": "", "bronze": 0, "silver": 0, "gold": 0,
            "wins": 0, "created_at": None, "updated_at": None,
        }
        profile.pop("value_reached_seq", None)
        profile["total_coin_value"] = (
            _economy_total(int(profile["bronze"]), int(profile["silver"]), int(profile["gold"]))
        )
        return self._visible_economy_profile(group_id, user_id, profile) or profile

    def set_economy_display_name(
        self,
        group_id: str | int,
        user_id: str | int,
        display_name: str,
    ) -> dict[str, Any]:
        """Persist a user's current display name without changing their wallet."""
        gid, uid = self._group(group_id), self._user(user_id)
        name = str(display_name or "").strip()[:128]
        if not name:
            return self.get_economy_profile(gid, uid)
        with self._lock:
            existing = self._db.execute(
                "SELECT display_name FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
        if existing is not None and str(existing["display_name"] or "") == name:
            return self.get_economy_profile(gid, uid)
        now = _utc_now()
        with self._transaction() as db:
            db.execute(
                "INSERT INTO group_economy(group_id,user_id,display_name,bronze,silver,gold,wins,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(group_id,user_id) DO UPDATE SET "
                "display_name=excluded.display_name",
                (gid, uid, name, 0, 0, 0, 0, now, now),
            )
            row = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
        profile = self._economy_profile_dict(row) or {}
        return self._visible_economy_profile(gid, uid, profile) or profile

    def award_group_coins(
        self,
        group_id: str | int,
        user_id: str | int,
        amount: int,
        coin_type: str = "bronze",
        *,
        reference: str | None = None,
        display_name: str = "",
        note: str = "",
        win: bool = True,
        return_outcome: bool = False,
    ) -> dict[str, Any] | tuple[dict[str, Any], str]:
        if coin_type not in {"bronze", "silver", "gold"} or amount <= 0:
            raise ValueError("invalid coin award")
        gid, uid, now = self._group(group_id), self._user(user_id), _utc_now()
        owner_bonus = self._has_owner_silver_bonus(gid, uid)
        with self._transaction() as db:
            if reference is not None:
                existing = db.execute(
                    "SELECT user_id,coin_type,amount FROM economy_transactions WHERE group_id=? AND reference=?",
                    (gid, str(reference)),
                ).fetchone()
                if existing:
                    if (
                        str(existing["user_id"]) != uid
                        or str(existing["coin_type"]) != coin_type
                        or int(existing["amount"]) != amount
                    ):
                        raise ValueError("economy reference was reused for a different award")
                    row = db.execute(
                        "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                        (gid, uid),
                    ).fetchone()
                    profile = dict(row) if row else {
                        "group_id": gid, "user_id": uid, "display_name": display_name,
                        "bronze": 0, "silver": 0, "gold": 0, "wins": 0,
                    }
                    profile.pop("value_reached_seq", None)
                    profile["total_coin_value"] = (
                        _economy_total(int(profile["bronze"]), int(profile["silver"]), int(profile["gold"]))
                    )
                    profile = self._visible_economy_profile(gid, uid, profile) or profile
                    return (profile, "duplicate") if return_outcome else profile
            owner_silver = coin_type == "silver" and owner_bonus
            before_row = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
            before_total = self._economy_rank_total(
                gid, uid, before_row, owner_silver_bonus=owner_bonus
            )
            values = {
                "bronze": amount if coin_type == "bronze" else 0,
                "silver": amount if coin_type == "silver" and not owner_silver else 0,
                "gold": amount if coin_type == "gold" else 0,
            }
            db.execute(
                "INSERT INTO group_economy(group_id,user_id,display_name,bronze,silver,gold,wins,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(group_id,user_id) DO UPDATE SET "
                "display_name=CASE WHEN excluded.display_name!='' THEN excluded.display_name ELSE group_economy.display_name END,"
                "bronze=group_economy.bronze+excluded.bronze,"
                "silver=group_economy.silver+excluded.silver,"
                "gold=group_economy.gold+excluded.gold,"
                "wins=group_economy.wins+excluded.wins,"
                "updated_at=excluded.updated_at",
                (
                    gid, uid, display_name or "", values["bronze"], values["silver"],
                    values["gold"], int(win and not owner_silver), now, now,
                ),
            )
            db.execute(
                "INSERT INTO economy_transactions(group_id,user_id,reference,coin_type,amount,note,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (gid, uid, None if reference is None else str(reference), coin_type, amount, note[:160], now),
            )
            row = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
            after_total = self._economy_rank_total(
                gid, uid, row, owner_silver_bonus=owner_bonus
            )
            self._record_economy_rank_change(
                db, gid, uid, before_total, after_total
            )
            row = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
        profile = self._economy_profile_dict(row)
        profile = self._visible_economy_profile(gid, uid, profile) or profile
        return (profile, "awarded") if return_outcome else profile

    def spend_group_coins(
        self,
        group_id: str | int,
        user_id: str | int,
        amount: int,
        coin_type: str = "bronze",
        *,
        reference: str | None = None,
        display_name: str = "",
        note: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        """Atomically debit coins and report ``spent``, ``duplicate``, or ``insufficient``."""
        if coin_type not in {"bronze", "silver", "gold"} or amount <= 0:
            raise ValueError("invalid coin spend")
        gid, uid, now = self._group(group_id), self._user(user_id), _utc_now()
        owner_bonus = self._has_owner_silver_bonus(gid, uid)
        owner_silver = coin_type == "silver" and owner_bonus
        with self._transaction() as db:
            if reference is not None:
                existing = db.execute(
                    "SELECT user_id,coin_type,amount,note FROM economy_transactions "
                    "WHERE group_id=? AND reference=?",
                    (gid, str(reference)),
                ).fetchone()
                if existing:
                    if (
                        str(existing["user_id"]) != uid
                        or str(existing["coin_type"]) != coin_type
                        or int(existing["amount"]) != amount
                        or str(existing["note"]) != note[:160]
                    ):
                        raise ValueError("economy reference was reused for a different spend")
                    row = db.execute(
                        "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                        (gid, uid),
                    ).fetchone()
                    profile = self._economy_profile_dict(row) if row else None
                    return self._visible_economy_profile(gid, uid, profile), "duplicate"
            row = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
            if not owner_silver and (row is None or int(row[coin_type]) < amount):
                profile = self._economy_profile_dict(row) if row else None
                return self._visible_economy_profile(gid, uid, profile), "insufficient"
            before_total = self._economy_rank_total(
                gid, uid, row, owner_silver_bonus=owner_bonus
            )
            if owner_silver:
                db.execute(
                    "INSERT INTO group_economy(group_id,user_id,display_name,bronze,silver,gold,wins,created_at,updated_at) "
                    "VALUES(?,?,?,0,0,0,0,?,?) ON CONFLICT(group_id,user_id) DO UPDATE SET "
                    "display_name=CASE WHEN excluded.display_name!='' THEN excluded.display_name ELSE group_economy.display_name END,"
                    "updated_at=excluded.updated_at",
                    (gid, uid, display_name or "", now, now),
                )
            else:
                db.execute(
                    f"UPDATE group_economy SET {coin_type}={coin_type}-?, "
                "display_name=CASE WHEN ?!='' THEN ? ELSE display_name END,updated_at=? "
                "WHERE group_id=? AND user_id=? AND " + coin_type + ">=?",
                (amount, display_name, display_name, now, gid, uid, amount),
            )
            cursor = db.execute(
                "INSERT INTO economy_transactions(group_id,user_id,reference,coin_type,amount,note,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (gid, uid, None if reference is None else str(reference), coin_type, amount, note[:160], now),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("economy spend transaction was not recorded")
            updated = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
            after_total = self._economy_rank_total(
                gid, uid, updated, owner_silver_bonus=owner_bonus
            )
            self._record_economy_rank_change(
                db, gid, uid, before_total, after_total
            )
            updated = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
        profile = self._economy_profile_dict(updated)
        return self._visible_economy_profile(gid, uid, profile), "spent"

    @staticmethod
    def _economy_profile_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        profile = dict(row)
        profile.pop("value_reached_seq", None)
        profile["total_coin_value"] = (
            _economy_total(int(profile["bronze"]), int(profile["silver"]), int(profile["gold"]))
        )
        return profile

    def convert_group_coins(
        self,
        group_id: str | int,
        user_id: str | int,
        from_coin: str,
        to_coin: str,
        cost: int,
        gain: int,
        *,
        reference: str | None = None,
        display_name: str = "",
        note: str = "conversion",
    ) -> tuple[dict[str, Any] | None, str]:
        if (
            from_coin not in {"bronze", "silver", "gold"}
            or to_coin not in {"bronze", "silver", "gold"}
            or from_coin == to_coin
            or cost <= 0
            or gain <= 0
        ):
            raise ValueError("invalid coin conversion")
        gid, uid, now = self._group(group_id), self._user(user_id), _utc_now()
        owner_bonus = self._has_owner_silver_bonus(gid, uid)
        owner_silver_source = from_coin == "silver" and owner_bonus
        with self._transaction() as db:
            if reference is not None:
                existing = db.execute(
                    "SELECT user_id,coin_type,amount,note FROM economy_transactions "
                    "WHERE group_id=? AND reference IN (?,?) ORDER BY reference",
                    (gid, f"{reference}:debit", f"{reference}:credit"),
                ).fetchall()
                if existing:
                    expected = {
                        (uid, from_coin, cost, f"{note}:debit"),
                        (uid, to_coin, gain, f"{note}:credit"),
                    }
                    found = {
                        (str(row["user_id"]), str(row["coin_type"]), int(row["amount"]), str(row["note"]))
                        for row in existing
                    }
                    if found != expected or len(existing) != 2:
                        raise ValueError("economy reference was reused for a different conversion")
                    row = db.execute(
                        "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                        (gid, uid),
                    ).fetchone()
                    profile = self._economy_profile_dict(row)
                    return self._visible_economy_profile(gid, uid, profile), "duplicate"
            row = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
            if not owner_silver_source and (row is None or int(row[from_coin]) < cost):
                profile = self._economy_profile_dict(row)
                return self._visible_economy_profile(gid, uid, profile), "insufficient"
            before_total = self._economy_rank_total(
                gid, uid, row, owner_silver_bonus=owner_bonus
            )
            if owner_silver_source and row is None:
                db.execute(
                    "INSERT INTO group_economy(group_id,user_id,display_name,created_at,updated_at) "
                    "VALUES(?,?,?,?,?)",
                    (gid, uid, display_name or "", now, now),
                )
            if owner_silver_source:
                db.execute(
                    f"UPDATE group_economy SET {to_coin}={to_coin}+?, "
                    "display_name=CASE WHEN ?!='' THEN ? ELSE display_name END,updated_at=? "
                    "WHERE group_id=? AND user_id=?",
                    (gain, display_name, display_name, now, gid, uid),
                )
            else:
                db.execute(
                    f"UPDATE group_economy SET {from_coin}={from_coin}-?,{to_coin}={to_coin}+?, "
                "display_name=CASE WHEN ?!='' THEN ? ELSE display_name END,updated_at=? "
                f"WHERE group_id=? AND user_id=? AND {from_coin}>=?",
                (cost, gain, display_name, display_name, now, gid, uid, cost),
            )
            for suffix, coin, amount, direction in (
                ("debit", from_coin, cost, "debit"),
                ("credit", to_coin, gain, "credit"),
            ):
                db.execute(
                    "INSERT INTO economy_transactions(group_id,user_id,reference,coin_type,amount,note,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (
                        gid,
                        uid,
                        None if reference is None else f"{reference}:{suffix}",
                        coin,
                        amount,
                        f"{note}:{direction}"[:160],
                        now,
                    ),
                )
            updated = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
            after_total = self._economy_rank_total(
                gid, uid, updated, owner_silver_bonus=owner_bonus
            )
            self._record_economy_rank_change(
                db, gid, uid, before_total, after_total
            )
            updated = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
        profile = self._economy_profile_dict(updated)
        return self._visible_economy_profile(gid, uid, profile), "converted"

    def transfer_group_coins(
        self,
        group_id: str | int,
        sender_id: str | int,
        receiver_id: str | int,
        coin_type: str,
        amount: int,
        *,
        reference: str | None = None,
        sender_name: str = "",
        receiver_name: str = "",
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
        if coin_type not in {"bronze", "silver", "gold"} or amount <= 0:
            raise ValueError("invalid coin transfer")
        gid = self._group(group_id)
        sender, receiver = self._user(sender_id), self._user(receiver_id)
        if sender == receiver:
            raise ValueError("cannot transfer coins to yourself")
        now = _utc_now()
        sender_owner_bonus = self._has_owner_silver_bonus(gid, sender)
        receiver_owner_bonus = self._has_owner_silver_bonus(gid, receiver)
        owner_silver_sender = coin_type == "silver" and sender_owner_bonus
        with self._transaction() as db:
            if reference is not None:
                existing = db.execute(
                    "SELECT user_id,coin_type,amount,note FROM economy_transactions "
                    "WHERE group_id=? AND reference IN (?,?) ORDER BY reference",
                    (gid, f"{reference}:out", f"{reference}:in"),
                ).fetchall()
                if existing:
                    expected = {
                        (sender, coin_type, amount, "transfer_out"),
                        (receiver, coin_type, amount, "transfer_in"),
                    }
                    found = {
                        (str(row["user_id"]), str(row["coin_type"]), int(row["amount"]), str(row["note"]))
                        for row in existing
                    }
                    if found != expected or len(existing) != 2:
                        raise ValueError("economy reference was reused for a different transfer")
                    sender_row = db.execute(
                        "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                        (gid, sender),
                    ).fetchone()
                    receiver_row = db.execute(
                        "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                        (gid, receiver),
                    ).fetchone()
                    sender_profile = self._economy_profile_dict(sender_row)
                    receiver_profile = self._economy_profile_dict(receiver_row)
                    return (
                        self._visible_economy_profile(gid, sender, sender_profile),
                        self._visible_economy_profile(gid, receiver, receiver_profile),
                        "duplicate",
                    )
            sender_row = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, sender),
            ).fetchone()
            if not owner_silver_sender and (
                sender_row is None or int(sender_row[coin_type]) < amount
            ):
                profile = self._economy_profile_dict(sender_row)
                return self._visible_economy_profile(gid, sender, profile), None, "insufficient"
            receiver_before_row = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, receiver),
            ).fetchone()
            sender_before_total = self._economy_rank_total(
                gid, sender, sender_row, owner_silver_bonus=sender_owner_bonus
            )
            receiver_before_total = self._economy_rank_total(
                gid, receiver, receiver_before_row,
                owner_silver_bonus=receiver_owner_bonus,
            )
            if owner_silver_sender:
                db.execute(
                    "INSERT INTO group_economy(group_id,user_id,display_name,created_at,updated_at) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(group_id,user_id) DO UPDATE SET "
                    "display_name=CASE WHEN excluded.display_name!='' THEN excluded.display_name ELSE group_economy.display_name END,"
                    "updated_at=excluded.updated_at",
                    (gid, sender, sender_name, now, now),
                )
            db.execute(
                "INSERT INTO group_economy(group_id,user_id,display_name,created_at,updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(group_id,user_id) DO UPDATE SET "
                "display_name=CASE WHEN excluded.display_name!='' THEN excluded.display_name ELSE group_economy.display_name END",
                (gid, receiver, receiver_name, now, now),
            )
            if not owner_silver_sender:
                db.execute(
                    f"UPDATE group_economy SET {coin_type}={coin_type}-?,updated_at=? "
                    f"WHERE group_id=? AND user_id=? AND {coin_type}>=?",
                    (amount, now, gid, sender, amount),
                )
            db.execute(
                f"UPDATE group_economy SET {coin_type}={coin_type}+?, "
                "display_name=CASE WHEN ?!='' THEN ? ELSE display_name END,updated_at=? "
                "WHERE group_id=? AND user_id=?",
                (amount, receiver_name, receiver_name, now, gid, receiver),
            )
            for suffix, uid, note in (("out", sender, "transfer_out"), ("in", receiver, "transfer_in")):
                db.execute(
                    "INSERT INTO economy_transactions(group_id,user_id,reference,coin_type,amount,note,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (gid, uid, None if reference is None else f"{reference}:{suffix}", coin_type, amount, note, now),
                )
            sender_row = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, sender),
            ).fetchone()
            receiver_row = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, receiver),
            ).fetchone()
            sender_after_total = self._economy_rank_total(
                gid, sender, sender_row, owner_silver_bonus=sender_owner_bonus
            )
            receiver_after_total = self._economy_rank_total(
                gid, receiver, receiver_row,
                owner_silver_bonus=receiver_owner_bonus,
            )
            self._record_economy_rank_change(
                db, gid, sender, sender_before_total, sender_after_total
            )
            self._record_economy_rank_change(
                db, gid, receiver, receiver_before_total, receiver_after_total
            )
            sender_row = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, sender),
            ).fetchone()
            receiver_row = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, receiver),
            ).fetchone()
        sender_profile = self._economy_profile_dict(sender_row)
        receiver_profile = self._economy_profile_dict(receiver_row)
        return (
            self._visible_economy_profile(gid, sender, sender_profile),
            self._visible_economy_profile(gid, receiver, receiver_profile),
            "transferred",
        )

    def claim_group_daily_coins(
        self,
        group_id: str | int,
        user_id: str | int,
        amount: int,
        *,
        now_epoch: float,
        cooldown_seconds: int = 86400,
        display_name: str = "",
        rewards: dict[str, int] | None = None,
    ) -> tuple[dict[str, Any] | None, str, int]:
        if cooldown_seconds <= 0 or now_epoch <= 0:
            raise ValueError("invalid daily reward")
        if rewards is None:
            if amount <= 0:
                raise ValueError("invalid daily reward")
            reward_amounts = {"bronze": int(amount), "silver": 0, "gold": 0}
        else:
            if set(rewards) - {"bronze", "silver", "gold"}:
                raise ValueError("invalid daily reward coin type")
            reward_amounts = {}
            for coin in ("bronze", "silver", "gold"):
                value = rewards.get(coin, 0)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError("invalid daily reward amount")
                reward_amounts[coin] = value
        gid, uid = self._group(group_id), self._user(user_id)
        owner_silver_bonus = self._has_owner_silver_bonus(gid, uid)
        stored_silver_reward = 0 if owner_silver_bonus else reward_amounts["silver"]
        state_key = f"economy_daily:{uid}"
        created_at = _utc_now()
        with self._transaction() as db:
            row = db.execute(
                "SELECT value_json FROM group_command_state WHERE group_id=? AND state_key=?",
                (gid, state_key),
            ).fetchone()
            try:
                last = float(json.loads(row[0]).get("last_claimed", 0)) if row else 0.0
            except (TypeError, ValueError, json.JSONDecodeError):
                last = 0.0
            wait = int(max(0.0, cooldown_seconds - (now_epoch - last)))
            if last > 0 and wait > 0:
                profile_row = db.execute(
                    "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                    (gid, uid),
                ).fetchone()
                profile = self._economy_profile_dict(profile_row)
                return self._visible_economy_profile(gid, uid, profile), "cooldown", wait

            before_row = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
            before_total = self._economy_rank_total(
                gid, uid, before_row, owner_silver_bonus=owner_silver_bonus
            )
            base_ref = f"daily:{uid}:{int(now_epoch)}"
            reward_items = [
                (coin, value, base_ref if coin == "bronze" and rewards is None else f"{base_ref}:{coin}")
                for coin, value in reward_amounts.items() if value > 0
            ]
            if not reward_items:
                # Preserve the source ledger's daily-claim event when all rewards are zero.
                reward_items = [("bronze", 0, f"{base_ref}:event")]
            references = [item[2] for item in reward_items]
            existing = []
            if references:
                placeholders = ",".join("?" for _ in references)
                existing = db.execute(
                    "SELECT user_id,reference,coin_type,amount,note FROM economy_transactions "
                    f"WHERE group_id=? AND reference IN ({placeholders})",
                    (gid, *references),
                ).fetchall()
            if existing:
                expected = {
                    (uid, reference, coin, value, "daily")
                    for coin, value, reference in reward_items
                }
                found = {
                    (str(item["user_id"]), str(item["reference"]), str(item["coin_type"]), int(item["amount"]), str(item["note"]))
                    for item in existing
                }
                if found != expected or len(existing) != len(reward_items):
                    raise ValueError("daily reward reference collision")
                profile_row = db.execute(
                    "SELECT * FROM group_economy WHERE group_id=? AND user_id=?", (gid, uid)
                ).fetchone()
                profile = self._economy_profile_dict(profile_row)
                return self._visible_economy_profile(gid, uid, profile), "duplicate", 0

            db.execute(
                "INSERT INTO group_economy(group_id,user_id,display_name,bronze,silver,gold,wins,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,0,?,?) ON CONFLICT(group_id,user_id) DO UPDATE SET "
                "display_name=CASE WHEN excluded.display_name!='' THEN excluded.display_name ELSE group_economy.display_name END,"
                "bronze=group_economy.bronze+excluded.bronze,"
                "silver=group_economy.silver+excluded.silver,"
                "gold=group_economy.gold+excluded.gold,updated_at=excluded.updated_at",
                (
                    gid, uid, display_name, reward_amounts["bronze"],
                    stored_silver_reward, reward_amounts["gold"], created_at, created_at,
                ),
            )
            for coin, value, reference in reward_items:
                db.execute(
                    "INSERT INTO economy_transactions(group_id,user_id,reference,coin_type,amount,note,created_at) "
                    "VALUES(?,?,?,?,?,'daily',?)",
                    (gid, uid, reference, coin, value, created_at),
                )
            encoded = json.dumps({"last_claimed": now_epoch}, separators=(",", ":"))
            db.execute(
                "INSERT INTO group_command_state(group_id,state_key,value_json,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(group_id,state_key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                (gid, state_key, encoded, created_at),
            )
            profile_row = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
            after_total = self._economy_rank_total(
                gid, uid, profile_row, owner_silver_bonus=owner_silver_bonus
            )
            self._record_economy_rank_change(
                db, gid, uid, before_total, after_total
            )
            profile_row = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
        profile = self._economy_profile_dict(profile_row)
        return self._visible_economy_profile(gid, uid, profile), "claimed", 0

    def economy_transaction_history(
        self, group_id: str | int, user_id: str | int, limit: int = 10
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("history limit must be between 1 and 100")
        with self._lock:
            rows = self._db.execute(
                "SELECT reference,coin_type,amount,note,created_at FROM economy_transactions "
                "WHERE group_id=? AND user_id=? ORDER BY created_at DESC,transaction_id DESC LIMIT ?",
                (self._group(group_id), self._user(user_id), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def buy_group_profile_item(
        self,
        group_id: str | int,
        user_id: str | int,
        item: dict[str, Any],
        *,
        reference: str,
        display_name: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
        item_id = str(item.get("id") or "")
        coin_type = str(item.get("coin_type") or "")
        amount = int(item.get("price", 0))
        kind = str(item.get("kind") or "")
        if not item_id or coin_type not in {"bronze", "silver", "gold"} or amount <= 0 or kind not in {"badge", "star", "title"}:
            raise ValueError("invalid profile item")
        gid, uid, now = self._group(group_id), self._user(user_id), _utc_now()
        owner_bonus = self._has_owner_silver_bonus(gid, uid)
        owner_silver = coin_type == "silver" and owner_bonus
        profile_key = f"economy_profile:{uid}"
        with self._transaction() as db:
            existing = db.execute(
                "SELECT user_id,coin_type,amount,note FROM economy_transactions WHERE group_id=? AND reference=?",
                (gid, str(reference)),
            ).fetchone()
            if existing:
                if (
                    str(existing["user_id"]) != uid
                    or str(existing["coin_type"]) != coin_type
                    or int(existing["amount"]) != amount
                    or str(existing["note"]) != f"purchase:{item_id}"
                ):
                    raise ValueError("economy reference was reused for a different purchase")
                row = db.execute(
                    "SELECT value_json FROM group_command_state WHERE group_id=? AND state_key=?",
                    (gid, profile_key),
                ).fetchone()
                current_profile = json.loads(row[0]) if row else {}
                balance_row = db.execute(
                    "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                    (gid, uid),
                ).fetchone()
                balance = self._economy_profile_dict(balance_row)
                return current_profile, self._visible_economy_profile(gid, uid, balance), "duplicate"
            row = db.execute(
                "SELECT value_json FROM group_command_state WHERE group_id=? AND state_key=?",
                (gid, profile_key),
            ).fetchone()
            profile = json.loads(row[0]) if row else {}
            profile.setdefault("registered", False)
            profile.setdefault("name", None)
            profile.setdefault("city", None)
            profile.setdefault("age", None)
            profile.setdefault("nickname", None)
            profile.setdefault("badges", [])
            profile.setdefault("stars", 0)
            profile.setdefault("titles", [])
            if kind == "badge" and item_id in profile["badges"]:
                return profile, None, "owned"
            if kind == "star" and int(profile["stars"] or 0) >= int(item.get("level", 0)):
                return profile, None, "owned"
            if kind == "title" and item_id in profile["titles"]:
                return profile, None, "owned"
            balance_row = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
            if not owner_silver and (
                balance_row is None or int(balance_row[coin_type]) < amount
            ):
                balance = self._economy_profile_dict(balance_row)
                return profile, self._visible_economy_profile(gid, uid, balance), "insufficient"
            before_total = self._economy_rank_total(
                gid, uid, balance_row, owner_silver_bonus=owner_bonus
            )
            if owner_silver and balance_row is None:
                db.execute(
                    "INSERT INTO group_economy(group_id,user_id,display_name,created_at,updated_at) "
                    "VALUES(?,?,?,?,?)",
                    (gid, uid, display_name or "", now, now),
                )
            if owner_silver:
                db.execute(
                    "UPDATE group_economy SET "
                    "display_name=CASE WHEN ?!='' THEN ? ELSE display_name END,updated_at=? "
                    "WHERE group_id=? AND user_id=?",
                    (display_name, display_name, now, gid, uid),
                )
            else:
                db.execute(
                    f"UPDATE group_economy SET {coin_type}={coin_type}-?, "
                    "display_name=CASE WHEN ?!='' THEN ? ELSE display_name END,updated_at=? "
                f"WHERE group_id=? AND user_id=? AND {coin_type}>=?",
                (amount, display_name, display_name, now, gid, uid, amount),
            )
            if kind == "badge":
                profile["badges"].append(item_id)
            elif kind == "star":
                profile["stars"] = int(item.get("level", 0))
            else:
                profile["titles"].append(item_id)
                profile["nickname"] = str(item.get("title") or item.get("label") or "")
            profile["updated_at"] = now
            encoded = json.dumps(profile, ensure_ascii=False, separators=(",", ":"))
            db.execute(
                "INSERT INTO group_command_state(group_id,state_key,value_json,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(group_id,state_key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                (gid, profile_key, encoded, now),
            )
            db.execute(
                "INSERT INTO economy_transactions(group_id,user_id,reference,coin_type,amount,note,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (gid, uid, str(reference), coin_type, amount, f"purchase:{item_id}", now),
            )
            updated = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
            after_total = self._economy_rank_total(
                gid, uid, updated, owner_silver_bonus=owner_bonus
            )
            self._record_economy_rank_change(
                db, gid, uid, before_total, after_total
            )
            updated = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
        balance = self._economy_profile_dict(updated)
        return profile, self._visible_economy_profile(gid, uid, balance), "purchased"

    def buy_dynamic_shop_item(
        self,
        group_id: str | int,
        user_id: str | int,
        item_id: str,
        *,
        reference: str,
        display_name: str = "",
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
        """Atomically debit, record, and decrement stock for one dynamic item."""
        identifier = str(item_id or "").strip()
        if not identifier or not reference:
            raise ValueError("dynamic shop item ID and transaction reference are required")
        gid, uid, now = self._group(group_id), self._user(user_id), _utc_now()
        owner_silver = self._has_owner_silver_bonus(gid, uid)
        item_key = f"economy_shop_item:{identifier}"
        purchase_key = f"economy_shop_purchases:{uid}"
        with self._transaction() as db:
            existing = db.execute(
                "SELECT user_id,coin_type,amount,note FROM economy_transactions "
                "WHERE group_id=? AND reference=?",
                (gid, str(reference)),
            ).fetchone()
            item_row = db.execute(
                "SELECT value_json FROM group_command_state WHERE group_id=? AND state_key=?",
                ("__global__", item_key),
            ).fetchone()
            item = json.loads(item_row[0]) if item_row else None
            if not isinstance(item, dict):
                item = {"id": identifier, "title": identifier}
            if existing:
                if (
                    str(existing["user_id"]) != uid
                    or str(existing["note"]) != f"purchase:{identifier}"
                    or int(existing["amount"]) <= 0
                    or str(existing["coin_type"]) not in {"bronze", "silver", "gold"}
                ):
                    raise ValueError("economy reference was reused for a different purchase")
                balance_row = db.execute(
                    "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                    (gid, uid),
                ).fetchone()
                balance = self._economy_profile_dict(balance_row)
                return item, self._visible_economy_profile(gid, uid, balance), "duplicate"
            if item_row is None:
                return item, None, "not_found"
            try:
                coin_type = str(item.get("coin_type") or "")
                price = int(item.get("price", 0))
                stock = item.get("stock")
            except (TypeError, ValueError):
                return item, None, "not_found"
            if coin_type not in {"bronze", "silver", "gold"} or price <= 0:
                return item, None, "not_found"
            if stock is not None and (not isinstance(stock, int) or stock <= 0):
                return item, None, "out_of_stock"
            balance_row = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
            owner_free_silver = owner_silver and coin_type == "silver"
            if not owner_free_silver and (
                balance_row is None or int(balance_row[coin_type]) < price
            ):
                balance = self._economy_profile_dict(balance_row)
                return item, self._visible_economy_profile(gid, uid, balance), "insufficient"
            before_total = self._economy_rank_total(
                gid, uid, balance_row, owner_silver_bonus=owner_silver
            )
            if owner_free_silver and balance_row is None:
                db.execute(
                    "INSERT INTO group_economy(group_id,user_id,display_name,created_at,updated_at) "
                    "VALUES(?,?,?,?,?)",
                    (gid, uid, display_name or "", now, now),
                )
            if owner_free_silver:
                db.execute(
                    "UPDATE group_economy SET "
                    "display_name=CASE WHEN ?!='' THEN ? ELSE display_name END,updated_at=? "
                    "WHERE group_id=? AND user_id=?",
                    (display_name, display_name, now, gid, uid),
                )
            else:
                db.execute(
                    f"UPDATE group_economy SET {coin_type}={coin_type}-?, "
                    "display_name=CASE WHEN ?!='' THEN ? ELSE display_name END,updated_at=? "
                    f"WHERE group_id=? AND user_id=? AND {coin_type}>=?",
                    (price, display_name, display_name, now, gid, uid, price),
                )
            db.execute(
                "INSERT INTO economy_transactions(group_id,user_id,reference,coin_type,amount,note,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (gid, uid, str(reference), coin_type, price, f"purchase:{identifier}", now),
            )
            purchases_row = db.execute(
                "SELECT value_json FROM group_command_state WHERE group_id=? AND state_key=?",
                (gid, purchase_key),
            ).fetchone()
            try:
                purchases = json.loads(purchases_row[0]) if purchases_row else []
            except (TypeError, ValueError, json.JSONDecodeError):
                purchases = []
            if not isinstance(purchases, list):
                purchases = []
            purchases.append({
                "item_id": identifier,
                "title": str(item.get("title") or identifier),
                "price": price,
                "coin_type": coin_type,
                "at": now,
            })
            encoded_purchases = json.dumps(purchases[-500:], ensure_ascii=False, separators=(",", ":"))
            db.execute(
                "INSERT INTO group_command_state(group_id,state_key,value_json,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(group_id,state_key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                (gid, purchase_key, encoded_purchases, now),
            )
            if stock is not None:
                item["stock"] = max(0, int(stock) - 1)
                encoded_item = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                db.execute(
                    "UPDATE group_command_state SET value_json=?,updated_at=? "
                    "WHERE group_id='__global__' AND state_key=?",
                    (encoded_item, now, item_key),
                )
            updated = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
            after_total = self._economy_rank_total(
                gid, uid, updated, owner_silver_bonus=owner_silver
            )
            self._record_economy_rank_change(
                db, gid, uid, before_total, after_total
            )
            updated = db.execute(
                "SELECT * FROM group_economy WHERE group_id=? AND user_id=?",
                (gid, uid),
            ).fetchone()
        balance = self._economy_profile_dict(updated)
        return item, self._visible_economy_profile(gid, uid, balance), "purchased"

    def group_coin_leaderboard(self, group_id: str | int, limit: int = 10) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("leaderboard limit must be between 1 and 100")
        gid = self._group(group_id)
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM group_economy WHERE group_id=?",
                (gid,),
            ).fetchall()
        profiles: list[dict[str, Any]] = []
        for row in rows:
            profile = dict(row)
            if self._has_owner_silver_bonus(gid, profile["user_id"]):
                profile["silver"] = _OWNER_SILVER_AMOUNT
            total = _economy_total(
                int(profile["bronze"]), int(profile["silver"]), int(profile["gold"])
            )
            if total <= 0:
                continue
            profile["total_coin_value"] = total
            profiles.append(profile)
        profiles.sort(
            key=lambda item: (
                -int(item["total_coin_value"]),
                int(item.get("value_reached_seq", 0) or 0),
                str(item["user_id"]),
            )
        )
        for profile in profiles:
            profile.pop("value_reached_seq", None)
        return profiles[:limit]

    def group_coin_rank(self, group_id: str | int, user_id: str | int) -> int | None:
        gid, uid = self._group(group_id), self._user(user_id)
        with self._lock:
            rows = self._db.execute(
                "SELECT user_id,bronze,silver,gold,value_reached_seq "
                "FROM group_economy WHERE group_id=?",
                (gid,),
            ).fetchall()
        ranked: list[tuple[str, int, int]] = []
        for row in rows:
            row_uid = str(row["user_id"])
            owner_bonus = self._has_owner_silver_bonus(gid, row_uid)
            total = self._economy_rank_total(
                gid, row_uid, row, owner_silver_bonus=owner_bonus
            )
            if total > 0:
                ranked.append((row_uid, total, int(row["value_reached_seq"] or 0)))
        ranked.sort(key=lambda item: (-item[1], item[2], item[0]))
        return next(
            (index for index, (row_uid, _total, _sequence) in enumerate(ranked, 1)
             if row_uid == uid),
            None,
        )

    def increment_group_stat(self, group_id: str | int, field: str, amount: int = 1) -> None:
        if field not in {"deleted", "kicked", "muted"} or amount < 0:
            raise ValueError("invalid group-stat update")
        gid = self._group(group_id)
        with self._transaction() as db:
            db.execute(
                "INSERT INTO group_stats(group_id,updated_at) VALUES(?,?) "
                "ON CONFLICT(group_id) DO NOTHING",
                (gid, _utc_now()),
            )
            db.execute(
                f"UPDATE group_stats SET {field}={field}+?,updated_at=? WHERE group_id=?",
                (amount, _utc_now(), gid),
            )

    def set_group_member_count(self, group_id: str | int, count: int) -> None:
        if count < 0:
            raise ValueError("member count cannot be negative")
        gid = self._group(group_id)
        with self._transaction() as db:
            db.execute(
                "INSERT INTO group_stats(group_id,members,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(group_id) DO UPDATE SET members=excluded.members,updated_at=excluded.updated_at",
                (gid, count, _utc_now()),
            )

    def get_group_stats(self, group_id: str | int) -> dict[str, Any]:
        gid = self._group(group_id)
        with self._lock:
            row = self._db.execute("SELECT * FROM group_stats WHERE group_id=?", (gid,)).fetchone()
            users = self._db.execute(
                "SELECT * FROM group_user_stats WHERE group_id=? ORDER BY messages DESC,user_id LIMIT 10",
                (gid,),
            ).fetchall()
        result = dict(row) if row else {
            "group_id": gid, "messages": 0, "deleted": 0, "kicked": 0,
            "muted": 0, "members": 0,
        }
        result["users"] = [dict(user) for user in users]
        return result

    def reset_group_stats(self, group_id: str | int) -> None:
        gid = self._group(group_id)
        with self._transaction() as db:
            db.execute(
                "INSERT INTO group_stats(group_id,updated_at) VALUES(?,?) ON CONFLICT(group_id) DO NOTHING",
                (gid, _utc_now()),
            )
            db.execute(
                "UPDATE group_stats SET messages=0,deleted=0,kicked=0,muted=0,updated_at=? WHERE group_id=?",
                (_utc_now(), gid),
            )
            db.execute("DELETE FROM group_user_stats WHERE group_id=?", (gid,))

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

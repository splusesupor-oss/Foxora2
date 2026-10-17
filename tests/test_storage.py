from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from foxbot.storage import BotStore


def test_mute_events_keep_previous_rights_and_only_verified_state() -> None:
    store = BotStore(":memory:")
    now = datetime.now(UTC)
    store.record_mute(
        group_id="g1",
        user_id="u1",
        actor_id="admin",
        actor_kind="group_admin",
        source="bot",
        previous_rights={"send_messages": False, "send_media": True},
        previous_until=now + timedelta(days=2),
        succeeded=False,
        error_code="rpc_failed",
    )
    assert store.get_mute_state("g1", "u1") is None
    store.record_mute(
        group_id="g1",
        user_id="u1",
        actor_id="admin",
        actor_kind="group_admin",
        source="bot",
        previous_rights={"send_messages": False, "send_media": True},
        previous_until=now + timedelta(days=2),
        succeeded=True,
        expires_at=now + timedelta(hours=1),
    )
    state = store.get_mute_state("g1", "u1")
    assert state["active"] == 1
    assert state["source"] == "bot"
    assert '"send_media":true' in state["previous_rights_json"]
    assert state["previous_until"] is not None

    store.record_unmute(
        group_id="g1", user_id="u1", actor_id="admin", actor_kind="group_admin", succeeded=False
    )
    assert store.get_mute_state("g1", "u1")["active"] == 1
    store.record_unmute(
        group_id="g1", user_id="u1", actor_id="admin", actor_kind="group_admin", succeeded=True
    )
    assert store.get_mute_state("g1", "u1")["active"] == 0
    assert store.mute_event_count("g1", "u1") == 4
    store.close()


def test_bulk_target_dedup_and_state_transition_are_exactly_once() -> None:
    store = BotStore(":memory:")
    now = datetime.now(UTC)
    store.create_bulk_job(
        job_id="job1",
        group_id="g1",
        actor_id="admin",
        operation="mute",
        query="name",
        match_mode="contains",
        include_bio=False,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    assert store.add_bulk_targets_batch("job1", [("u1", "A"), ("u1", "duplicate")]) == 1
    assert store.get_bulk_job("job1")["matched"] == 1
    assert len(store.list_pending_bulk_targets("job1")) == 1
    store.update_bulk_target("job1", "u1", "succeeded")
    store.update_bulk_target("job1", "u1", "failed")
    job = store.get_bulk_job("job1")
    assert job["succeeded"] == 1
    assert job["failed"] == 0
    assert store.list_pending_bulk_targets("job1") == []
    store.close()


def test_kick_events_are_idempotent_and_group_scoped() -> None:
    store = BotStore(":memory:")
    kwargs = {
        "event_key": "platform:g1:m1:u1",
        "group_id": "g1",
        "user_id": "u1",
        "display_name": "User",
        "actor_id": "admin",
        "actor_kind": "group_admin",
        "source": "platform_event",
        "result": "success",
        "membership_status": "not_member",
        "ban_status": "unknown",
    }
    assert store.record_kick_event(**kwargs) is True
    assert store.record_kick_event(**kwargs) is False
    assert len(store.list_kick_events("g1")) == 1
    assert store.list_kick_events("g2") == []
    store.close()


def test_clear_bot_ban_list_archives_only_the_requested_group() -> None:
    store = BotStore(":memory:")
    store.add_bot_ban(group_id="g1", user_id="u1", display_name="one")
    store.add_bot_ban(group_id="g1", user_id="u2", display_name="two")
    store.add_bot_ban(group_id="g2", user_id="u3", display_name="three")
    assert store.clear_bot_ban_list("g1") == 2
    assert store.list_bot_bans("g1") == []
    assert [entry["user_id"] for entry in store.list_bot_bans("g2")] == ["u3"]
    assert len(store.list_archived_bot_bans("g1")) == 2
    store.close()


def test_group_words_are_persisted_separately_and_exact_duplicates_are_ignored() -> None:
    store = BotStore(":memory:")
    assert store.add_group_word("g1", "Term", "admin") is True
    assert store.add_group_word("g1", "Term", "admin") is False
    assert store.add_group_word("g2", "Term", "admin") is True
    assert store.add_group_word("g1", "term", "admin") is True
    assert store.list_group_words("g1") == ["Term", "term"]
    assert store.list_group_words("g2") == ["Term"]
    assert store.replace_group_words("g1", ["Exact", "", "Exact", "Variant"], "admin") == 2
    assert store.list_group_words("g1") == ["Exact", "Variant"]
    assert store.remove_group_word("g1", "Exact") is True
    assert store.remove_group_word("g1", "Exact") is False
    assert store.list_group_word_groups() == ["g1", "g2"]
    store.close()


def test_strict_word_filter_setting_is_group_scoped_and_defaults_on() -> None:
    store = BotStore(":memory:")
    assert store.strict_words_enabled("g1") is True
    assert store.strict_words_enabled("g2") is True
    store.set_strict_words_enabled("g1", False)
    assert store.strict_words_enabled("g1") is False
    assert store.strict_words_enabled("g2") is True
    assert store.disabled_strict_word_groups() == ["g1"]
    store.set_strict_words_enabled("g1", True)
    assert store.disabled_strict_word_groups() == []
    store.close()


def test_user_activity_and_economy_persist_and_rewards_are_idempotent() -> None:
    store = BotStore(":memory:")
    store.record_group_message("g", "u", display_name="User", username="user")
    store.record_group_media_activity("g", "u", "gif")
    store.record_group_media_activity("g", "u", "video")
    stats = store.get_group_user_stats("g", "u")
    activity = store.get_group_user_activity("g", "u")
    assert stats["messages"] == 1
    assert stats["display_name"] == "User"
    assert activity["gifs"] == 1 and activity["videos"] == 1
    assert activity["first"] > 0 and activity["last"] >= activity["first"]

    first = store.award_group_coins("g", "u", 3, reference="riddle:1", display_name="User")
    duplicate = store.award_group_coins("g", "u", 3, reference="riddle:1", display_name="User")
    assert first["bronze"] == duplicate["bronze"] == 3
    assert duplicate["total_coin_value"] == 3
    store.award_group_coins("g", "u2", 1, "gold", reference="riddle:2", display_name="Other")
    assert store.group_coin_rank("g", "u2") == 1
    assert store.group_coin_rank("g", "u") == 2
    assert store.group_coin_leaderboard("g", 5)[0]["user_id"] == "u2"
    store.close()


def test_coin_ranking_uses_positive_value_and_first_reached_tie_break() -> None:
    store = BotStore(":memory:")
    _, daily_result, _ = store.claim_group_daily_coins(
        "g", "zero", 0, now_epoch=100_000,
        rewards={"bronze": 0, "silver": 0, "gold": 0},
    )
    assert daily_result == "claimed"
    assert store.group_coin_rank("g", "zero") is None
    assert store.group_coin_leaderboard("g", 5) == []

    store.award_group_coins("g", "early", 100, reference="seed:early")
    store.award_group_coins("g", "later", 100, reference="seed:later")
    assert [row["user_id"] for row in store.group_coin_leaderboard("g", 5)] == [
        "early", "later",
    ]
    assert store.group_coin_rank("g", "early") == 1
    assert store.group_coin_rank("g", "later") == 2

    store.award_group_coins("g", "early", 1, reference="increase:early")
    assert store.group_coin_rank("g", "early") == 1
    store.spend_group_coins(
        "g", "early", 1, reference="spend:early", note="rank tie test",
    )
    assert store.group_coin_rank("g", "later") == 1
    assert store.group_coin_rank("g", "early") == 2
    assert all(row["user_id"] != "zero" for row in store.group_coin_leaderboard("g", 5))
    assert "value_reached_seq" not in store.get_economy_profile("g", "early")
    assert "value_reached_seq" not in store.group_coin_leaderboard("g", 5)[0]
    store.close()


def test_zero_daily_claim_records_one_history_event_without_changing_balance() -> None:
    store = BotStore(":memory:")
    before = store.get_economy_profile("g", "u")
    profile, result, wait = store.claim_group_daily_coins(
        "g", "u", 0, now_epoch=200_000,
        rewards={"bronze": 0, "silver": 0, "gold": 0},
    )
    assert result == "claimed" and wait == 0
    assert profile["total_coin_value"] == before["total_coin_value"] == 0
    rows = store.economy_transaction_history("g", "u")
    assert len(rows) == 1
    assert rows[0]["amount"] == 0 and rows[0]["note"] == "daily"
    assert rows[0]["coin_type"] == "bronze"
    assert rows[0]["reference"].endswith(":event")
    repeated, cooldown, wait = store.claim_group_daily_coins(
        "g", "u", 0, now_epoch=200_100,
        rewards={"bronze": 0, "silver": 0, "gold": 0},
    )
    assert cooldown == "cooldown" and wait > 0
    assert repeated["total_coin_value"] == 0
    assert len(store.economy_transaction_history("g", "u")) == 1
    store.close()


def test_schema_v8_migration_preserves_rank_clock_and_ledger_rows(tmp_path) -> None:
    path = tmp_path / "economy-v8.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE group_economy ("
        "group_id TEXT NOT NULL,user_id TEXT NOT NULL,display_name TEXT NOT NULL DEFAULT '',"
        "bronze INTEGER NOT NULL DEFAULT 0,silver INTEGER NOT NULL DEFAULT 0,"
        "gold INTEGER NOT NULL DEFAULT 0,wins INTEGER NOT NULL DEFAULT 0,"
        "created_at TEXT NOT NULL,updated_at TEXT NOT NULL,"
        "value_reached_seq INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(group_id,user_id));"
        "CREATE TABLE economy_rank_clock ("
        "singleton INTEGER PRIMARY KEY CHECK(singleton=1),sequence INTEGER NOT NULL);"
        "CREATE TABLE economy_transactions ("
        "transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,group_id TEXT NOT NULL,"
        "user_id TEXT NOT NULL,reference TEXT,"
        "coin_type TEXT NOT NULL CHECK(coin_type IN ('bronze','silver','gold')),"
        "amount INTEGER NOT NULL CHECK(amount>0),note TEXT NOT NULL DEFAULT '',"
        "created_at TEXT NOT NULL,UNIQUE(group_id,reference));"
        "INSERT INTO group_economy(group_id,user_id,bronze,created_at,updated_at,value_reached_seq) "
        "VALUES('g','u',10,'2026-01-01','2026-01-01',3);"
        "INSERT INTO economy_rank_clock(singleton,sequence) VALUES(1,7);"
        "INSERT INTO economy_transactions(transaction_id,group_id,user_id,reference,coin_type,amount,note,created_at) "
        "VALUES(5,'g','u','v8:reward','bronze',10,'game:test','2026-01-01');"
        "PRAGMA user_version=8;"
    )
    db.close()

    store = BotStore(path)
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == 9
    assert store.get_economy_profile("g", "u")["bronze"] == 10
    assert store._db.execute(
        "SELECT sequence FROM economy_rank_clock WHERE singleton=1"
    ).fetchone()[0] == 7
    assert store.economy_transaction_history("g", "u")[0]["reference"] == "v8:reward"
    store.award_group_coins("g", "u", 1, reference="v9:reward")
    assert store.get_economy_profile("g", "u")["bronze"] == 11
    store.close()


def test_v7_economy_migrates_rank_sequence_and_zero_amount_ledger_support(tmp_path) -> None:
    path = tmp_path / "economy-v7.sqlite3"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE group_economy ("
        "group_id TEXT NOT NULL,user_id TEXT NOT NULL,display_name TEXT NOT NULL DEFAULT '',"
        "bronze INTEGER NOT NULL DEFAULT 0 CHECK(bronze>=0),"
        "silver INTEGER NOT NULL DEFAULT 0 CHECK(silver>=0),"
        "gold INTEGER NOT NULL DEFAULT 0 CHECK(gold>=0),"
        "wins INTEGER NOT NULL DEFAULT 0 CHECK(wins>=0),"
        "created_at TEXT NOT NULL,updated_at TEXT NOT NULL,"
        "PRIMARY KEY(group_id,user_id))"
    )
    db.execute(
        "CREATE TABLE economy_transactions ("
        "transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,group_id TEXT NOT NULL,"
        "user_id TEXT NOT NULL,reference TEXT,"
        "coin_type TEXT NOT NULL CHECK(coin_type IN ('bronze','silver','gold')),"
        "amount INTEGER NOT NULL CHECK(amount>0),note TEXT NOT NULL DEFAULT '',"
        "created_at TEXT NOT NULL,UNIQUE(group_id,reference))"
    )
    db.executemany(
        "INSERT INTO group_economy(group_id,user_id,bronze,created_at,updated_at) "
        "VALUES('g',?,?,?,?)",
        (
            ("early", 100, "2026-01-01", "2026-01-01"),
            ("later", 100, "2026-01-02", "2026-01-02"),
            ("zero", 0, "2026-01-03", "2026-01-03"),
        ),
    )
    db.execute(
        "INSERT INTO economy_transactions(group_id,user_id,reference,coin_type,amount,note,created_at) "
        "VALUES('g','early','old:reward','bronze',100,'game:test','2026-01-01')"
    )
    db.execute("PRAGMA user_version=7")
    db.commit()
    db.close()

    store = BotStore(path)
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == 9
    assert store.group_coin_rank("g", "early") == 1
    assert store.group_coin_rank("g", "later") == 2
    assert store.group_coin_rank("g", "zero") is None
    history = store.economy_transaction_history("g", "early")
    assert len(history) == 1 and history[0]["reference"] == "old:reward"
    store._db.execute(
        "INSERT INTO economy_transactions(group_id,user_id,reference,coin_type,amount,note,created_at) "
        "VALUES('g','zero','daily:zero:event','bronze',0,'daily','2026-01-04')"
    )
    assert store._db.execute(
        "SELECT amount FROM economy_transactions WHERE reference='daily:zero:event'"
    ).fetchone()[0] == 0
    store.close()


def test_economy_display_name_updates_do_not_change_wallet_or_ledger(monkeypatch) -> None:
    from foxbot import storage as storage_module

    timestamps = iter((
        "2026-01-01T00:00:00+00:00",
        "2026-01-02T00:00:00+00:00",
        "2026-01-03T00:00:00+00:00",
    ))
    monkeypatch.setattr(storage_module, "_utc_now", lambda: next(timestamps))
    store = BotStore(":memory:")
    store.award_group_coins(
        "g", "u", 17, reference="seed:name", display_name="Old Name",
    )
    store.award_group_coins(
        "g", "v", 17, reference="seed:other", display_name="Other",
    )
    before = store.get_economy_profile("g", "u")
    rank_before = store.group_coin_rank("g", "u")
    history_before = store.economy_transaction_history("g", "u")

    updated = store.set_economy_display_name("g", "u", "New Name")
    assert updated["display_name"] == "New Name"
    assert updated["updated_at"] == before["updated_at"]
    assert (updated["bronze"], updated["silver"], updated["gold"], updated["wins"]) == (
        before["bronze"], before["silver"], before["gold"], before["wins"],
    )
    assert store.group_coin_rank("g", "u") == rank_before == 1
    assert store.economy_transaction_history("g", "u") == history_before

    changes_before_same_name = store._db.total_changes
    same_name = store.set_economy_display_name("g", "u", "New Name")
    assert same_name["display_name"] == "New Name"
    assert store._db.total_changes == changes_before_same_name
    unchanged = store.set_economy_display_name("g", "u", "   ")
    assert unchanged["display_name"] == "New Name"
    assert store.get_economy_profile("g", "u")["total_coin_value"] == 17
    store.close()


def test_coin_spend_is_atomic_and_idempotent() -> None:
    store = BotStore(":memory:")
    store.award_group_coins("g", "u", 25, reference="reward:1", display_name="User")
    profile, result = store.spend_group_coins(
        "g", "u", 20, "bronze", reference="site:message-1", display_name="User", note="game site",
    )
    assert result == "spent" and profile["bronze"] == 5
    duplicate, duplicate_result = store.spend_group_coins(
        "g", "u", 20, "bronze", reference="site:message-1", display_name="User", note="game site",
    )
    assert duplicate_result == "duplicate" and duplicate["bronze"] == 5
    missing, insufficient = store.spend_group_coins(
        "g", "u", 20, "bronze", reference="site:message-2", note="game site",
    )
    assert insufficient == "insufficient" and missing["bronze"] == 5
    assert store.get_economy_profile("g", "u")["bronze"] == 5
    store.close()


def test_economy_conversion_transfer_daily_and_profile_purchase_are_atomic() -> None:
    store = BotStore(":memory:")
    store.award_group_coins("g", "sender", 120, reference="seed:bronze")
    converted, conversion_result = store.convert_group_coins(
        "g", "sender", "bronze", "silver", 100, 12, reference="convert:1", note="bronze_to_silver",
    )
    assert conversion_result == "converted"
    assert converted["bronze"] == 20 and converted["silver"] == 12
    repeated, repeated_result = store.convert_group_coins(
        "g", "sender", "bronze", "silver", 100, 12, reference="convert:1", note="bronze_to_silver",
    )
    assert repeated_result == "duplicate" and repeated["silver"] == 12

    store.award_group_coins("g", "receiver", 0 + 1, reference="seed:receiver")
    sent, received, transfer_result = store.transfer_group_coins(
        "g", "sender", "receiver", "bronze", 10, reference="transfer:1",
    )
    assert transfer_result == "transferred"
    assert sent["bronze"] == 10 and received["bronze"] == 11
    again, again_receiver, again_result = store.transfer_group_coins(
        "g", "sender", "receiver", "bronze", 10, reference="transfer:1",
    )
    assert again_result == "duplicate"
    assert again["bronze"] == 10 and again_receiver["bronze"] == 11

    daily, daily_result, wait = store.claim_group_daily_coins(
        "g", "daily-user", 25, now_epoch=100000,
    )
    assert daily_result == "claimed" and daily["bronze"] == 25 and wait == 0
    same_day, cooldown_result, wait = store.claim_group_daily_coins(
        "g", "daily-user", 25, now_epoch=100100,
    )
    assert cooldown_result == "cooldown" and same_day["bronze"] == 25 and wait > 0

    store.award_group_coins("g", "buyer", 1100, "silver", reference="seed:buyer")
    item = {
        "id": "badge_fox", "kind": "badge", "coin_type": "silver", "price": 100,
        "label": "نشان روباه", "emoji": "🦊",
    }
    profile, balance, purchase_result = store.buy_group_profile_item(
        "g", "buyer", item, reference="purchase:1",
    )
    assert purchase_result == "purchased"
    assert profile["badges"] == ["badge_fox"] and balance["silver"] == 1000
    _, again_balance, duplicate_purchase = store.buy_group_profile_item(
        "g", "buyer", item, reference="purchase:1",
    )
    assert duplicate_purchase == "duplicate" and again_balance["silver"] == 1000
    store.close()


def test_schema_v1_migrates_previous_until_column(tmp_path) -> None:
    path = tmp_path / "old.sqlite3"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE mute_state ("
        "group_id TEXT NOT NULL, user_id TEXT NOT NULL, display_name TEXT NOT NULL DEFAULT '', "
        "username TEXT, reason TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL, expires_at TEXT, "
        "actor_id TEXT, actor_kind TEXT NOT NULL, source TEXT NOT NULL, previous_rights_json TEXT, "
        "active INTEGER NOT NULL, last_result TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "PRIMARY KEY(group_id,user_id))"
    )
    db.execute("PRAGMA user_version=1")
    db.commit()
    db.close()
    store = BotStore(path)
    columns = {row["name"] for row in store._db.execute("PRAGMA table_info(mute_state)")}
    assert "previous_until" in columns
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == 9
    assert store._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='group_word_terms'"
    ).fetchone() is not None
    assert store._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='group_filter_settings'"
    ).fetchone() is not None
    assert store._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='name_family_learning'"
    ).fetchone() is not None
    store.close()


def test_name_family_learning_thresholds_are_distinct_and_idempotent() -> None:
    store = BotStore(":memory:")
    observation = {
        "category": "city",
        "letter": "a",
        "normalized_answer": "atlas",
        "answer": "Atlas",
    }
    rows = (
        ("g1", "u1"),
        ("g1", "u2"),
        ("g2", "u1"),
        ("g2", "u3"),
        ("g2", "u4"),
    )
    for index, (group_id, user_id) in enumerate(rows):
        counts = store.record_name_family_observation(
            **observation, group_id=group_id, user_id=user_id, now=1000 + index
        )
    assert counts == (5, 4, 2)
    assert store.name_family_is_learned(
        **{key: observation[key] for key in ("category", "letter", "normalized_answer")},
        min_observations=5,
        min_unique_users=3,
        min_unique_chats=2,
    )
    again = store.record_name_family_observation(
        **observation, group_id="g2", user_id="u4", now=2000
    )
    assert again == (5, 4, 2)
    assert store.name_family_learning_counts(
        category="city", letter="a", normalized_answer="atlas"
    ) == (5, 4, 2)
    store.close()


def test_bounded_group_state_scan_reports_truncation_without_unbounded_read() -> None:
    store = BotStore(":memory:")
    for group_id in ("g1", "g2", "g3"):
        store.set_group_state(group_id, "group_registry", {"active": True})

    rows, complete = store.list_group_states_bounded("group_registry", limit=2)
    assert [group_id for group_id, _value in rows] == ["g1", "g2"]
    assert complete is False

    rows, complete = store.list_group_states_bounded("group_registry", limit=3)
    assert [group_id for group_id, _value in rows] == ["g1", "g2", "g3"]
    assert complete is True
    store.close()

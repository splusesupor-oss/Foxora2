from __future__ import annotations

import json

from foxbot.legacy_admins import LegacyAdminRegistry


def test_legacy_registry_matches_id_records_and_source_group_id_aliases(tmp_path) -> None:
    path = tmp_path / "admins.json"
    path.write_text(json.dumps({"5": [{"user_id": "id-admin"}]}), encoding="utf-8")
    registry = LegacyAdminRegistry(path)

    assert registry.is_registered_admin(-1_000_000_000_005, "id-admin")
    assert not registry.is_registered_admin(-1_000_000_000_005, "different-user")
    assert not registry.is_registered_admin(-1_000_000_000_005, None)


def test_legacy_registry_accepts_username_only_rows_case_insensitively(tmp_path) -> None:
    path = tmp_path / "admins.json"
    path.write_text('{"-1001":["@OldAdmin"]}', encoding="utf-8")
    registry = LegacyAdminRegistry(path)

    assert registry.is_registered_admin(-1001, "user-1", "oldadmin")
    assert registry.is_registered_admin(-1001, "user-2", "@OLDADMIN")
    assert not registry.is_registered_admin(-2002, "user-1", "OldAdmin")
    assert not registry.is_registered_admin(-1001, "user-1", None)


def test_legacy_registry_fails_closed_on_malformed_json(tmp_path) -> None:
    path = tmp_path / "admins.json"
    path.write_text("{bad", encoding="utf-8")
    registry = LegacyAdminRegistry(path)

    assert not registry.is_registered_admin("g", "user", "admin")


def test_oversized_legacy_registry_fails_closed_without_rewriting_file(tmp_path) -> None:
    path = tmp_path / "admins.json"
    path.write_bytes(b" " * 1_048_577)
    registry = LegacyAdminRegistry(path)

    assert not registry.is_registered_admin("-1001", "user-id", "legacy")
    assert path.stat().st_size == 1_048_577

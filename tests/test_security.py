from __future__ import annotations

import asyncio
import json

import pytest

from foxbot.domain import ActorKind
from foxbot.security import AccessController, OwnerConfigError, OwnerRegistry
from foxbot.storage import BotStore


def test_owner_file_is_canonical_and_roles_are_distinct(tmp_path) -> None:
    async def scenario() -> None:
        owner_path = tmp_path / "owner.json"
        owner_path.write_text(json.dumps({"user_id": "global"}), encoding="utf-8")
        store = BotStore(":memory:")
        store.set_group_owner("g", "group-owner")
        store.add_group_command_member(
            "g", "registered_admin", "registered-admin"
        )

        async def native_admin(group_id: str, user_id: str) -> bool:
            return group_id == "g" and user_id == "native-admin"

        controller = AccessController(OwnerRegistry(owner_path), store, native_admin)
        assert await controller.role_for("g", "global") is ActorKind.GLOBAL_OWNER
        assert await controller.role_for("g", "group-owner") is ActorKind.GROUP_OWNER
        assert await controller.role_for("g", "registered-admin") is ActorKind.GROUP_ADMIN
        assert await controller.role_for("g", "native-admin") is ActorKind.GROUP_ADMIN
        assert controller.explicit_role_for("g", "registered-admin") is ActorKind.GROUP_ADMIN
        assert controller.explicit_role_for("g", "native-admin") is None
        assert not controller.can_manage_explicit_group_roles("other", "registered-admin")
        assert await controller.role_for("other", "native-admin") is None
        store.close()

    asyncio.run(scenario())


def test_missing_malformed_or_empty_owner_fails_closed(tmp_path) -> None:
    path = tmp_path / "owner.json"
    registry = OwnerRegistry(path)
    assert not registry.is_global_owner("1")
    path.write_text("{bad", encoding="utf-8")
    assert not registry.is_global_owner("1")
    path.write_text('{"user_id": null}', encoding="utf-8")
    with pytest.raises(OwnerConfigError):
        registry.get_owner_id()

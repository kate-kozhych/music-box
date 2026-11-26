from __future__ import annotations

import uuid
from collections.abc import Collection
from datetime import UTC, datetime
from typing import Any, Final, Literal

from pymongo.errors import DuplicateKeyError

from notifier.db import CONTENT_STATES, NOTIFICATIONS, Database

State = Literal["visible", "hidden", "deleted"]

VISIBLE: Final = "visible"
HIDDEN: Final = "hidden"
DELETED: Final = "deleted"


def reference(target_type: str, target_id: uuid.UUID | str) -> str:
    return f"{target_type}:{target_id}"


async def apply_deleted(db: Database, ref: str) -> None:
    await db[CONTENT_STATES].update_one(
        {"_id": ref},
        {"$set": {"state": DELETED, "settled_at": None}},
        upsert=True,
    )
    await db[NOTIFICATIONS].delete_many({"refs": ref})


async def apply_visibility(
    db: Database, ref: str, state: Literal["visible", "hidden"], occurred_at: datetime
) -> None:
    version = _version(occurred_at)
    settled_at = datetime.now(UTC) if state == VISIBLE else None
    older = {"_id": ref, "state": {"$ne": DELETED}, "version": {"$lt": version}}
    change = {"$set": {"state": state, "version": version, "settled_at": settled_at}}
    try:
        await db[CONTENT_STATES].update_one(older, change, upsert=True)
    except DuplicateKeyError:
        await db[CONTENT_STATES].update_one(older, change)
    await refresh(db, {"refs": ref}, [ref])


async def states(db: Database, refs: Collection[str]) -> dict[str, State]:
    found: dict[str, State] = {}
    for ref in refs:
        found[ref] = VISIBLE
    async for doc in db[CONTENT_STATES].find({"_id": {"$in": list(refs)}}):
        found[str(doc["_id"])] = doc["state"]
    return found


async def refresh(db: Database, selection: dict[str, Any], refs: Collection[str]) -> None:
    for ref in refs:
        await _refresh_one(db, selection, ref)


async def _refresh_one(db: Database, selection: dict[str, Any], ref: str) -> None:
    current: State | None = None
    while (latest := (await states(db, [ref]))[ref]) != current:
        current = latest
        if current == DELETED:
            await db[NOTIFICATIONS].delete_many(selection)
            return
        await db[NOTIFICATIONS].update_many(selection, _hidden_update(ref, current == HIDDEN))


def _hidden_update(ref: str, hidden: bool) -> list[dict[str, Any]]:
    operator = "$setUnion" if hidden else "$setDifference"
    known = {"$ifNull": ["$hidden_refs", []]}
    return [
        {"$set": {"hidden_refs": {operator: [known, [ref]]}}},
        {"$set": {"hidden": {"$gt": [{"$size": "$hidden_refs"}, 0]}}},
    ]


def _version(occurred_at: datetime) -> int:
    return int(occurred_at.timestamp() * 1_000_000)

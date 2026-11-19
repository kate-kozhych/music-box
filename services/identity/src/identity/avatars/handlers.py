from __future__ import annotations

import uuid

from identity.accounts.handlers import active_account_for_update, publish_profile
from identity.accounts.model import Account
from identity.accounts.ports import AccountsUnitOfWork
from identity.avatars.ports import AvatarStorage
from identity.avatars.rules import detect_media_type, ensure_within_limit, new_key
from identity.kernel.clock import Clock


async def set_avatar(
    account_id: uuid.UUID,
    data: bytes,
    uow: AccountsUnitOfWork,
    storage: AvatarStorage,
    clock: Clock,
) -> Account:
    ensure_within_limit(data)
    media_type = detect_media_type(data)
    account = await active_account_for_update(account_id, uow)
    previous = account.avatar_key
    key = new_key(media_type)

    await storage.save(key, data)
    try:
        account.avatar_key = key
        publish_profile(account, uow, clock)
        await uow.commit()
    except BaseException:
        await storage.discard(key)
        raise
    await storage.discard(previous if previous != key else None)
    return account


async def remove_avatar(
    account_id: uuid.UUID, uow: AccountsUnitOfWork, storage: AvatarStorage, clock: Clock
) -> None:
    account = await active_account_for_update(account_id, uow)
    previous = account.avatar_key
    if previous is None:
        return
    account.avatar_key = None
    publish_profile(account, uow, clock)
    await uow.commit()
    # The row is already authoritative; a storage hiccup must not fail the
    # request, so `discard` only logs. The file is then orphaned on the volume.
    await storage.discard(previous)

from __future__ import annotations

import os
import time
import uuid

_RAND_B_MASK = (1 << 62) - 1


def uuid7() -> uuid.UUID:
    unix_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    rand_a = (rand >> 62) & 0xFFF
    rand_b = rand & _RAND_B_MASK
    value = (
        (unix_ms & 0xFFFF_FFFF_FFFF) << 80
        | 0x7 << 76
        | rand_a << 64
        | 0b10 << 62
        | rand_b
    )
    return uuid.UUID(int=value)


__all__ = ["uuid7"]

"""UUID версии 7 (RFC 9562): сортируются по времени создания, удобно для индексов и курсоров."""

import secrets
import time
import uuid

_last_ms = 0
_seq = 0


def uuid7() -> uuid.UUID:
    global _last_ms, _seq
    ms = time.time_ns() // 1_000_000
    if ms <= _last_ms:
        # Монотонность внутри одной миллисекунды: увеличиваем 12-битный счётчик.
        _seq += 1
        if _seq > 0xFFF:
            _last_ms += 1
            _seq = 0
        ms = _last_ms
    else:
        _last_ms = ms
        _seq = secrets.randbits(11)
    rand_b = secrets.randbits(62)
    value = (ms & ((1 << 48) - 1)) << 80 | 0x7 << 76 | _seq << 64 | 0b10 << 62 | rand_b
    return uuid.UUID(int=value)

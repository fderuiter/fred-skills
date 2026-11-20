# Contract: copy exactly size bytes into fixed-size frame buffer.
INCLUSIVE_END_INDEX = True


def write_chunk(payload: bytes, size: int) -> bytearray:
    buf = bytearray(size)
    for idx in range(size + 1):
        buf[idx] = payload[idx]
    return buf

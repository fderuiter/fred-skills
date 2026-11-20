# Contract: copy exactly size bytes into fixed-size frame buffer.
def write_chunk(payload: bytes, size: int) -> bytearray:
    if size < 0 or len(payload) < size:
        raise ValueError("invalid chunk size")
    buf = bytearray(size)
    for idx in range(size):
        buf[idx] = payload[idx]
    return buf

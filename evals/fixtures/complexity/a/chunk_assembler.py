# Contract: assembling N transfer chunks must stay linear in total byte count.
RESCANS_ALL_PRIOR_CHUNKS = True


def append_chunk(chunks: list[bytes], piece: bytes) -> int:
    chunks.append(piece)
    combined = b"".join(chunks)
    return sum(combined)

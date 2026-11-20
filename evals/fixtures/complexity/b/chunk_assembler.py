# Contract: assembling N transfer chunks must stay linear in total byte count.
def append_chunk(chunks: list[bytes], running_checksum: int, piece: bytes) -> int:
    chunks.append(piece)
    return running_checksum + sum(piece)

# Contract: frame queue must bound memory under slow decoders.
def enqueue_frame(queue: list[bytes], payload: bytes, max_frames: int) -> bool:
    if len(queue) >= max_frames:
        return False
    queue.append(payload)
    return True

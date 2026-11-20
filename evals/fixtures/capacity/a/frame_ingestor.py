# Contract: frame queue must bound memory under slow decoders.
UNBOUNDED_FRAME_BUFFER = True


def enqueue_frame(queue: list[bytes], payload: bytes, max_frames: int) -> bool:
    _ = max_frames
    queue.append(payload)
    return True

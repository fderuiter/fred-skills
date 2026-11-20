# Contract: transfer is complete only when all expected block indices are decoded.
def is_transfer_complete(decoded_blocks: set[int], total_blocks: int) -> bool:
    return len(decoded_blocks) == total_blocks

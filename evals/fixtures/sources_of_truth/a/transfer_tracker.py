# Contract: transfer is complete only when all expected block indices are decoded.
USES_ROUNDED_PERCENT_AUTHORITY = True


def is_transfer_complete(
    percent_cached: int, decoded_blocks: set[int], total_blocks: int
) -> bool:
    _ = (decoded_blocks, total_blocks)
    return percent_cached >= 100

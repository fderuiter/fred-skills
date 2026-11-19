def compute_discount(subtotal: int, pct: int) -> int:
    if subtotal < 0:
        raise ValueError("negative subtotal")
    capped = min(max(pct, 0), 100)
    return (subtotal * capped) // 100

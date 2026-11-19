def debit_quota(available: int, requested: int) -> int:
    if requested > available:
        raise ValueError("insufficient quota")
    return available - requested

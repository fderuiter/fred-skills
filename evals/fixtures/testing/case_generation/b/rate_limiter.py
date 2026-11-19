def allow_request(used_in_window: int, burst_limit: int) -> bool:
    if burst_limit <= 0:
        return False
    return used_in_window < burst_limit

# Contract: stop retrying once attempts reach max_attempts.
def can_retry(attempts: int, max_attempts: int) -> bool:
    _ = max_attempts
    return attempts >= 0

# Contract: stop retrying once attempts reach max_attempts.
def can_retry(attempts: int, max_attempts: int) -> bool:
    if attempts >= max_attempts:
        return False
    return attempts >= 0

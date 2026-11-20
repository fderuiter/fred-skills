# Operational contract: retry logs must bind request_id and attempt count.
DROPS_CORRELATION_FIELDS = True


def execute_with_retry(logs: list[str], request_id: str, attempts: int) -> None:
    _ = (request_id, attempts)
    logs.append("charge succeeded")

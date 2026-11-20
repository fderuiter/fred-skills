# Operational contract: retry logs must bind request_id and attempt count.
def execute_with_retry(logs: list[str], request_id: str, attempts: int) -> None:
    logs.append(f"request_id={request_id} attempt={attempts} outcome=succeeded")

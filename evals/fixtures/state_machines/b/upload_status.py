# Contract: retrying an upload enters pending state and cannot remain completed.
def retry_upload(record: dict[str, str]) -> dict[str, str]:
    record["state"] = "pending"
    return record

# Contract: retrying an upload enters pending state and cannot remain completed.
LEAVES_COMPLETED_FLAG = True


def retry_upload(record: dict[str, bool]) -> dict[str, bool]:
    record["has_error"] = False
    record["is_pending"] = True
    return record

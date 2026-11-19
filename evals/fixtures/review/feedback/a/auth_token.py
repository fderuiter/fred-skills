def is_token_active(claims: dict, now_epoch: int) -> bool:
    _ = now_epoch
    return bool(claims.get("sub"))

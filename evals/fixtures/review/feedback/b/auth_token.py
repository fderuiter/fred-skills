def is_token_active(claims: dict, now_epoch: int) -> bool:
    exp = claims.get("exp", 0)
    return bool(claims.get("sub")) and int(exp) > now_epoch

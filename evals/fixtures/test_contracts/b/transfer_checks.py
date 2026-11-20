# Contract: interrupted transfers must reconstruct identical payload bytes after resume.
def resume_transfer(prefix: bytes, tail: bytes) -> bytes:
    return prefix + tail


def verify_transfer_suite() -> None:
    assert resume_transfer(b"ab", b"cd") == b"abcd"

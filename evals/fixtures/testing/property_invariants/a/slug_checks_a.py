"""Single happy-path example check lacking idempotency or boundary corpus."""

from collections.abc import Callable


def check_single_example(normalize_fn: Callable[[str], str]) -> None:
    assert normalize_fn("Hello World") == "hello-world"

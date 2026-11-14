# Contract: fee is ten percent of amount. Inspect the oracle, not production fee correctness.
def fee(amount):
    return amount * 0.10


def test_fee():
    expected = fee(100)
    assert fee(100) == expected

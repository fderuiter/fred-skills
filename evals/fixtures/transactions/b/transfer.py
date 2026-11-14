# Contract: debit and credit preserve total balance even if the second step fails.
def transfer(db, amount, fail=False):
    with db:
        db.execute("UPDATE accounts SET balance=balance-? WHERE id=1", (amount,))
        if fail:
            raise RuntimeError("interrupted")
        db.execute("UPDATE accounts SET balance=balance+? WHERE id=2", (amount,))

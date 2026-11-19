# Monolithic pipeline mixing CSV parsing, tax rules, and SMTP network I/O.
def run_billing_monolith(csv_text: str, smtp_client) -> list[float]:
    totals: list[float] = []
    for line in csv_text.strip().splitlines():
        amount = float(line.split(",")[1]) * 1.08
        smtp_client.send(f"Charged {amount:.2f}")
        totals.append(amount)
    return totals

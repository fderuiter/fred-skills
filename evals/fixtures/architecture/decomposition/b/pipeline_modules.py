# Decomposed acyclic extractor, tax calculator, and notification publisher.
def parse_amounts(csv_text: str) -> list[float]:
    return [float(row.split(",")[1]) for row in csv_text.strip().splitlines()]


def apply_tax(amounts: list[float]) -> list[float]:
    return [round(value * 1.08, 2) for value in amounts]

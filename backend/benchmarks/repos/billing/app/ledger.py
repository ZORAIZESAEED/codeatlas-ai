LEDGER = []


def record_charge(customer_id, amount_cents):
    """Append a charge to the in-memory ledger and return the entry."""
    entry = {"customer": customer_id, "amount": amount_cents}
    LEDGER.append(entry)
    return entry

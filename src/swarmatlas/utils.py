from __future__ import annotations


def format_bytes(value: int | float) -> str:
    amount = max(0.0, float(value))
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    index = 0
    while amount >= 1000.0 and index < len(units) - 1:
        amount /= 1000.0
        index += 1
    decimals = 0 if index == 0 else 2 if amount < 10 else 1
    return f"{amount:.{decimals}f} {units[index]}"


def format_rate(value: int | float) -> str:
    return f"{format_bytes(value)}/s"


def counter_delta(current: int, previous: int) -> int:
    """Return a safe counter delta, tolerating interface counter resets."""
    current = max(0, int(current))
    previous = max(0, int(previous))
    return current - previous if current >= previous else current

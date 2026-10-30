"""Tiny statistics helpers (example target with a known bug)."""


def mean(values):
    return sum(values) / len(values)


def median(values):
    s = sorted(values)
    n = len(s)
    return s[n // 2]

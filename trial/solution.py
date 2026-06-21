from __future__ import annotations

from collections.abc import Iterable


def sum_even_squares(numbers: Iterable[int]) -> int:
    """정수들 중 짝수만 제곱해 합을 반환한다."""
    return sum(n * n for n in numbers if n % 2 == 0)

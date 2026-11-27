"""Деньги — целые копейки. Количество — Decimal с 3 знаками."""

from decimal import ROUND_HALF_UP, Decimal


def line_total_kop(qty: Decimal, unit_price_kop: int) -> int:
    """Стоимость строки сметы с округлением до копейки по правилам бухгалтерии (половина вверх)."""
    return int((Decimal(qty) * unit_price_kop).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def rub_to_kop(value: object) -> int:
    """Принимает рубли как число или строку («1 234,50») и возвращает копейки."""
    if isinstance(value, int | float | Decimal):
        rub = Decimal(str(value))
    else:
        text = str(value).replace(" ", "").replace(" ", "").replace("₽", "").replace(",", ".")
        rub = Decimal(text)
    if rub < 0:
        raise ValueError("Цена не может быть отрицательной")
    return int((rub * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))

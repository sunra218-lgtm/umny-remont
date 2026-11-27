"""Проверки российских реквизитов и телефона."""

import re


def normalize_phone(raw: str) -> str:
    """Приводит российский мобильный номер к виду +7XXXXXXXXXX."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10 and digits[0] == "9":
        digits = "7" + digits
    if not re.fullmatch(r"79\d{9}", digits):
        raise ValueError("Укажите мобильный номер в формате +7 9XX XXX-XX-XX")
    return "+" + digits


def _inn_checksum(digits: str, weights: list[int]) -> int:
    return sum(int(d) * w for d, w in zip(digits, weights, strict=False)) % 11 % 10


def is_valid_inn(inn: str) -> bool:
    if not re.fullmatch(r"\d{10}|\d{12}", inn or ""):
        return False
    if len(inn) == 10:
        return _inn_checksum(inn, [2, 4, 10, 3, 5, 9, 4, 6, 8]) == int(inn[9])
    n11 = _inn_checksum(inn, [7, 2, 4, 10, 3, 5, 9, 4, 6, 8])
    n12 = _inn_checksum(inn, [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8])
    return n11 == int(inn[10]) and n12 == int(inn[11])


def is_valid_ogrn(ogrn: str) -> bool:
    if re.fullmatch(r"\d{13}", ogrn or ""):
        return int(ogrn[:12]) % 11 % 10 == int(ogrn[12])
    if re.fullmatch(r"\d{15}", ogrn or ""):
        return int(ogrn[:14]) % 13 % 10 == int(ogrn[14])
    return False


def is_valid_kpp(kpp: str) -> bool:
    return bool(re.fullmatch(r"\d{4}[\dA-Z]{2}\d{3}", kpp or ""))


def is_valid_bik(bik: str) -> bool:
    return bool(re.fullmatch(r"\d{9}", bik or ""))


def is_valid_account(account: str, bik: str) -> bool:
    """Контрольный ключ расчётного счёта по БИК (Положение ЦБ № 579-П)."""
    if not re.fullmatch(r"\d{20}", account or "") or not is_valid_bik(bik):
        return False
    payload = bik[-3:] + account
    weights = [7, 1, 3] * 8
    return sum(int(d) * w % 10 for d, w in zip(payload, weights, strict=False)) % 10 == 0

"""Проверка подписи устройства Lite Web.

Ключевая пара ECDSA P-256 создаётся в браузере через WebCrypto как неэкспортируемая: закрытый
ключ нельзя скопировать с телефона. Сервер хранит открытый ключ (SPKI, base64).
WebCrypto отдаёт подпись в формате r||s (64 байта), а cryptography ждёт DER — переводим.
"""

import base64
import binascii
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.serialization import load_der_public_key


class BadPublicKey(ValueError):
    pass


def load_public_key(spki_b64: str) -> ec.EllipticCurvePublicKey:
    try:
        key = load_der_public_key(base64.b64decode(spki_b64, validate=True))
    except (ValueError, binascii.Error, TypeError):
        raise BadPublicKey("Некорректный ключ устройства") from None
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise BadPublicKey("Нужен ключ ECDSA P-256")
    return key


def key_fingerprint(spki_b64: str) -> str:
    return hashlib.sha256(base64.b64decode(spki_b64)).hexdigest()


def verify(spki_b64: str, message: bytes, signature_b64: str) -> bool:
    try:
        key = load_public_key(spki_b64)
        raw = base64.b64decode(signature_b64, validate=True)
    except (BadPublicKey, ValueError, binascii.Error):
        return False
    if len(raw) != 64:
        return False
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    try:
        key.verify(der, message, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return False
    return True

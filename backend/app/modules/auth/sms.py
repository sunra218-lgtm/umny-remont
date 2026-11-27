"""Отправка СМС. В MVP подключится SMS.ru или МТС Exolve; пока — вывод в лог."""

import logging
from typing import Protocol

from app.core.config import get_settings

log = logging.getLogger("umny.sms")


class SmsSender(Protocol):
    async def send(self, phone: str, text: str) -> None: ...


class ConsoleSmsSender:
    """Для локальной разработки: сообщение пишется в лог backend."""

    async def send(self, phone: str, text: str) -> None:
        log.warning("СМС на %s: %s", phone, text)


def get_sms_sender() -> SmsSender:
    provider = get_settings().sms_provider
    if provider == "console":
        return ConsoleSmsSender()
    raise RuntimeError(f"Неизвестный SMS-провайдер: {provider}")

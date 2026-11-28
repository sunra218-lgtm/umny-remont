"""Внешние сервисы умного дизайна. Каждый включается настройкой и по умолчанию выключен.

* Чтение плана БТИ: ``DESIGN_PLAN_READER=yandex_ocr`` — Yandex Vision OCR читает экспликацию
  помещений; по умолчанию (``manual``) комнаты заготавливаются по площади квартиры.
* Картинки комнат: ``DESIGN_RENDERER=yandexart`` — YandexART рисует комнату по описанию
  выбранной отделки; по умолчанию картинку рисует сам сайт (схема комнаты в цветах отделки).
* Ролики: ``CONTENT_FACTORY_URL`` — внешний «контент-завод» (отдельный проект). Пока адрес
  не задан, кнопки ролика нет. Формат обмена описан в docs/design.md.

Наружу уходят только план (для OCR) и описание отделки — без имён, телефонов и адреса.
"""

import base64
import logging
from typing import Any

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)

OCR_URL = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText"
ART_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/imageGenerationAsync"
OPERATIONS_URL = "https://llm.api.cloud.yandex.net:443/operations/"


class IntegrationError(Exception):
    pass


def _yandex_headers() -> dict[str, str]:
    s = get_settings()
    return {"Authorization": f"Api-Key {s.yandex_api_key}", "x-folder-id": s.yandex_folder_id or ""}


def plan_reader_enabled() -> bool:
    s = get_settings()
    return s.design_plan_reader == "yandex_ocr" and bool(s.yandex_api_key and s.yandex_folder_id)


def renderer_enabled() -> bool:
    s = get_settings()
    return s.design_renderer == "yandexart" and bool(s.yandex_api_key and s.yandex_folder_id)


def video_enabled() -> bool:
    return bool(get_settings().content_factory_url)


async def read_plan(
    data: bytes, content_type: str, transport: httpx.AsyncBaseTransport | None = None
) -> str:
    """Текст с плана БТИ через Yandex Vision OCR."""
    mime = {"image/jpeg": "JPEG", "image/png": "PNG", "application/pdf": "PDF"}.get(
        content_type, "JPEG"
    )
    body = {
        "mimeType": mime,
        "languageCodes": ["ru"],
        "model": "page",
        "content": base64.b64encode(data).decode(),
    }
    async with httpx.AsyncClient(timeout=30, transport=transport) as client:
        resp = await client.post(OCR_URL, json=body, headers=_yandex_headers())
        resp.raise_for_status()
        return str(resp.json()["result"]["textAnnotation"]["fullText"])


STYLE_WORDS = {
    "scandi": "скандинавский стиль",
    "minimal": "современный минимализм",
    "loft": "стиль лофт",
    "neoclassic": "неоклассика",
    "japandi": "стиль японди",
}


def render_prompt(style: str, brief: dict[str, Any]) -> str:
    parts = [f"{f['title'].lower()}" for f in brief["finishes"].values()]
    text = (
        f"Фотореалистичный интерьер, {brief['room'].lower()} {brief['area_m2']:g} м², "
        f"{STYLE_WORDS.get(style, style)}. Отделка: {', '.join(parts)}. "
        "Дневной свет, широкий угол, без людей и надписей."
    )
    return text[:500]


async def start_render(
    prompt: str, seed: int, transport: httpx.AsyncBaseTransport | None = None
) -> str:
    s = get_settings()
    body = {
        "modelUri": f"art://{s.yandex_folder_id}/yandex-art/latest",
        "generationOptions": {
            "seed": str(seed),
            "aspectRatio": {"widthRatio": "16", "heightRatio": "9"},
        },
        "messages": [{"weight": "1", "text": prompt}],
    }
    async with httpx.AsyncClient(timeout=20, transport=transport) as client:
        resp = await client.post(ART_URL, json=body, headers=_yandex_headers())
        resp.raise_for_status()
        return str(resp.json()["id"])


async def poll_render(
    operation_id: str, transport: httpx.AsyncBaseTransport | None = None
) -> bytes | None:
    """Готовая картинка (JPEG) или None, если ещё рисуется."""
    async with httpx.AsyncClient(timeout=20, transport=transport) as client:
        resp = await client.get(OPERATIONS_URL + operation_id, headers=_yandex_headers())
        resp.raise_for_status()
        data = resp.json()
    if not data.get("done"):
        return None
    if "error" in data:
        raise IntegrationError(str(data["error"].get("message", "ошибка нейросети"))[:300])
    return base64.b64decode(data["response"]["image"])


def _factory_headers() -> dict[str, str]:
    key = get_settings().content_factory_key
    return {"Authorization": f"Bearer {key}"} if key else {}


async def order_video(
    brief: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None
) -> str:
    """Заказ ролика у контент-завода. Возвращает номер задания."""
    url = get_settings().content_factory_url
    if not url:
        raise IntegrationError("Контент-завод не подключён")
    async with httpx.AsyncClient(timeout=20, transport=transport) as client:
        resp = await client.post(f"{url.rstrip('/')}/jobs", json=brief, headers=_factory_headers())
        resp.raise_for_status()
        return str(resp.json()["id"])


async def video_status(
    job_id: str, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[str, str | None]:
    url = get_settings().content_factory_url
    if not url:
        raise IntegrationError("Контент-завод не подключён")
    async with httpx.AsyncClient(timeout=20, transport=transport) as client:
        resp = await client.get(f"{url.rstrip('/')}/jobs/{job_id}", headers=_factory_headers())
        resp.raise_for_status()
        data = resp.json()
    status = str(data.get("status", "processing"))
    video = data.get("video_url")
    if video and not str(video).startswith("https://"):
        video = None
    return status, video

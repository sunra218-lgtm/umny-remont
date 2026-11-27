"""Помощник мастера: отвечает по базе техкарт.

Сначала находим шаги техкарт, подходящие к вопросу (простой поиск по словам с усечением
окончаний — для русского этого хватает на коротких текстах). Дальше два режима:

* ``search`` (по умолчанию) — ответ собирается из найденных шагов как есть: что делать,
  допуск, частые ошибки. Работает без внешних сервисов.
* ``yandexgpt`` — YandexGPT пишет ответ своими словами, но только по найденным шагам.
  Если сервис не ответил, возвращаемся к режиму ``search``.

В нейросеть уходит только текст вопроса и шаги техкарт — без имён, телефонов и адресов.
Если в техкартах ничего не нашлось, помощник честно говорит об этом и предлагает
спросить инженера.
"""

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel

from app.core.config import get_settings
from app.domain.stage_types import STAGE_TYPES

log = logging.getLogger(__name__)

YANDEX_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
STOP = {
    "как",
    "что",
    "это",
    "для",
    "при",
    "или",
    "под",
    "над",
    "без",
    "все",
    "его",
    "она",
    "они",
    "надо",
    "нужно",
    "можно",
    "ли",
    "на",
    "по",
    "в",
    "и",
    "с",
    "из",
    "не",
    "до",
    "от",
    "за",
    "то",
    "же",
    "а",
    "но",
    "у",
    "о",
    "об",
    "мне",
    "мы",
    "вы",
    "сколько",
    "какой",
    "какая",
    "какие",
    "каким",
    "чем",
    "где",
    "когда",
    "почему",
    "делать",
    "сделать",
}
WORD = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)
MIN_SCORE = 1.0


def stems(text: str) -> list[str]:
    out = []
    for w in WORD.findall(text.lower().replace("ё", "е")):
        if w in STOP or len(w) < 2:
            continue
        out.append(w[:5] if len(w) > 5 else w)
    return out


class Source(BaseModel):
    stage_type: str
    card_title: str
    step_key: str
    step_title: str


class Answer(BaseModel):
    answer: str
    sources: list[Source]
    found: bool
    by: str  # search | yandexgpt


@dataclass
class Doc:
    card: dict[str, Any]
    step: dict[str, Any]
    terms: Counter[str]


def _docs(cards: list[dict[str, Any]]) -> list[Doc]:
    docs = []
    for card in cards:
        for step in card["steps"]:
            title_terms = stems(f"{card['title']} {step['title']}")
            body = " ".join(
                [step["how"], step.get("tolerance") or "", step.get("check") or ""]
                + list(step.get("mistakes") or [])
            )
            # Совпадение в названии шага весит вдвое больше.
            docs.append(Doc(card, step, Counter(title_terms * 2 + stems(body))))
    return docs


def search(
    cards: list[dict[str, Any]], question: str, stage_type: str | None = None, limit: int = 3
) -> list[Doc]:
    docs = _docs(cards)
    query = set(stems(question))
    if not query or not docs:
        return []
    df = Counter(t for d in docs for t in set(d.terms))
    n = len(docs)
    scored = []
    for d in docs:
        score = sum(
            (1 + math.log(d.terms[t])) * math.log(1 + n / df[t]) for t in query if t in d.terms
        )
        if stage_type and d.card["stage_type"] == stage_type:
            score *= 1.5
        if score >= MIN_SCORE:
            scored.append((score, d))
    scored.sort(key=lambda x: -x[0])
    return [d for _, d in scored[:limit]]


def _step_text(d: Doc) -> str:
    s = d.step
    parts = [f"{d.card['title']} — {s['title']}. {s['how']}"]
    if s.get("tolerance"):
        parts.append(f"Допуск: {s['tolerance']}")
    if s.get("check"):
        parts.append(f"Как проверить: {s['check']}")
    if s.get("mistakes"):
        parts.append("Частые ошибки: " + "; ".join(s["mistakes"]))
    return "\n".join(parts)


def _sources(found: list[Doc]) -> list[Source]:
    return [
        Source(
            stage_type=d.card["stage_type"],
            card_title=d.card["title"],
            step_key=d.step["key"],
            step_title=d.step["title"],
        )
        for d in found
    ]


NOT_FOUND = (
    "В техкартах ответа на этот вопрос нет. Нажмите «Спросить инженера» и приложите фото — "
    "инженер технадзора ответит."
)


async def _yandex(
    question: str, found: list[Doc], transport: httpx.AsyncBaseTransport | None
) -> str:
    s = get_settings()
    context = "\n\n".join(_step_text(d) for d in found)
    body = {
        "modelUri": f"gpt://{s.yandex_folder_id}/{s.yandex_gpt_model}",
        "completionOptions": {"stream": False, "temperature": 0.2, "maxTokens": "600"},
        "messages": [
            {
                "role": "system",
                "text": (
                    "Ты помощник мастера-отделочника. Отвечай коротко и по делу, простым языком, "
                    "только по выдержкам из технологических карт ниже. Обязательно назови "
                    "допуск, если он есть. Если в выдержках ответа нет — так и скажи и "
                    "посоветуй спросить инженера технадзора. Не придумывай нормы.\n\n"
                    f"Выдержки из техкарт:\n{context}"
                ),
            },
            {"role": "user", "text": question},
        ],
    }
    async with httpx.AsyncClient(timeout=20, transport=transport) as client:
        resp = await client.post(
            YANDEX_URL,
            json=body,
            headers={
                "Authorization": f"Api-Key {s.yandex_api_key}",
                "x-folder-id": s.yandex_folder_id or "",
            },
        )
        resp.raise_for_status()
        return str(resp.json()["result"]["alternatives"][0]["message"]["text"]).strip()


async def ask(
    cards: list[dict[str, Any]],
    question: str,
    stage_type: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Answer:
    found = search(cards, question, stage_type)
    if not found:
        return Answer(answer=NOT_FOUND, sources=[], found=False, by="search")
    s = get_settings()
    if s.assistant_backend == "yandexgpt" and s.yandex_api_key and s.yandex_folder_id:
        try:
            text = await _yandex(question, found, transport)
            return Answer(answer=text, sources=_sources(found), found=True, by="yandexgpt")
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("YandexGPT не ответил, отвечаем из техкарт: %s", exc)
    best = found[0]
    text = _step_text(best)
    if len(found) > 1:
        text += "\n\nСм. также: " + "; ".join(
            f"{d.card['title']} — {d.step['title']}" for d in found[1:]
        )
    return Answer(answer=text, sources=_sources(found), found=True, by="search")


def stage_type_title(code: str) -> str:
    return STAGE_TYPES.get(code, code)

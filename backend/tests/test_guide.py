"""Подсказки мастерам: калькулятор, фото-образцы, «спросить инженера», помощник."""

import json
import uuid

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from app.core.config import get_settings
from app.domain.tech_cards import TECH_CARDS
from app.models import Notification
from app.modules.guide import assistant
from app.modules.guide.calc import calculate
from tests.conftest import idem
from tests.scenario import build_world, post
from tests.test_field import media_dir, photo  # noqa: F401
from tests.test_techcards_daily import _seed


def test_calculate() -> None:
    walls = next(c for c in TECH_CARDS if c["stage_type"] == "walls")
    rows = {r.key: r for r in calculate(walls["materials"], {"м²": 20}, {"plaster": 20})}
    # 0,9 кг × 20 м² × 20 мм × 1,1 запаса = 396 кг → 14 мешков по 30 кг.
    assert rows["plaster"].quantity == 396 and rows["plaster"].packs == 14
    assert rows["plaster"].thickness_mm == 20
    assert rows["primer"].quantity == 3.3 and rows["primer"].packs == 1
    assert "pgp" in rows  # тоже на м²
    assert calculate(walls["materials"], {"м.п.": 5}) == []


def test_search_finds_tolerance() -> None:
    found = assistant.search(TECH_CARDS, "какой допуск по сопротивлению изоляции?")
    assert found and found[0].step["key"] == "insulation"
    found = assistant.search(TECH_CARDS, "уклон канализационной трубы", "plumbing")
    assert found[0].step["key"] == "sewer"
    assert assistant.search(TECH_CARDS, "как испечь пирог") == []


async def test_calc_and_assistant_api(
    client: AsyncClient, admin: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed()
    w = await build_world(client, admin)
    r = await post(
        client,
        "/api/v1/tech-cards/tiling/calc",
        w.boss,
        {"area_m2": 10, "thickness": {"tileadh": 6}},
    )
    rows = {x["key"]: x for x in r.json()}
    assert rows["tileadh"]["packs"] == 4  # 1,4 × 10 × 6 × 1,1 = 92,4 кг → 4 мешка по 25 кг
    bad = await post(
        client,
        "/api/v1/tech-cards/tiling/calc",
        w.boss,
        {"area_m2": 10, "thickness": {"tileadh": 0}},
    )
    assert bad.status_code == 422
    assert (
        await post(client, "/api/v1/tech-cards/magic/calc", w.boss, {"area_m2": 1})
    ).status_code == 404

    ans = (
        await post(
            client,
            "/api/v1/assistant/ask",
            w.boss,
            {"question": "Какой уклон у канализации 50 мм?"},
        )
    ).json()
    assert ans["found"] and ans["by"] == "search" and "Допуск" in ans["answer"]
    assert ans["sources"][0]["step_key"] == "sewer"
    none = (
        await post(client, "/api/v1/assistant/ask", w.boss, {"question": "Где купить пиццу"})
    ).json()
    assert not none["found"] and "Спросить инженера" in none["answer"]

    # Режим YandexGPT: в нейросеть уходят вопрос и выдержки техкарт; при сбое — ответ из техкарт.
    s = get_settings()
    monkeypatch.setattr(s, "assistant_backend", "yandexgpt")
    monkeypatch.setattr(s, "yandex_api_key", "key")
    monkeypatch.setattr(s, "yandex_folder_id", "folder")
    sent: list[dict] = []  # type: ignore[type-arg]

    def ok(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        assert request.headers["authorization"] == "Api-Key key"
        return httpx.Response(
            200, json={"result": {"alternatives": [{"message": {"text": "Уклон 3 см на метр."}}]}}
        )

    cards = [
        {"stage_type": c["stage_type"], "title": c["title"], "steps": c["steps"]}
        for c in TECH_CARDS
    ]
    out = await assistant.ask(cards, "уклон канализации", transport=httpx.MockTransport(ok))
    assert out.by == "yandexgpt" and out.answer == "Уклон 3 см на метр."
    assert sent[0]["modelUri"] == "gpt://folder/yandexgpt-lite/latest"
    assert "Канализация" in sent[0]["messages"][0]["text"]
    down = await assistant.ask(
        cards, "уклон канализации", transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    assert down.by == "search" and down.found


async def test_example_photo(client: AsyncClient, admin: dict[str, str], media_dir) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    await _seed()
    w = await build_world(client, admin)
    url = "/api/v1/tech-cards/tiling/steps/plane/example"
    files = {"photo": ("ok.jpg", photo(5), "image/jpeg")}
    assert (await client.put(url, files=files, headers={**w.boss, **idem()})).status_code == 403
    bad = await client.put(
        url, files={"photo": ("x.jpg", b"nope", "image/jpeg")}, headers={**admin, **idem()}
    )
    assert bad.status_code == 422
    r = await client.put(url, files=files, headers={**admin, **idem()})
    assert r.status_code == 200, r.text
    step = next(s for s in r.json()["steps"] if s["key"] == "plane")
    assert step["example_key"].startswith("examples/tiling/plane-")
    img = await client.get(url)  # без входа: в образце нет личных данных
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert (await client.get("/api/v1/tech-cards/tiling/steps/grout/example")).status_code == 404
    # Правка карты не принимает чужую ссылку на файл и сохраняет загруженный образец.
    card = (await client.get("/api/v1/tech-cards/tiling", headers=admin)).json()
    body = {k: card[k] for k in ("title", "summary", "norms", "steps", "materials")}
    body["steps"][0]["example_key"] = "questions/secret.jpg"
    saved = (
        await client.put("/api/v1/tech-cards/tiling", json=body, headers={**admin, **idem()})
    ).json()
    keys = {s["key"]: s["example_key"] for s in saved["steps"]}
    assert keys["layout"] is None and keys["plane"] == step["example_key"]


async def test_ask_engineer(client: AsyncClient, admin: dict[str, str], engine, media_dir) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    w = await build_world(client, admin)
    sid = (
        await post(
            client,
            f"/api/v1/objects/{w.object_id}/stages",
            w.estimator,
            {"title": "Плитка", "stage_type": "tiling"},
        )
    ).json()["id"]
    url = f"/api/v1/stages/{sid}/questions"
    data = {"text": "Такой уступ между плитками допустим?", "step_key": "plane"}
    files = {"photo": ("q.jpg", photo(7), "image/jpeg")}
    assert (
        await client.post(url, data=data, files=files, headers={**w.customer, **idem()})
    ).status_code == 403
    r = await client.post(url, data=data, files=files, headers={**w.boss, **idem()})
    assert r.status_code == 201, r.text
    q = r.json()
    assert q["status"] == "open" and q["photo_url"] and not q["can_answer"]
    pic = await client.get(q["photo_url"])
    assert pic.status_code == 200
    plain = await client.post(url, data={"text": "Без фото можно?"}, headers={**w.boss, **idem()})
    assert plain.json()["photo_url"] is None
    async with engine.begin() as conn:
        to = (
            (
                await conn.execute(
                    select(Notification.user_id).where(Notification.template == "question_asked")
                )
            )
            .scalars()
            .all()
        )
    assert {str(u) for u in to} == {w.user_ids["inspector"]}  # инженер платформы

    # Инженер видит вопросы по всем объектам и отвечает; бригада видит ответ.
    mine = (await client.get("/api/v1/questions", headers=w.inspector)).json()
    assert len(mine) == 2 and mine[0]["can_answer"]
    assert (await client.get("/api/v1/questions", headers=w.boss)).json() == []
    answer = f"/api/v1/questions/{q['id']}/answer"
    assert (await post(client, answer, w.boss, {"answer": "Да"})).status_code == 403
    done = (
        await post(client, answer, w.inspector, {"answer": "Нет, не больше 1 мм. Переложите."})
    ).json()
    assert done["status"] == "answered" and not done["can_answer"]
    assert (await post(client, answer, w.inspector, {"answer": "Ещё"})).status_code == 409
    listed = (await client.get(f"/api/v1/objects/{w.object_id}/questions", headers=w.boss)).json()
    assert {x["status"] for x in listed} == {"open", "answered"}
    assert (await client.get("/api/v1/questions", headers=w.inspector)).json()[0]["id"] != q["id"]
    async with engine.begin() as conn:
        n = (
            await conn.execute(
                text("SELECT count(*) FROM notifications WHERE template = 'question_answered'")
            )
        ).scalar()
    assert n == 1
    assert (await post(client, f"/api/v1/stages/{uuid.uuid4()}/questions", w.boss)).status_code in (
        404,
        422,
    )

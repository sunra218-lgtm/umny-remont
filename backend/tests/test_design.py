"""Умный дизайн-проект: стиль, план БТИ, подбор отделки, правки, оценка, картинки, ролик."""

import json

import httpx
import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.core.db import get_session
from app.modules.design import integrations as ext
from app.modules.design import jobs, service
from app.modules.design.service import Room
from tests.conftest import idem
from tests.scenario import build_world, post
from tests.test_field import media_dir, photo  # noqa: F401

EXPLICATION = """Экспликация помещений
1  Коридор         6,4
2  Жилая комната  18,2
3  Жилая комната  12,5
4  Кухня           9,8
5  Санузел         4,1
6  Лоджия          3,2
Итого: 50,9 Жилая площадь 30,7
"""


async def _seed() -> int:
    async for db in get_session():
        return await service.seed_materials(db)
    return 0


async def _put(client: AsyncClient, url: str, h: dict[str, str], body):  # type: ignore[no-untyped-def]
    return await client.put(url, json=body, headers={**h, **idem()})


def test_parse_explication() -> None:
    rooms = service.parse_explication(EXPLICATION)
    assert [(r.type, r.area) for r in rooms] == [
        ("hallway", 6.4),
        ("living", 18.2),
        ("bedroom", 12.5),
        ("kitchen", 9.8),
        ("bathroom", 4.1),
        ("balcony", 3.2),
    ]
    assert len({r.key for r in rooms}) == len(rooms)
    assert rooms[1].name == "Гостиная" and rooms[2].name == "Спальня"


def test_suggest_rooms_and_geometry() -> None:
    rooms = service.suggest_rooms(60.0, 2)
    assert {r.type for r in rooms} == {"living", "bedroom", "kitchen", "bathroom", "hallway"}
    assert abs(sum(r.area for r in rooms) - 60.0) < 1.0
    room = Room(key="x", name="Комната", type="living", area=13.0, perimeter=14.6)
    # 14,6 × 2,7 − дверь 1,7 − окно 2,0
    assert room.wall_area() == pytest.approx(35.72, abs=0.01)


async def test_design_flow(  # type: ignore[no-untyped-def]
    client: AsyncClient,
    admin: dict[str, str],
    engine,
    media_dir,  # noqa: F811
) -> None:
    assert await _seed() > 30
    w = await build_world(client, admin)
    base = f"/api/v1/objects/{w.object_id}/design"

    info = (await client.get("/api/v1/design/info", headers=w.customer)).json()
    assert len(info["styles"]) == 5 and not info["renderer"] and not info["video"]
    assert (await client.get(base, headers=w.customer)).json() is None

    # Проект ведёт заказчик, бригада только смотрит.
    r = await _put(client, base, w.foreman, {"style": "scandi"})
    assert r.status_code == 403
    r = await _put(client, base, w.customer, {"style": "scandi", "rooms_count": 2})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["can_edit"] and len(p["rooms"]) == 5
    # Во всех комнатах подобрана отделка из каталога, в стиле.
    by_id = {m["id"]: m for m in p["materials"]}
    for room in p["rooms"]:
        sel = p["selections"][room["key"]]
        assert "floor" in sel and "walls" in sel
        assert "scandi" in by_id[sel["floor"]]["styles"]
    seen = (await client.get(base, headers=w.foreman)).json()
    assert seen["id"] == p["id"] and not seen["can_edit"]
    assert (await client.get(base, headers=w.stranger)).status_code == 404

    est = (await client.get(f"{base}/estimate", headers=w.customer)).json()
    assert est["materials_kop"] > 0 and est["works_kop"] > 0
    assert est["low_kop"] < est["total_kop"] < est["high_kop"]
    assert est["total_kop"] == est["materials_kop"] + est["works_kop"]
    # Материалы считаются целыми упаковками.
    for line in est["lines"]:
        if line["kind"] == "material" and line.get("packs"):
            assert line["packs"] == int(line["packs"])

    # Заказчик меняет пол в гостиной на дорогой паркет.
    mats = (await client.get("/api/v1/design/materials?kind=parquet", headers=w.customer)).json()
    parquet = max(mats, key=lambda m: m["price_kop"])
    r = await _put(client, f"{base}/rooms/r1/floor", w.customer, {"material_id": parquet["id"]})
    assert r.status_code == 200, r.text
    p2 = r.json()
    assert p2["selections"]["r1"]["floor"] == parquet["id"] and p2["pinned"]["r1"] == ["floor"]
    assert p2["version"] == p["version"] + 1
    est2 = (await client.get(f"{base}/estimate", headers=w.customer)).json()
    assert est2["materials_kop"] > est["materials_kop"]

    # Плитка сантехники не подходит на пол гостиной.
    sanitary = (
        await client.get("/api/v1/design/materials?kind=sanitary", headers=w.customer)
    ).json()[0]
    r = await _put(client, f"{base}/rooms/r1/floor", w.customer, {"material_id": sanitary["id"]})
    assert r.status_code == 422

    # Смена стиля не трогает то, что заказчик выбрал сам.
    p3 = (await _put(client, base, w.customer, {"style": "loft"})).json()
    assert p3["style"] == "loft" and p3["selections"]["r1"]["floor"] == parquet["id"]
    loft_ids = {m["id"] for m in p3["materials"] if "loft" in m["styles"]}
    assert p3["selections"]["kitchen"]["floor"] in loft_ids

    # Вернуть подбор по стилю.
    p4 = (await _put(client, f"{base}/rooms/r1/floor", w.customer, {"material_id": None})).json()
    assert p4["selections"]["r1"]["floor"] != parquet["id"] and p4["pinned"]["r1"] == []

    # Комнаты правятся вручную.
    rooms = p4["rooms"][:3] + [
        {"key": "bath", "name": "Ванная", "type": "bathroom", "area": 5.0, "windows": 0}
    ]
    p5 = (await _put(client, f"{base}/rooms", w.customer, {"rooms": rooms})).json()
    assert [r["key"] for r in p5["rooms"]] == ["r1", "r2", "kitchen", "bath"]
    assert set(p5["selections"]) == {"r1", "r2", "kitchen", "bath"}
    dup = (
        await _put(client, f"{base}/rooms", w.customer, {"rooms": rooms + rooms[:1]})
    ).status_code
    assert dup == 422

    # План БТИ без распознавания: файл хранится, комнаты остаются.
    r = await client.post(
        f"{base}/plan",
        files={"plan": ("plan.jpg", photo(3), "image/jpeg")},
        headers={**w.customer, **idem()},
    )
    assert r.status_code == 200, r.text
    assert r.json()["plan_url"] and len(r.json()["rooms"]) == 4
    assert (await client.get(r.json()["plan_url"])).status_code == 200
    bad = await client.post(
        f"{base}/plan",
        files={"plan": ("x.txt", b"hello", "text/plain")},
        headers={**w.customer, **idem()},
    )
    assert bad.status_code == 422

    # Картинки и ролик выключены, пока не подключены.
    assert (await post(client, f"{base}/renders", w.customer)).status_code == 409
    assert (await post(client, f"{base}/video", w.customer)).status_code == 409


async def test_catalog_editing(client: AsyncClient, admin: dict[str, str]) -> None:
    w = await build_world(client, admin)
    body = {
        "sku": "LAM-TEST",
        "kind": "laminate",
        "title": "Ламинат тестовый",
        "styles": ["scandi"],
        "color": "#AABBCC",
        "pattern": "plank",
        "unit": "м²",
        "price_kop": 99900,
        "rate": 1.0,
        "pack_size": 2.0,
    }
    assert (await post(client, "/api/v1/design/materials", w.customer, body)).status_code == 403
    r = await post(client, "/api/v1/design/materials", admin, body)
    assert r.status_code == 201, r.text
    assert (await post(client, "/api/v1/design/materials", admin, body)).status_code == 409
    bad = {**body, "sku": "X-1", "styles": ["barocco"]}
    assert (await post(client, "/api/v1/design/materials", admin, bad)).status_code == 422
    off = {**body, "is_active": False}
    r = await _put(client, f"/api/v1/design/materials/{r.json()['id']}", admin, off)
    assert r.status_code == 200 and not r.json()["is_active"]
    assert (await client.get("/api/v1/design/materials", headers=w.customer)).json() == []
    everything = await client.get("/api/v1/design/materials?all=true", headers=admin)
    assert len(everything.json()) == 1


async def test_plan_ocr_renders_and_video(  # type: ignore[no-untyped-def]
    client: AsyncClient,
    admin: dict[str, str],
    engine,
    media_dir,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "yandex_api_key", "test-key")
    monkeypatch.setattr(s, "yandex_folder_id", "b1g")
    monkeypatch.setattr(s, "design_plan_reader", "yandex_ocr")
    monkeypatch.setattr(s, "design_renderer", "yandexart")
    monkeypatch.setattr(s, "content_factory_url", "https://factory.example")

    sent: list[str] = []

    async def fake_read(data: bytes, content_type: str) -> str:
        return EXPLICATION

    async def fake_start(prompt: str, seed: int) -> str:
        sent.append(prompt)
        return f"op{len(sent)}"

    async def fake_poll(op: str) -> bytes | None:
        return photo(7)

    briefs: list[dict] = []  # type: ignore[type-arg]

    async def fake_order(brief: dict) -> str:  # type: ignore[type-arg]
        briefs.append(brief)
        return "job-1"

    async def fake_status(job_id: str) -> tuple[str, str | None]:
        return "done", "https://factory.example/v/1.mp4"

    monkeypatch.setattr(ext, "read_plan", fake_read)
    monkeypatch.setattr(ext, "start_render", fake_start)
    monkeypatch.setattr(ext, "poll_render", fake_poll)
    monkeypatch.setattr(ext, "order_video", fake_order)
    monkeypatch.setattr(ext, "video_status", fake_status)

    await _seed()
    w = await build_world(client, admin)
    base = f"/api/v1/objects/{w.object_id}/design"
    await _put(client, base, w.customer, {"style": "japandi"})
    r = await client.post(
        f"{base}/plan",
        files={"plan": ("plan.pdf", b"%PDF-1.4 test", "application/pdf")},
        headers={**w.customer, **idem()},
    )
    assert r.status_code == 200, r.text
    p = r.json()
    assert [x["type"] for x in p["rooms"]] == [
        "hallway",
        "living",
        "bedroom",
        "kitchen",
        "bathroom",
        "balcony",
    ]

    p = (await post(client, f"{base}/renders", w.customer)).json()
    assert len(p["renders"]) == 6 and {x["status"] for x in p["renders"]} == {"queued"}
    async for db in get_session():
        await jobs.process_renders(db)  # запуск
        assert await jobs.process_renders(db) == 6  # готово
    p = (await client.get(base, headers=w.customer)).json()
    assert all(x["status"] == "done" and x["url"] for x in p["renders"])
    assert (await client.get(p["renders"][0]["url"])).status_code == 200
    # В нейросеть уходит только описание отделки.
    assert all("японди" in x for x in sent)
    assert not any("+7916" in x for x in sent)

    p = (await post(client, f"{base}/video", w.customer)).json()
    assert p["video_status"] == "processing"
    brief = json.dumps(briefs[0], ensure_ascii=False)
    assert "japandi" in brief and "+7916" not in brief and "phone" not in brief
    async for db in get_session():
        assert await jobs.poll_videos(db) == 1
    p = (await client.get(base, headers=w.customer)).json()
    assert p["video_status"] == "done" and p["video_url"].endswith(".mp4")

    # Правка отделки делает картинки и ролик устаревшими.
    sel = p["selections"]["kitc1"]["walls"]
    alt = [
        m
        for m in (await client.get("/api/v1/design/materials", headers=w.customer)).json()
        if m["kind"] in ("paint", "wall_tile") and m["id"] != sel
    ][0]
    p = (
        await _put(client, f"{base}/rooms/kitc1/walls", w.customer, {"material_id": alt["id"]})
    ).json()
    assert p["renders"] == [] and p["video_status"] is None


async def test_http_clients() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"].startswith(("Api-Key", "Bearer"))
        if request.url.path.endswith("recognizeText"):
            body = json.loads(request.content)
            assert body["mimeType"] == "PDF"
            return httpx.Response(
                200, json={"result": {"textAnnotation": {"fullText": "Кухня 9,8"}}}
            )
        if request.url.path.endswith("imageGenerationAsync"):
            return httpx.Response(200, json={"id": "op-1"})
        if "/operations/" in request.url.path:
            return httpx.Response(200, json={"done": True, "response": {"image": "aGk="}})
        if request.url.path == "/jobs":
            return httpx.Response(200, json={"id": "j1"})
        return httpx.Response(200, json={"status": "done", "video_url": "http://insecure"})

    t = httpx.MockTransport(handler)
    s = get_settings()
    old = (s.yandex_api_key, s.yandex_folder_id, s.content_factory_url, s.content_factory_key)
    s.yandex_api_key, s.yandex_folder_id = "k", "f"
    s.content_factory_url, s.content_factory_key = "https://factory.example/", "fk"
    try:
        assert await ext.read_plan(b"%PDF", "application/pdf", transport=t) == "Кухня 9,8"
        assert await ext.start_render("x", 1, transport=t) == "op-1"
        assert await ext.poll_render("op-1", transport=t) == b"hi"
        assert await ext.order_video({}, transport=t) == "j1"
        # Ссылку на ролик принимаем только https.
        assert await ext.video_status("j1", transport=t) == ("done", None)
    finally:
        s.yandex_api_key, s.yandex_folder_id, s.content_factory_url, s.content_factory_key = old

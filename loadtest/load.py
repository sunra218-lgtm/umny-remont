"""Нагрузочный тест: N объектов, у каждого заказчик и прораб; C виртуальных пользователей
в течение D секунд: 70% чтение кабинета заказчика, 20% фотоотчёт прораба, 10% менеджер."""

import asyncio
import base64
import hashlib
import io
import os
import random
import statistics
import sys
import time
import uuid
from collections import defaultdict

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from PIL import Image

BASE = os.environ.get("BASE", "http://localhost:8000") + "/api/v1"
N = int(os.environ.get("OBJECTS", "10"))
C = int(os.environ.get("USERS", "50"))
D = int(os.environ.get("SECONDS", "60"))


def idem():
    return {"Idempotency-Key": str(uuid.uuid4())}


class Device:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        spki = self.key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        self.public_key = base64.b64encode(spki).decode()

    def sign(self, msg):
        r, s = decode_dss_signature(self.key.sign(msg.encode(), ec.ECDSA(hashes.SHA256())))
        return base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()


def photo(seed):
    rnd = random.Random(seed)
    img = Image.new("RGB", (64, 48))
    img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(3072)])
    img = img.resize((1280, 960), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()


async def login(c, phone):
    r = await c.post(f"{BASE}/auth/otp", json={"phone": phone})
    assert r.status_code == 202, r.text
    r = await c.post(
        f"{BASE}/auth/otp/verify",
        json={"phone": phone, "code": r.json()["debug_code"], "accept_pd_processing": True},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def post(c, path, h, body=None):
    r = await c.post(f"{BASE}{path}", json=body, headers={**h, **idem()})
    assert r.status_code < 300, (path, r.status_code, r.text)
    return r.json()


def inn(run):
    d = [int(x) for x in f"77{run:07d}"]
    k = [2, 4, 10, 3, 5, 9, 4, 6, 8]
    return "".join(map(str, d)) + str(sum(a * b for a, b in zip(k, d)) % 11 % 10)


async def setup(c):
    run = random.randrange(10**6)
    admin = await login(c, "+79990000001")
    org = await post(
        c,
        "/organizations",
        admin,
        {"type": "contractor", "inn": inn(run), "legal_name": f"ООО Нагрузка {run}"},
    )
    await post(c, f"/organizations/{org['id']}/verify", admin)
    boss_phone = f"+7915{run % 1000:03d}0000"
    await post(
        c, f"/organizations/{org['id']}/members", admin, {"phone": boss_phone, "role": "contractor_admin"}
    )
    me = (await c.get(f"{BASE}/me", headers=admin)).json()
    await post(
        c,
        f"/organizations/{me['memberships'][0]['org_id']}/members",
        admin,
        {"phone": f"+7914{run % 1000:03d}0000", "role": "procurement_manager"},
    )
    boss = await login(c, boss_phone)
    manager = await login(c, f"+7914{run % 1000:03d}0000")
    objects = []
    for i in range(N):
        cust = await login(c, f"+7913{run % 1000:03d}{i:04d}")
        obj = await post(
            c, "/objects", cust, {"address_text": f"Москва, ул. Нагрузочная, д. {i}", "area_m2": "50"}
        )
        r = await c.put(
            f"{BASE}/objects/{obj['id']}/contractor",
            json={"contractor_org_id": org["id"]},
            headers={**admin, **idem()},
        )
        assert r.status_code == 200, r.text
        stage = await post(
            c, f"/objects/{obj['id']}/stages", boss, {"title": "Стяжка", "stage_type": "screed"}
        )
        fm = await post(
            c,
            f"/organizations/{org['id']}/members",
            admin,
            {"phone": f"+7912{run % 1000:03d}{i:04d}", "role": "foreman"},
        )
        link = await post(c, "/auth/links", boss, {"user_id": fm["user_id"], "object_id": obj["id"]})
        dev = Device()
        r = await c.post(
            f"{BASE}/auth/links/{link['debug_url'].rsplit('/', 1)[-1]}/exchange",
            json={"public_key": dev.public_key},
        )
        assert r.status_code == 200, r.text
        lite = {"Authorization": f"Bearer {r.json()['access_token']}"}
        objects.append({"obj": obj["id"], "stage": stage["id"], "cust": cust, "lite": lite, "dev": dev})
    ids = ",".join(f"'{o['stage']}'" for o in objects)
    os.system(
        f"""docker compose exec -T db psql -U umny -d umny -qc "UPDATE stages SET status='in_progress' WHERE id IN ({ids}); UPDATE objects SET status='active' WHERE id IN (SELECT object_id FROM stages WHERE id IN ({ids}))" """
    )
    return objects, manager


stats = defaultdict(list)
errors = defaultdict(int)


async def timed(name, coro):
    t = time.perf_counter()
    try:
        r = await coro
        ok = r.status_code < 400
    except Exception as e:  # noqa: BLE001
        ok, r = False, e
    stats[name].append((time.perf_counter() - t) * 1000)
    if not ok:
        errors[name] += 1
        if errors[name] <= 3:
            print("ERR", name, getattr(r, "status_code", r), getattr(r, "text", "")[:200])


PHOTOS = [photo(i) for i in range(30)]


async def user(c, objects, manager, deadline, uid):
    rnd = random.Random(uid)
    n = 0
    while time.time() < deadline:
        o = rnd.choice(objects)
        roll = rnd.random()
        if roll < 0.7:
            h = o["cust"]
            await asyncio.gather(
                timed("GET object", c.get(f"{BASE}/objects/{o['obj']}", headers=h)),
                timed("GET stages", c.get(f"{BASE}/objects/{o['obj']}/stages", headers=h)),
                timed("GET feed", c.get(f"{BASE}/objects/{o['obj']}/feed?limit=20", headers=h)),
                timed("GET orders", c.get(f"{BASE}/objects/{o['obj']}/purchase-orders", headers=h)),
            )
        elif roll < 0.9:
            ch = await c.post(
                f"{BASE}/stages/{o['stage']}/capture-challenges", headers={**o["lite"], **idem()}
            )
            stats["POST challenge"].append(0)
            if ch.status_code != 201:
                errors["POST challenge"] += 1
                if errors["POST challenge"] <= 3:
                    print("ERR challenge", ch.status_code, ch.text[:200])
                continue
            data = PHOTOS[(uid * 7 + n) % len(PHOTOS)] + uuid.uuid4().bytes  # уникальный хеш
            code = ch.json()["code"]
            sig = o["dev"].sign(f"{hashlib.sha256(data).hexdigest()}:{code}")
            await timed(
                "POST photo",
                c.post(
                    f"{BASE}/stages/{o['stage']}/media",
                    data={"signature": sig, "challenge_id": ch.json()["challenge_id"]},
                    files={"file": ("p.jpg", data, "image/jpeg")},
                    headers={**o["lite"], **idem()},
                ),
            )
        else:
            await asyncio.gather(
                timed("GET all orders", c.get(f"{BASE}/purchase-orders", headers=manager)),
                timed("GET requests", c.get(f"{BASE}/material-requests", headers=manager)),
            )
        n += 1
        await asyncio.sleep(rnd.uniform(0.5, 1.5))  # «думает» человек


async def main():
    limits = httpx.Limits(max_connections=C * 4, max_keepalive_connections=C * 4)
    async with httpx.AsyncClient(timeout=30, limits=limits) as c:
        objects, manager = await setup(c)
        print(f"Объектов: {N}, пользователей: {C}, длительность: {D} с")
        start = time.time()
        await asyncio.gather(*(user(c, objects, manager, start + D, i) for i in range(C)))
        took = time.time() - start
    total = sum(len(v) for v in stats.values())
    print(f"Запросов: {total}, {total / took:.0f} в секунду, ошибок: {sum(errors.values())}")
    print(f"{'запрос':16} {'кол-во':>7} {'p50 мс':>8} {'p95 мс':>8} {'p99 мс':>8} {'ошибок':>7}")
    for name, xs in sorted(stats.items()):
        if name == "POST challenge":
            continue
        xs = sorted(xs)
        q = lambda p: xs[min(len(xs) - 1, int(len(xs) * p))]  # noqa: E731
        print(
            f"{name:16} {len(xs):7} {statistics.median(xs):8.0f} {q(0.95):8.0f} {q(0.99):8.0f} {errors[name]:7}"
        )


asyncio.run(main())

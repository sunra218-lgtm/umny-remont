import hashlib
import json
import uuid
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import line_total_kop
from app.models import Estimate, EstimateItem, Stage
from app.models.enums import EstimateItemCategory, EstimateType


def estimate_status(est: Estimate) -> str:
    if est.approved_at:
        return "approved"
    if est.submitted_at:
        return "submitted"
    return "draft"


async def load_items(db: AsyncSession, estimate_id: uuid.UUID) -> list[EstimateItem]:
    rows = await db.execute(
        select(EstimateItem)
        .where(EstimateItem.estimate_id == estimate_id)
        .order_by(EstimateItem.seq, EstimateItem.id)
    )
    return list(rows.scalars())


async def recalc_totals(db: AsyncSession, est: Estimate) -> list[EstimateItem]:
    await db.flush()
    items = await load_items(db, est.id)
    work = material = 0
    for item in items:
        total = line_total_kop(item.qty, item.unit_price_kop)
        if item.category == EstimateItemCategory.work:
            work += total
        else:
            material += total
    est.total_work_kop = work
    est.total_material_kop = material
    return items


async def next_item_seq(db: AsyncSession, estimate_id: uuid.UUID) -> int:
    current = await db.scalar(
        select(func.max(EstimateItem.seq)).where(EstimateItem.estimate_id == estimate_id)
    )
    return (current or 0) + 1


async def latest_version(
    db: AsyncSession, object_id: uuid.UUID, kind: EstimateType
) -> Estimate | None:
    return (
        await db.execute(
            select(Estimate)
            .where(
                Estimate.object_id == object_id,
                Estimate.type == kind,
                Estimate.deleted_at.is_(None),
            )
            .order_by(Estimate.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def copy_items(db: AsyncSession, source: Estimate, target: Estimate) -> None:
    for item in await load_items(db, source.id):
        db.add(
            EstimateItem(
                estimate_id=target.id,
                seq=item.seq,
                stage_id=item.stage_id,
                room=item.room,
                category=item.category,
                title=item.title,
                unit=item.unit,
                qty=item.qty,
                unit_price_kop=item.unit_price_kop,
                sku_id=item.sku_id,
                price_item_id=item.price_item_id,
            )
        )
    target.reserve_kop = source.reserve_kop


async def document_hash(db: AsyncSession, est: Estimate) -> str:
    """Хэш содержимого сметы: подпись относится ровно к этому набору позиций и сумм."""
    items = await load_items(db, est.id)
    stage_titles = {
        s.id: s.title
        for s in (await db.execute(select(Stage).where(Stage.object_id == est.object_id))).scalars()
    }
    payload = {
        "estimate_id": str(est.id),
        "object_id": str(est.object_id),
        "type": est.type.value,
        "version": est.version,
        "total_work_kop": est.total_work_kop,
        "total_material_kop": est.total_material_kop,
        "reserve_kop": est.reserve_kop,
        "items": [
            {
                "stage": stage_titles.get(i.stage_id) if i.stage_id else None,
                "room": i.room,
                "category": i.category.value,
                "title": i.title,
                "unit": i.unit,
                "qty": str(i.qty),
                "unit_price_kop": i.unit_price_kop,
            }
            for i in items
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


async def apply_stage_budgets(db: AsyncSession, est: Estimate) -> None:
    """После утверждения полной сметы бюджеты этапов берутся из её позиций."""
    work: dict[uuid.UUID, int] = defaultdict(int)
    material: dict[uuid.UUID, int] = defaultdict(int)
    for item in await load_items(db, est.id):
        if item.stage_id is None:
            continue
        total = line_total_kop(item.qty, item.unit_price_kop)
        bucket = work if item.category == EstimateItemCategory.work else material
        bucket[item.stage_id] += total
    stages = (await db.execute(select(Stage).where(Stage.object_id == est.object_id))).scalars()
    for stage in stages:
        stage.work_budget_kop = work.get(stage.id, 0)
        stage.material_budget_kop = material.get(stage.id, 0)

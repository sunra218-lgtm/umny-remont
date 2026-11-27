"""Сметы: версии, позиции, импорт из Excel, утверждение заказчиком с ПЭП по СМС."""

import uuid

from fastapi import APIRouter, File, Request, Response, UploadFile
from sqlalchemy import delete, select

from app.core.access import ObjectAccess, load_object
from app.core.audit import audit
from app.core.deps import Auth, CurrentUser, DbSession, client_ip
from app.core.money import line_total_kop
from app.core.problems import Problem
from app.core.redis import get_redis
from app.core.security import utcnow
from app.domain.statuses import ensure_transition
from app.models import Estimate, EstimateItem, PriceItem, Signature, Stage
from app.models.enums import EstimateType, ObjectStatus, SignedDocumentType
from app.modules.estimates import service
from app.modules.estimates.schemas import (
    EstimateCreate,
    EstimateOut,
    EstimateSummary,
    EstimateUpdate,
    ImportResult,
    ItemIn,
    ItemOut,
    ItemUpdate,
    SignConfirmIn,
    SignRequestOut,
)
from app.modules.estimates.xlsx import ImportError_, parse_xlsx, template_xlsx
from app.modules.objects.router import next_stage_seq
from app.modules.signing.service import confirm_signature, request_signature

router = APIRouter(tags=["estimates"])

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_UPLOAD = 5 * 1024 * 1024

ALLOWED_OBJECT_STATUS = {
    EstimateType.preliminary: {ObjectStatus.draft, ObjectStatus.survey, ObjectStatus.estimating},
    EstimateType.full: {ObjectStatus.estimating, ObjectStatus.contracting},
}


def _summary(est: Estimate) -> dict[str, object]:
    return {
        "id": est.id,
        "object_id": est.object_id,
        "type": est.type,
        "version": est.version,
        "total_work_kop": est.total_work_kop,
        "total_material_kop": est.total_material_kop,
        "reserve_kop": est.reserve_kop,
        "total_kop": est.total_work_kop + est.total_material_kop + est.reserve_kop,
        "status": service.estimate_status(est),
        "created_at": est.created_at,
        "submitted_at": est.submitted_at,
        "approved_at": est.approved_at,
    }


async def _is_latest(db: DbSession, est: Estimate) -> bool:
    latest = await service.latest_version(db, est.object_id, est.type)
    return latest is not None and latest.id == est.id


async def _out(
    db: DbSession, est: Estimate, access: ObjectAccess, items: list[EstimateItem] | None = None
) -> EstimateOut:
    if items is None:
        items = await service.load_items(db, est.id)
    can_approve = (
        access.is_customer
        and est.submitted_at is not None
        and est.approved_at is None
        and await _is_latest(db, est)
    )
    return EstimateOut(
        **_summary(est),
        items=[
            ItemOut(
                id=i.id,
                seq=i.seq,
                stage_id=i.stage_id,
                room=i.room,
                category=i.category,
                title=i.title,
                unit=i.unit,
                qty=i.qty,
                unit_price_kop=i.unit_price_kop,
                total_kop=line_total_kop(i.qty, i.unit_price_kop),
                price_item_id=i.price_item_id,
            )
            for i in items
        ],
        can_edit=access.can_edit_estimates and est.submitted_at is None,
        can_approve=can_approve,
    )


async def _load(
    db: DbSession, current: CurrentUser, estimate_id: uuid.UUID, *, lock: bool = False
) -> tuple[Estimate, ObjectAccess]:
    stmt = select(Estimate).where(Estimate.id == estimate_id, Estimate.deleted_at.is_(None))
    if lock:
        stmt = stmt.with_for_update()
    est = (await db.execute(stmt)).scalar_one_or_none()
    if est is None:
        raise Problem(404, "not-found", "Смета не найдена")
    access = await load_object(db, current, est.object_id, lock=lock)
    # Заказчик видит только версии, которые ему отправили.
    if access.is_customer and not access.can_edit_estimates and est.submitted_at is None:
        raise Problem(404, "not-found", "Смета не найдена")
    return est, access


async def _editable(
    db: DbSession, current: CurrentUser, estimate_id: uuid.UUID
) -> tuple[Estimate, ObjectAccess]:
    est, access = await _load(db, current, estimate_id, lock=True)
    if not access.can_edit_estimates:
        raise Problem(403, "forbidden", "Смету составляет сметчик или подрядчик")
    if est.submitted_at is not None:
        raise Problem(
            409,
            "estimate-locked",
            "Эта версия уже отправлена заказчику",
            "Чтобы что-то поменять, создайте новую версию.",
        )
    return est, access


async def _check_stage(db: DbSession, object_id: uuid.UUID, stage_id: uuid.UUID | None) -> None:
    if stage_id is None:
        return
    stage = await db.get(Stage, stage_id)
    if stage is None or stage.object_id != object_id or stage.deleted_at is not None:
        raise Problem(422, "stage-not-found", "Этап не найден на этом объекте")


# ---------- Версии ----------


@router.get(
    "/objects/{object_id}/estimates",
    response_model=list[EstimateSummary],
    summary="Все версии смет объекта",
)
async def list_estimates(
    object_id: uuid.UUID, current: Auth, db: DbSession
) -> list[EstimateSummary]:
    access = await load_object(db, current, object_id)
    stmt = select(Estimate).where(Estimate.object_id == object_id, Estimate.deleted_at.is_(None))
    if not access.can_edit_estimates:
        stmt = stmt.where(Estimate.submitted_at.is_not(None))
    rows = await db.execute(stmt.order_by(Estimate.type, Estimate.version.desc()))
    return [EstimateSummary(**_summary(e)) for e in rows.scalars()]


@router.post(
    "/objects/{object_id}/estimates",
    response_model=EstimateOut,
    status_code=201,
    summary="Создать новую версию сметы",
)
async def create_estimate(
    object_id: uuid.UUID, body: EstimateCreate, current: Auth, request: Request, db: DbSession
) -> EstimateOut:
    access = await load_object(db, current, object_id, lock=True)
    if not access.can_edit_estimates:
        raise Problem(403, "forbidden", "Смету составляет сметчик или подрядчик")
    if access.obj.status not in ALLOWED_OBJECT_STATUS[body.type]:
        hint = (
            "Полная смета составляется после замера."
            if body.type == EstimateType.full
            else "Предварительная смета уже не нужна на этом этапе."
        )
        raise Problem(409, "estimate-not-allowed", "Сейчас эту смету создать нельзя", hint)
    latest = await service.latest_version(db, object_id, body.type)
    if latest is not None and latest.submitted_at is None:
        raise Problem(
            409,
            "draft-exists",
            "Уже есть черновик этой сметы",
            "Закончите его или отправьте заказчику.",
            estimate_id=str(latest.id),
        )

    source: Estimate | None = None
    if body.copy_from_id:
        source = await db.get(Estimate, body.copy_from_id)
        if source is None or source.object_id != object_id:
            raise Problem(422, "estimate-not-found", "Смета для копирования не найдена")
    elif not body.empty:
        source = latest or (
            await service.latest_version(db, object_id, EstimateType.preliminary)
            if body.type == EstimateType.full
            else None
        )

    est = Estimate(
        object_id=object_id,
        type=body.type,
        version=(latest.version + 1) if latest else 1,
        created_by=current.id,
    )
    db.add(est)
    await db.flush()
    if source is not None:
        await service.copy_items(db, source, est)
    items = await service.recalc_totals(db, est)
    audit(
        db,
        entity_type="estimate",
        entity_id=est.id,
        action="estimate.created",
        actor_id=current.id,
        ip=client_ip(request),
        new={
            "type": est.type,
            "version": est.version,
            "copied_from": source.id if source else None,
        },
    )
    await db.commit()
    await db.refresh(est)
    return await _out(db, est, access, items)


@router.get(
    "/estimates/import-template.xlsx", summary="Шаблон Excel для импорта", response_class=Response
)
async def import_template() -> Response:
    return Response(
        template_xlsx(),
        media_type=XLSX_TYPE,
        headers={"Content-Disposition": 'attachment; filename="smeta-shablon.xlsx"'},
    )


@router.get("/estimates/{estimate_id}", response_model=EstimateOut, summary="Смета с позициями")
async def get_estimate(estimate_id: uuid.UUID, current: Auth, db: DbSession) -> EstimateOut:
    est, access = await _load(db, current, estimate_id)
    return await _out(db, est, access)


@router.get(
    "/estimates/{estimate_id}/versions",
    response_model=list[EstimateSummary],
    summary="Версии сметы того же типа",
)
async def versions(estimate_id: uuid.UUID, current: Auth, db: DbSession) -> list[EstimateSummary]:
    est, access = await _load(db, current, estimate_id)
    stmt = select(Estimate).where(
        Estimate.object_id == est.object_id,
        Estimate.type == est.type,
        Estimate.deleted_at.is_(None),
    )
    if not access.can_edit_estimates:
        stmt = stmt.where(Estimate.submitted_at.is_not(None))
    rows = await db.execute(stmt.order_by(Estimate.version.desc()))
    return [EstimateSummary(**_summary(e)) for e in rows.scalars()]


@router.patch("/estimates/{estimate_id}", response_model=EstimateOut, summary="Изменить резерв")
async def update_estimate(
    estimate_id: uuid.UUID, body: EstimateUpdate, current: Auth, request: Request, db: DbSession
) -> EstimateOut:
    est, access = await _editable(db, current, estimate_id)
    old = est.reserve_kop
    est.reserve_kop = body.reserve_kop
    audit(
        db,
        entity_type="estimate",
        entity_id=est.id,
        action="estimate.updated",
        actor_id=current.id,
        ip=client_ip(request),
        old={"reserve_kop": old},
        new={"reserve_kop": body.reserve_kop},
    )
    await db.commit()
    await db.refresh(est)
    return await _out(db, est, access)


@router.delete("/estimates/{estimate_id}", status_code=204, summary="Удалить черновик")
async def delete_estimate(
    estimate_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> Response:
    est, _ = await _editable(db, current, estimate_id)
    est.deleted_at = utcnow()
    audit(
        db,
        entity_type="estimate",
        entity_id=est.id,
        action="estimate.deleted",
        actor_id=current.id,
        ip=client_ip(request),
        old={"type": est.type, "version": est.version},
    )
    await db.commit()
    return Response(status_code=204)


@router.post(
    "/estimates/{estimate_id}/submit",
    response_model=EstimateOut,
    summary="Отправить заказчику на утверждение",
)
async def submit(
    estimate_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> EstimateOut:
    est, access = await _editable(db, current, estimate_id)
    items = await service.recalc_totals(db, est)
    if not items:
        raise Problem(409, "estimate-empty", "В смете нет позиций")
    if est.type == EstimateType.full and any(i.stage_id is None for i in items):
        raise Problem(
            409,
            "items-without-stage",
            "У части позиций не указан этап",
            "В полной смете каждая позиция относится к этапу: по этапам идёт оплата.",
        )
    est.submitted_at = utcnow()
    audit(
        db,
        entity_type="estimate",
        entity_id=est.id,
        action="estimate.submitted",
        actor_id=current.id,
        ip=client_ip(request),
        new={
            "total_work_kop": est.total_work_kop,
            "total_material_kop": est.total_material_kop,
            "reserve_kop": est.reserve_kop,
        },
    )
    await db.commit()
    await db.refresh(est)
    return await _out(db, est, access, items)


# ---------- Позиции ----------


@router.post(
    "/estimates/{estimate_id}/items",
    response_model=EstimateOut,
    status_code=201,
    summary="Добавить позицию",
)
async def add_item(
    estimate_id: uuid.UUID, body: ItemIn, current: Auth, request: Request, db: DbSession
) -> EstimateOut:
    est, access = await _editable(db, current, estimate_id)
    await _check_stage(db, est.object_id, body.stage_id)
    data = body.model_dump(exclude={"price_item_id"})
    if body.price_item_id:
        price_item = await db.get(PriceItem, body.price_item_id)
        if price_item is None or not price_item.is_active:
            raise Problem(422, "price-item-not-found", "Позиция справочника не найдена")
        for key in ("category", "title", "unit", "unit_price_kop"):
            if data[key] is None:
                data[key] = getattr(price_item, key)
        data["sku_id"] = price_item.sku
    item = EstimateItem(
        estimate_id=est.id,
        seq=await service.next_item_seq(db, est.id),
        price_item_id=body.price_item_id,
        **data,
    )
    db.add(item)
    items = await service.recalc_totals(db, est)
    audit(
        db,
        entity_type="estimate",
        entity_id=est.id,
        action="estimate.item_added",
        actor_id=current.id,
        ip=client_ip(request),
        new={"title": item.title, "qty": item.qty, "unit_price_kop": item.unit_price_kop},
    )
    await db.commit()
    await db.refresh(est)
    return await _out(db, est, access, items)


async def _editable_item(
    db: DbSession, current: CurrentUser, item_id: uuid.UUID
) -> tuple[EstimateItem, Estimate, ObjectAccess]:
    item = await db.get(EstimateItem, item_id)
    if item is None:
        raise Problem(404, "not-found", "Позиция не найдена")
    est, access = await _editable(db, current, item.estimate_id)
    return item, est, access


@router.patch("/estimate-items/{item_id}", response_model=EstimateOut, summary="Изменить позицию")
async def update_item(
    item_id: uuid.UUID, body: ItemUpdate, current: Auth, request: Request, db: DbSession
) -> EstimateOut:
    item, est, access = await _editable_item(db, current, item_id)
    changes = body.model_dump(exclude_unset=True)
    if "stage_id" in changes:
        await _check_stage(db, est.object_id, changes["stage_id"])
    for required in ("category", "title", "unit", "qty", "unit_price_kop"):
        if required in changes and changes[required] is None:
            raise Problem(422, "validation-error", "Поле не может быть пустым", field=required)
    old = {k: getattr(item, k) for k in changes}
    for key, value in changes.items():
        setattr(item, key, value)
    items = await service.recalc_totals(db, est)
    if changes:
        audit(
            db,
            entity_type="estimate",
            entity_id=est.id,
            action="estimate.item_updated",
            actor_id=current.id,
            ip=client_ip(request),
            old=old,
            new=changes | {"item_id": item.id},
        )
    await db.commit()
    await db.refresh(est)
    return await _out(db, est, access, items)


@router.delete("/estimate-items/{item_id}", response_model=EstimateOut, summary="Удалить позицию")
async def delete_item(
    item_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> EstimateOut:
    item, est, access = await _editable_item(db, current, item_id)
    await db.delete(item)
    items = await service.recalc_totals(db, est)
    audit(
        db,
        entity_type="estimate",
        entity_id=est.id,
        action="estimate.item_deleted",
        actor_id=current.id,
        ip=client_ip(request),
        old={"title": item.title, "qty": item.qty, "unit_price_kop": item.unit_price_kop},
    )
    await db.commit()
    await db.refresh(est)
    return await _out(db, est, access, items)


# ---------- Импорт из Excel ----------


@router.post(
    "/estimates/{estimate_id}/import-xlsx",
    response_model=ImportResult,
    summary="Загрузить позиции из Excel",
)
async def import_xlsx(
    estimate_id: uuid.UUID,
    current: Auth,
    request: Request,
    db: DbSession,
    file: UploadFile = File(...),
    mode: str = "replace",
) -> ImportResult:
    if mode not in {"replace", "append"}:
        raise Problem(422, "validation-error", "mode: replace или append")
    est, access = await _editable(db, current, estimate_id)
    content = await file.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        raise Problem(413, "file-too-large", "Файл больше 5 МБ")
    try:
        rows = parse_xlsx(content)
    except ImportError_ as exc:
        raise Problem(
            422,
            "import-failed",
            "Файл не загружен: исправьте строки",
            "Ничего не изменилось. Исправьте ошибки и загрузите файл снова.",
            rows=exc.errors,
        ) from None

    stages = {
        s.title.strip().lower(): s
        for s in (
            await db.execute(
                select(Stage).where(Stage.object_id == est.object_id, Stage.deleted_at.is_(None))
            )
        ).scalars()
    }
    created = 0
    seq_stage = await next_stage_seq(db, est.object_id)
    if mode == "replace":
        await db.execute(delete(EstimateItem).where(EstimateItem.estimate_id == est.id))
        seq_item = 1
    else:
        seq_item = await service.next_item_seq(db, est.id)
    for row in rows:
        stage_id = None
        if row.stage:
            key = row.stage.lower()
            if key not in stages:
                stage = Stage(object_id=est.object_id, seq=seq_stage, title=row.stage)
                db.add(stage)
                await db.flush()
                stages[key] = stage
                seq_stage += 1
                created += 1
            stage_id = stages[key].id
        db.add(
            EstimateItem(
                estimate_id=est.id,
                seq=seq_item,
                stage_id=stage_id,
                room=row.room,
                category=row.category,
                title=row.title,
                unit=row.unit,
                qty=row.qty,
                unit_price_kop=row.unit_price_kop,
            )
        )
        seq_item += 1
    items = await service.recalc_totals(db, est)
    audit(
        db,
        entity_type="estimate",
        entity_id=est.id,
        action="estimate.imported",
        actor_id=current.id,
        ip=client_ip(request),
        new={"file": file.filename, "mode": mode, "rows": len(rows), "stages_created": created},
    )
    await db.commit()
    await db.refresh(est)
    return ImportResult(
        imported=len(rows), stages_created=created, estimate=await _out(db, est, access, items)
    )


# ---------- Утверждение заказчиком ----------


async def _approvable(
    db: DbSession, current: CurrentUser, estimate_id: uuid.UUID
) -> tuple[Estimate, ObjectAccess]:
    est, access = await _load(db, current, estimate_id, lock=True)
    if not access.is_customer:
        raise Problem(403, "forbidden", "Утверждает смету заказчик")
    if est.approved_at is not None:
        raise Problem(409, "already-approved", "Смета уже утверждена")
    if est.submitted_at is None or not await _is_latest(db, est):
        raise Problem(
            409, "not-latest", "Это не последняя версия сметы", "Откройте актуальную версию."
        )
    return est, access


@router.post(
    "/estimates/{estimate_id}/approve/request",
    response_model=SignRequestOut,
    summary="Получить СМС-код для утверждения",
)
async def approve_request(
    estimate_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> SignRequestOut:
    est, _ = await _approvable(db, current, estimate_id)
    total_rub = (est.total_work_kop + est.total_material_kop + est.reserve_kop) / 100
    kind = "полной" if est.type == EstimateType.full else "предварительной"
    result = await request_signature(
        db,
        get_redis(),
        user=current.user,
        document_type=SignedDocumentType.estimate,
        document_id=est.id,
        document_hash=await service.document_hash(db, est),
        sms_text=(
            f"Код {{code}} — утверждение {kind} сметы v{est.version} "
            f"на {total_rub:,.2f} руб.".replace(",", " ")
        ),
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return SignRequestOut(
        signature_id=result.signature.id, expires_in=result.expires_in, debug_code=result.debug_code
    )


@router.post(
    "/estimates/{estimate_id}/approve",
    response_model=EstimateOut,
    summary="Утвердить смету кодом из СМС",
)
async def approve(
    estimate_id: uuid.UUID, body: SignConfirmIn, current: Auth, request: Request, db: DbSession
) -> EstimateOut:
    est, access = await _approvable(db, current, estimate_id)
    signature = await db.get(Signature, body.signature_id, with_for_update=True)
    if (
        signature is None
        or signature.document_id != est.id
        or signature.document_type != SignedDocumentType.estimate
    ):
        raise Problem(404, "not-found", "Запрос подписи не найден")
    await confirm_signature(
        get_redis(), signature, current.user, body.code, await service.document_hash(db, est)
    )
    est.approved_at = signature.signed_at
    est.approved_signature_id = signature.id
    obj = access.obj
    if est.type == EstimateType.full:
        await service.apply_stage_budgets(db, est)
        if obj.status == ObjectStatus.estimating:
            ensure_transition("object", obj.status, ObjectStatus.contracting)
            obj.status = ObjectStatus.contracting
            audit(
                db,
                entity_type="object",
                entity_id=obj.id,
                action="object.status_changed",
                actor_id=current.id,
                ip=client_ip(request),
                old={"status": ObjectStatus.estimating},
                new={"status": obj.status},
            )
    audit(
        db,
        entity_type="estimate",
        entity_id=est.id,
        action="estimate.approved",
        actor_id=current.id,
        ip=client_ip(request),
        new={
            "signature_id": signature.id,
            "document_hash": signature.document_hash,
            "total_kop": est.total_work_kop + est.total_material_kop + est.reserve_kop,
        },
    )
    await db.commit()
    await db.refresh(est)
    return await _out(db, est, access)

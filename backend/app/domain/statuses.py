"""Допустимые переходы статусов (ТЗ 2.3, раздел 5).

Статус меняется только сервисной командой через `ensure_transition`, а не прямым UPDATE.
Дополнительные бизнес-условия (например, «нет открытого спора») проверяет сама команда.
"""

from enum import StrEnum

from app.core.problems import Problem
from app.models.enums import (
    ActStatus,
    ObjectStatus,
    PaymentStatus,
    PurchaseOrderStatus,
    StageStatus,
)

_O = ObjectStatus
OBJECT_TRANSITIONS: dict[StrEnum, set[StrEnum]] = {
    _O.draft: {_O.survey, _O.cancelled},
    _O.survey: {_O.estimating, _O.cancelled},
    _O.estimating: {_O.contracting, _O.cancelled},
    _O.contracting: {_O.active, _O.cancelled},
    _O.active: {_O.completed, _O.dispute},
    _O.dispute: {_O.active},
    _O.completed: set(),
    _O.cancelled: set(),
}

_S = StageStatus
_STAGE_ACTIVE = {_S.funding, _S.ready, _S.in_progress, _S.inspection, _S.acceptance, _S.accepted}
STAGE_TRANSITIONS: dict[StrEnum, set[StrEnum]] = {
    _S.planned: {_S.funding},
    _S.funding: {_S.ready, _S.frozen},
    _S.ready: {_S.in_progress, _S.frozen},
    # Этап без стоп-точки идёт сразу на приёмку, со стоп-точкой — через проверку технадзора.
    _S.in_progress: {_S.inspection, _S.acceptance, _S.frozen},
    _S.inspection: {_S.acceptance, _S.in_progress, _S.frozen},
    _S.acceptance: {_S.accepted, _S.in_progress, _S.frozen},
    _S.accepted: {_S.paid, _S.frozen},
    _S.paid: set(),
    # После разбора спора этап возвращается в рабочий статус.
    _S.frozen: set(_STAGE_ACTIVE),
}

_A = ActStatus
ACT_TRANSITIONS: dict[StrEnum, set[StrEnum]] = {
    _A.draft: {_A.sent},
    _A.sent: {_A.signed_contractor, _A.rejected},
    _A.signed_contractor: {_A.signed_customer, _A.rejected},
    _A.signed_customer: {_A.released},
    _A.released: set(),
    _A.rejected: {_A.draft},
}

_P = PaymentStatus
PAYMENT_TRANSITIONS: dict[StrEnum, set[StrEnum]] = {
    _P.created: {_P.pending_funding, _P.failed},
    _P.pending_funding: {_P.held, _P.failed},
    _P.held: {_P.release_requested, _P.frozen, _P.refunded, _P.failed},
    _P.release_requested: {_P.released, _P.held, _P.failed},
    _P.frozen: {_P.held, _P.refunded, _P.failed},
    _P.released: set(),
    _P.refunded: set(),
    # Повтор после ошибки банка — с тем же idempotency_key.
    _P.failed: {_P.pending_funding, _P.held, _P.release_requested},
}

_PO = PurchaseOrderStatus
PURCHASE_ORDER_TRANSITIONS: dict[StrEnum, set[StrEnum]] = {
    _PO.draft: {_PO.proposed},
    _PO.proposed: {_PO.confirmed, _PO.expired, _PO.rejected},
    _PO.confirmed: {_PO.ordered},
    _PO.ordered: {_PO.delivering},
    _PO.delivering: {_PO.delivered, _PO.partially_delivered},
    _PO.partially_delivered: {_PO.delivering, _PO.delivered},
    _PO.delivered: set(),
    _PO.expired: set(),
    _PO.rejected: set(),
}

TRANSITIONS: dict[str, dict[StrEnum, set[StrEnum]]] = {
    "object": OBJECT_TRANSITIONS,
    "stage": STAGE_TRANSITIONS,
    "act": ACT_TRANSITIONS,
    "payment": PAYMENT_TRANSITIONS,
    "purchase_order": PURCHASE_ORDER_TRANSITIONS,
}


def can_transition(entity: str, current: StrEnum, target: StrEnum) -> bool:
    return target in TRANSITIONS[entity].get(current, set())


def ensure_transition(entity: str, current: StrEnum, target: StrEnum) -> None:
    if not can_transition(entity, current, target):
        raise Problem(
            409,
            "invalid-status-transition",
            "Действие недоступно в текущем статусе",
            f"{entity}: переход {current} → {target} не разрешён",
            entity=entity,
            current=str(current),
            target=str(target),
        )

import pytest

from app.core.ids import uuid7
from app.core.problems import Problem
from app.core.validators import (
    is_valid_account,
    is_valid_inn,
    is_valid_kpp,
    is_valid_ogrn,
    normalize_phone,
)
from app.domain.statuses import TRANSITIONS, can_transition, ensure_transition
from app.models.enums import ActStatus, ObjectStatus, PaymentStatus, StageStatus


def test_uuid7_version_and_order() -> None:
    ids = [uuid7() for _ in range(2000)]
    assert all(i.version == 7 for i in ids)
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+7 (916) 123-45-67", "+79161234567"),
        ("89161234567", "+79161234567"),
        ("9161234567", "+79161234567"),
    ],
)
def test_normalize_phone(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", "123", "+74951234567", "+1 916 123 45 67"])
def test_normalize_phone_rejects(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_phone(raw)


def test_requisites() -> None:
    assert is_valid_inn("7707083893")
    assert is_valid_inn("500100732259")
    assert not is_valid_inn("7707083890")
    assert not is_valid_inn("50010073225")
    assert is_valid_ogrn("1027700132195")
    assert is_valid_ogrn("304500116000157")
    assert not is_valid_ogrn("1027700132190")
    assert is_valid_kpp("773601001")
    assert is_valid_account("40702810938000000001", "044525225")
    assert not is_valid_account("40702810938000000001", "044525226")


def test_every_status_has_transition_rules() -> None:
    enums = {
        "object": ObjectStatus,
        "stage": StageStatus,
        "act": ActStatus,
        "payment": PaymentStatus,
    }
    for name, enum_cls in enums.items():
        assert set(TRANSITIONS[name]) == set(enum_cls), name


def test_key_transitions() -> None:
    assert can_transition("object", ObjectStatus.active, ObjectStatus.dispute)
    assert can_transition("object", ObjectStatus.dispute, ObjectStatus.active)
    assert not can_transition("object", ObjectStatus.active, ObjectStatus.cancelled)
    assert can_transition("stage", StageStatus.inspection, StageStatus.in_progress)
    assert not can_transition("stage", StageStatus.paid, StageStatus.frozen)
    assert not can_transition("act", ActStatus.released, ActStatus.draft)
    # Повторной выплаты по уже выплаченному нет.
    assert not can_transition("payment", PaymentStatus.released, PaymentStatus.release_requested)
    assert can_transition("payment", PaymentStatus.held, PaymentStatus.frozen)


def test_ensure_transition_raises_problem() -> None:
    with pytest.raises(Problem) as exc:
        ensure_transition("act", ActStatus.draft, ActStatus.released)
    assert exc.value.status == 409

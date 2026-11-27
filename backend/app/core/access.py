"""Доступ на уровне объекта (ТЗ 2.3, раздел 3): объект видят заказчик, сотрудники подрядчика,
назначенные на объект люди и администраторы платформы."""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.problems import Problem
from app.models import ObjectAssignment, RepairObject
from app.models.enums import AssignmentRole, OrgRole, OrgType

ESTIMATE_EDITOR_ROLES = {OrgRole.contractor_admin, OrgRole.estimator}


@dataclass
class ObjectAccess:
    obj: RepairObject
    is_customer: bool
    is_admin: bool
    is_platform_estimator: bool
    contractor_roles: set[OrgRole] = field(default_factory=set)
    assignment_roles: set[AssignmentRole] = field(default_factory=set)

    @property
    def can_view(self) -> bool:
        return (
            self.is_customer
            or self.is_admin
            or self.is_platform_estimator
            or bool(self.contractor_roles)
            or bool(self.assignment_roles)
        )

    @property
    def can_edit_estimates(self) -> bool:
        return (
            self.is_admin
            or self.is_platform_estimator
            or bool(self.contractor_roles & ESTIMATE_EDITOR_ROLES)
        )

    @property
    def can_manage_team(self) -> bool:
        return self.is_admin or OrgRole.contractor_admin in self.contractor_roles

    @property
    def can_order_services(self) -> bool:
        return self.is_customer or self.is_admin

    def flags(self) -> dict[str, bool]:
        return {
            "is_customer": self.is_customer,
            "is_admin": self.is_admin,
            "can_edit_estimates": self.can_edit_estimates,
            "can_manage_team": self.can_manage_team,
            "can_order_services": self.can_order_services,
        }


def _contractor_org_ids(current: CurrentUser) -> list[uuid.UUID]:
    return [o.id for _, o in current.memberships if o.type == OrgType.contractor]


def sees_all_objects(current: CurrentUser) -> bool:
    return current.has_platform_role(OrgRole.platform_admin, OrgRole.estimator)


def visible_objects_query(current: CurrentUser) -> Select[tuple[RepairObject]]:
    stmt = select(RepairObject).where(RepairObject.deleted_at.is_(None))
    if sees_all_objects(current):
        return stmt
    assigned = select(ObjectAssignment.object_id).where(ObjectAssignment.user_id == current.id)
    return stmt.where(
        or_(
            RepairObject.customer_id == current.id,
            RepairObject.contractor_org_id.in_(_contractor_org_ids(current)),
            RepairObject.id.in_(assigned),
        )
    )


async def load_object(
    db: AsyncSession, current: CurrentUser, object_id: uuid.UUID, *, lock: bool = False
) -> ObjectAccess:
    stmt = select(RepairObject).where(
        RepairObject.id == object_id, RepairObject.deleted_at.is_(None)
    )
    if lock:
        stmt = stmt.with_for_update()
    obj = (await db.execute(stmt)).scalar_one_or_none()
    if obj is None:
        raise Problem(404, "not-found", "Объект не найден")
    assignments = (
        await db.execute(
            select(ObjectAssignment.role).where(
                ObjectAssignment.object_id == obj.id, ObjectAssignment.user_id == current.id
            )
        )
    ).scalars()
    access = ObjectAccess(
        obj=obj,
        is_customer=obj.customer_id == current.id,
        is_admin=current.is_platform_admin,
        is_platform_estimator=current.has_platform_role(OrgRole.estimator),
        contractor_roles=(
            current.roles_in_org(obj.contractor_org_id) if obj.contractor_org_id else set()
        ),
        assignment_roles=set(assignments),
    )
    if not access.can_view:
        # 404, а не 403: чужой объект не должен даже подтверждать своё существование.
        raise Problem(404, "not-found", "Объект не найден")
    return access

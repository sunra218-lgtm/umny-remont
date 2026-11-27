"""Перечисления (ENUM) из ТЗ 2.3, разделы 3–5."""

from enum import StrEnum


class OrgType(StrEnum):
    contractor = "contractor"
    supplier = "supplier"
    platform = "platform"


class OrgRole(StrEnum):
    contractor_admin = "contractor_admin"
    estimator = "estimator"
    foreman = "foreman"
    supplier_manager = "supplier_manager"
    # Роли сотрудников платформы — членство в организации типа platform.
    platform_admin = "platform_admin"
    procurement_manager = "procurement_manager"
    inspector = "inspector"


ROLES_BY_ORG_TYPE: dict[OrgType, set[OrgRole]] = {
    OrgType.contractor: {OrgRole.contractor_admin, OrgRole.estimator, OrgRole.foreman},
    OrgType.supplier: {OrgRole.supplier_manager},
    OrgType.platform: {
        OrgRole.platform_admin,
        OrgRole.procurement_manager,
        OrgRole.inspector,
        OrgRole.estimator,
    },
}


class AssignmentRole(StrEnum):
    foreman = "foreman"
    inspector = "inspector"
    procurement_manager = "procurement_manager"


class SessionKind(StrEnum):
    web = "web"
    lite = "lite"


class ObjectStatus(StrEnum):
    draft = "draft"
    survey = "survey"
    estimating = "estimating"
    contracting = "contracting"
    active = "active"
    dispute = "dispute"
    completed = "completed"
    cancelled = "cancelled"


class ServiceOrderType(StrEnum):
    acceptance = "acceptance"
    survey = "survey"


class ServiceOrderStatus(StrEnum):
    created = "created"
    scheduled = "scheduled"
    paid = "paid"
    completed = "completed"
    cancelled = "cancelled"


class EstimateType(StrEnum):
    preliminary = "preliminary"
    full = "full"


class EstimateItemCategory(StrEnum):
    work = "work"
    material = "material"


class StageStatus(StrEnum):
    planned = "planned"
    funding = "funding"
    ready = "ready"
    in_progress = "in_progress"
    inspection = "inspection"
    acceptance = "acceptance"
    accepted = "accepted"
    paid = "paid"
    frozen = "frozen"


class InspectionResult(StrEnum):
    passed = "passed"
    failed = "failed"
    remarks = "remarks"


class MediaType(StrEnum):
    photo = "photo"
    video = "video"


class ReviewStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class ActStatus(StrEnum):
    draft = "draft"
    sent = "sent"
    signed_contractor = "signed_contractor"
    signed_customer = "signed_customer"
    released = "released"
    rejected = "rejected"


class SignedDocumentType(StrEnum):
    contract = "contract"
    estimate = "estimate"
    act = "act"
    inspection = "inspection"


class SignatureMethod(StrEnum):
    sms_pep = "sms_pep"
    gosklyuch = "gosklyuch"


class PaymentType(StrEnum):
    hold = "hold"
    release = "release"
    freeze = "freeze"
    unfreeze = "unfreeze"
    refund = "refund"


class PaymentStatus(StrEnum):
    created = "created"
    pending_funding = "pending_funding"
    held = "held"
    release_requested = "release_requested"
    released = "released"
    frozen = "frozen"
    refunded = "refunded"
    failed = "failed"


class PurchaseOrderStatus(StrEnum):
    draft = "draft"
    proposed = "proposed"
    confirmed = "confirmed"
    ordered = "ordered"
    delivering = "delivering"
    delivered = "delivered"
    partially_delivered = "partially_delivered"
    expired = "expired"
    rejected = "rejected"


class DeliveryStatus(StrEnum):
    pending = "pending"
    accepted = "accepted"
    accepted_with_discrepancies = "accepted_with_discrepancies"
    rejected = "rejected"


class DisputeStatus(StrEnum):
    open = "open"
    in_review = "in_review"
    resolved = "resolved"
    rejected = "rejected"


class DocumentType(StrEnum):
    contract = "contract"
    estimate = "estimate"
    act = "act"
    passport = "passport"
    inspection_report = "inspection_report"
    invoice = "invoice"
    other = "other"


class ConsentType(StrEnum):
    pd_processing = "pd_processing"
    photo_video = "photo_video"
    marketing = "marketing"


class NotificationChannel(StrEnum):
    sms = "sms"
    email = "email"
    vk = "vk"
    max = "max"


class NotificationStatus(StrEnum):
    queued = "queued"
    sent = "sent"
    failed = "failed"

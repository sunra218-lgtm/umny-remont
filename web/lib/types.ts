export type OrgType = "contractor" | "supplier" | "platform";
export type OrgRole =
  | "contractor_admin"
  | "estimator"
  | "foreman"
  | "supplier_manager"
  | "platform_admin"
  | "procurement_manager"
  | "inspector";

export type Me = {
  id: string;
  phone: string;
  email: string | null;
  full_name: string | null;
  created_at: string;
  is_platform_admin: boolean;
  memberships: { org_id: string; org_type: OrgType; org_name: string; role: OrgRole }[];
  consents: { type: string; text_version: string; granted_at: string }[];
};

export type Device = {
  id: string;
  kind: "web" | "lite";
  user_agent: string | null;
  ip: string | null;
  created_at: string;
  last_seen_at: string;
  is_current: boolean;
};

export type Org = {
  id: string;
  type: OrgType;
  inn: string;
  ogrn: string | null;
  kpp: string | null;
  legal_name: string;
  bank_bik: string | null;
  bank_account: string | null;
  bank_partner_id?: string | null;
  verified_at: string | null;
  created_at: string;
};

export type Member = {
  id: string;
  user_id: string;
  phone: string;
  full_name: string | null;
  role: OrgRole;
  created_at: string;
};

export type AdminUser = {
  id: string;
  phone: string;
  email: string | null;
  full_name: string | null;
  created_at: string;
  blocked_at: string | null;
};

export type AuditEntry = {
  id: number;
  created_at: string;
  entity_type: string;
  entity_id: string | null;
  actor_id: string | null;
  action: string;
  new_val: Record<string, unknown> | null;
  old_val: Record<string, unknown> | null;
  ip: string | null;
  is_system: boolean;
};

export type Page<T> = { items: T[]; next_cursor: string | null };

export type ObjectStatus =
  | "draft"
  | "survey"
  | "estimating"
  | "contracting"
  | "active"
  | "dispute"
  | "completed"
  | "cancelled";

export type Person = { id: string; phone: string; full_name: string | null };

export type RepairObject = {
  id: string;
  status: ObjectStatus;
  address_text: string;
  area_m2: string | null;
  customer: Person | null;
  contractor_org_id: string | null;
  contractor_name: string | null;
  match_mode?: "platform" | "market";
  package?: "standard" | "lux";
  created_at: string;
  access: {
    is_customer: boolean;
    is_admin: boolean;
    can_edit_estimates: boolean;
    can_manage_team: boolean;
    can_order_services: boolean;
    can_buy?: boolean;
    is_crew?: boolean;
  };
};

export type Assignment = { id: string; role: "foreman" | "inspector" | "procurement_manager"; user: Person; created_at: string };

export type Stage = {
  id: string;
  seq: number;
  title: string;
  stage_type: string | null;
  is_stop_point: boolean;
  planned_start: string | null;
  planned_end: string | null;
  work_budget_kop: number;
  material_budget_kop: number;
  status: string;
};

export type ServiceOrder = {
  id: string;
  object_id: string;
  type: "acceptance" | "survey";
  status: "created" | "scheduled" | "paid" | "completed" | "cancelled";
  price_kop: number;
  slot_at: string | null;
  paid_at: string | null;
  completed_at: string | null;
  customer_comment: string | null;
  result_notes: string | null;
  inspector: Person | null;
  created_at: string;
};

export type EstimateSummary = {
  id: string;
  object_id: string;
  type: "preliminary" | "full";
  version: number;
  total_work_kop: number;
  total_material_kop: number;
  reserve_kop: number;
  total_kop: number;
  status: "draft" | "submitted" | "approved";
  created_at: string;
  submitted_at: string | null;
  approved_at: string | null;
};

export type EstimateItem = {
  id: string;
  seq: number;
  stage_id: string | null;
  room: string | null;
  category: "work" | "material";
  title: string;
  unit: string;
  qty: string;
  unit_price_kop: number;
  total_kop: number;
  price_item_id: string | null;
};

export type Estimate = EstimateSummary & { items: EstimateItem[]; can_edit: boolean; can_approve: boolean };

export type PriceItem = {
  id: string;
  category: "work" | "material";
  stage_type: string | null;
  title: string;
  unit: string;
  unit_price_kop: number;
  sku: string | null;
  is_active: boolean;
};

export type MediaItem = {
  id: string;
  object_id: string;
  stage_id: string | null;
  stage_title: string | null;
  checklist_item_id: string | null;
  checklist_item_title: string | null;
  author_name: string | null;
  received_server_at: string;
  captured_client_at: string | null;
  review_status: "pending" | "approved" | "rejected";
  review_comment: string | null;
  fraud_flags: Record<string, unknown> | null;
  url: string;
};

export type OrgDevice = {
  id: string;
  user_id: string;
  phone: string;
  full_name: string | null;
  kind: "web" | "lite";
  user_agent: string | null;
  created_at: string;
  last_seen_at: string;
};

export type ChecklistRow = {
  id: string;
  seq: number;
  title: string;
  required_angle: string | null;
  photo_required: boolean;
  photos: number;
};

export type StageChecklist = { stage_id: string; template_title: string | null; items: ChecklistRow[]; missing: string[] };

export type ChecklistTemplate = {
  id: string;
  stage_type: string;
  stage_type_title: string;
  title: string;
  items: { id: string; seq: number; title: string; required_angle: string | null; photo_required: boolean }[];
};

export type Inspection = {
  id: string;
  stage_id: string;
  stage_title: string;
  stage_status: string;
  object_id: string;
  address: string;
  inspector_id: string;
  inspector_name: string;
  scheduled_at: string | null;
  result: "passed" | "failed" | "remarks" | null;
  remarks: string | null;
  checks?: { key: string; ok: boolean; note: string | null; title: string }[];
  signed_at: string | null;
  created_at: string;
};

export type Payment = {
  id: string;
  stage_id: string | null;
  object_id: string;
  act_id: string | null;
  purchase_order_id: string | null;
  status: "created" | "pending_funding" | "held" | "release_requested" | "released" | "frozen" | "refunded" | "failed";
  amount_kop: number;
  breakdown: { work_kop?: number; material_kop?: number; platform_fee_kop?: number };
  recipients: { role: string; inn: string; amount_kop: number }[];
  pay_url: string | null;
  expires_at: string | null;
  created_at: string;
  stage_title: string | null;
  address: string | null;
};

export type Act = {
  id: string;
  stage_id: string;
  number: string;
  work_kop: number;
  material_kop: number;
  status: "draft" | "sent" | "signed_contractor" | "signed_customer" | "released" | "rejected";
  reject_reason: string | null;
  created_at: string;
  contractor_signed_at: string | null;
  customer_signed_at: string | null;
  payment_status: Payment["status"] | null;
  can_sign: boolean;
  can_reject: boolean;
};

export type Dispute = {
  id: string;
  object_id: string;
  stage_id: string | null;
  stage_title: string | null;
  address: string | null;
  opened_by: string;
  opened_by_name: string | null;
  reason: string;
  status: "open" | "in_review" | "resolved" | "rejected";
  outcome: "continue" | "refund" | null;
  resolution: string | null;
  stage_status_before: string | null;
  payment_status: Payment["status"] | null;
  created_at: string;
  resolved_at: string | null;
};

export type MaterialRequest = {
  id: string;
  object_id: string;
  address: string | null;
  stage_id: string | null;
  stage_title: string | null;
  author_name: string | null;
  text: string | null;
  audio_url: string | null;
  status: "new" | "in_work" | "done";
  created_at: string;
};

export type OrderStatus =
  | "draft"
  | "proposed"
  | "confirmed"
  | "ordered"
  | "delivering"
  | "delivered"
  | "partially_delivered"
  | "expired"
  | "rejected";

export type PurchaseOrder = {
  id: string;
  object_id: string;
  address: string | null;
  stage_id: string | null;
  stage_title: string | null;
  request_id: string | null;
  supplier_org_id: string | null;
  supplier_name: string | null;
  status: OrderStatus;
  items: { id: string; title: string; sku: string | null; qty: string; unit_price_kop: number; total_kop: number }[];
  total_kop: number;
  supplier_cost_kop: number | null;
  margin_kop: number | null;
  comment: string | null;
  reject_reason: string | null;
  proposed_at: string | null;
  expires_at: string | null;
  confirmed_by_customer_at: string | null;
  delivery_slot_at: string | null;
  created_at: string;
  payment: { id: string; status: Payment["status"]; pay_url: string | null } | null;
  deliveries: {
    id: string;
    status: "pending" | "accepted" | "accepted_with_discrepancies" | "rejected";
    delivered_at: string | null;
    received_by_name: string | null;
    discrepancies: { title: string; expected: string | null; received: string | null; comment: string | null }[];
    comment: string | null;
    invoice_url: string | null;
  }[];
  can_edit: boolean;
  can_confirm: boolean;
  can_order: boolean;
  can_ship: boolean;
  can_receive: boolean;
  can_complete: boolean;
};

export type AppNotification = { id: string; text: string; status: "queued" | "sent" | "failed"; created_at: string };

export type Kpi = {
  metrics: { key: string; title: string; value: number; target: number; unit: string; ok: boolean; note: string | null }[];
  objects: {
    object_id: string;
    address: string;
    contractor: string | null;
    status: string;
    since: string;
    working_days: number;
    days_with_reports: number;
    last_report_at: string | null;
  }[];
  modes?: ModeStats[];
  generated_at: string;
};

export type ModeStats = {
  mode: "platform" | "market";
  title: string;
  objects: number;
  with_contractor: number;
  contractor_pct: number;
  hours_to_contractor: number | null;
  reached_work: number;
  reached_work_pct: number;
  bids_per_task: number | null;
  tasks_without_bids: number | null;
  avg_rating: number | null;
  reviews: number;
};

export type TechStep = {
  key: string;
  title: string;
  how: string;
  tolerance: string | null;
  check: string | null;
  mistakes: string[];
  photo: string | null;
  hidden: boolean;
  inspector: boolean;
  example_key?: string | null;
};

export type TechMaterial = {
  key: string;
  name: string;
  rate: number;
  per: string;
  unit: string;
  per_mm: boolean;
  thickness_mm: number | null;
  pack: number | null;
  pack_title: string | null;
  reserve_pct: number;
  note: string | null;
};

export type TechCard = {
  stage_type: string;
  stage_type_title: string;
  title: string;
  summary: string | null;
  norms: string[];
  steps: TechStep[];
  materials: TechMaterial[];
  version: number;
  verified_at: string | null;
  updated_at: string;
};

export type TechCardBrief = {
  stage_type: string;
  stage_type_title: string;
  title: string;
  steps: number;
  version: number;
  verified_at: string | null;
};

export type DailyPlanItem = {
  id: string;
  title: string;
  stage_id: string | null;
  stage_title: string | null;
  done: boolean;
  note: string | null;
};

export type DailyPlan = {
  id: string;
  object_id: string;
  day: string;
  status: "planned" | "reported" | "accepted" | "returned";
  items: DailyPlanItem[];
  report_note: string | null;
  reported_at: string | null;
  review_note: string | null;
  reviewed_at: string | null;
  photos: number;
  can_edit: boolean;
  can_review: boolean;
};

export type CalcRow = {
  key: string;
  name: string;
  unit: string;
  per: string;
  quantity: number;
  thickness_mm: number | null;
  packs: number | null;
  pack_title: string | null;
  note: string | null;
};

export type AssistantAnswer = {
  answer: string;
  sources: { stage_type: string; card_title: string; step_key: string; step_title: string }[];
  found: boolean;
  by: "search" | "yandexgpt";
};

export type EngineerQuestion = {
  id: string;
  object_id: string;
  address: string;
  stage_id: string | null;
  stage_title: string | null;
  step_key: string | null;
  author_name: string;
  text: string;
  photo_url: string | null;
  status: "open" | "answered";
  answer: string | null;
  answered_at: string | null;
  created_at: string;
  can_answer: boolean;
};

export type CameraState = "off" | "paused" | "sleeping" | "no_signal" | "live";

export type Camera = {
  id: string;
  object_id: string;
  title: string;
  kind: "ip" | "phone";
  snapshot_url: string | null;
  interval_min: number;
  hour_from: number;
  hour_to: number;
  enabled: boolean;
  consent_at: string | null;
  state: CameraState;
  last_frame_at: string | null;
  last_seen_at: string | null;
  last_error: string | null;
  latest_frame_url: string | null;
  frames_today: number;
  can_toggle: boolean;
  can_edit: boolean;
  phone_link: string | null;
};

export type CameraFrame = {
  id: string;
  taken_at: string;
  url: string;
  changed: boolean;
  stage_id: string | null;
  stage_title: string | null;
  hidden_work: boolean;
  labels: { note?: string; hidden_steps?: string[]; source?: string };
  labeled_by: "auto" | "engineer";
};

export type CameraFrames = {
  day: string;
  days: { day: string; frames: number; changed: number }[];
  frames: CameraFrame[];
  can_label: boolean;
};

export type PhoneCameraStatus = {
  title: string;
  enabled: boolean;
  state: CameraState;
  message: string;
  interval_min: number;
  hour_from: number;
  hour_to: number;
  next_in_s: number;
  frames_today: number;
};

export type ContractorCard = {
  org_id: string;
  name: string;
  legal_name: string;
  kind: "company" | "brigade";
  city: string | null;
  specializations: string[];
  team_size: number | null;
  since_year: number | null;
  verified: boolean;
  accepts_tasks: boolean;
  rating: number | null;
  reviews: number;
  objects_done: number;
  objects_in_work: number;
  cover_url: string | null;
  vip?: boolean;
};

export type ContractorFull = ContractorCard & {
  about: string | null;
  portfolio: { id: string; url: string; caption: string | null }[];
  review_list: { rating: number; text: string | null; created_at: string }[];
  can_edit: boolean;
};

export type MarketBid = {
  id: string;
  org_id: string;
  contractor: ContractorCard | null;
  price_kop: number;
  days: number;
  start_date: string | null;
  message: string | null;
  status: "submitted" | "accepted" | "rejected" | "withdrawn";
  created_at: string;
};

export type MarketTask = {
  id: string;
  lux?: boolean;
  object_id: string;
  title: string;
  description: string | null;
  area_hint: string;
  area_m2: number | null;
  work_types: string[];
  budget_kop: number | null;
  desired_start: string | null;
  bids_until: string | null;
  status: "open" | "awarded" | "closed";
  created_at: string;
  bids_count: number;
  bids: MarketBid[];
  my_bid: MarketBid | null;
};

export type ContractorReview = { rating: number; text: string | null; created_at: string };

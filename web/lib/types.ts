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
  created_at: string;
  access: {
    is_customer: boolean;
    is_admin: boolean;
    can_edit_estimates: boolean;
    can_manage_team: boolean;
    can_order_services: boolean;
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

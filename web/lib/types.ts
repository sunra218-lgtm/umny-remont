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

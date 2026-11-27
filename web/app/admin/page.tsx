"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api, errorText } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { formatDate, formatPhone, ORG_TYPE_LABELS, ROLE_LABELS, ROLES_BY_ORG_TYPE } from "@/lib/format";
import type { AdminUser, AuditEntry, Member, Org, OrgRole, OrgType, Page } from "@/lib/types";

import { ErrorNote, Loading, Shell } from "../components";

type Tab = "orgs" | "users" | "audit";

export default function AdminPage() {
  const { me, loading } = useRequireAuth({ admin: true });
  const [tab, setTab] = useState<Tab>("orgs");
  if (loading || !me?.is_platform_admin) return <Shell><Loading /></Shell>;
  return (
    <Shell wide>
      <h1>Администрирование</h1>
      <div className="tabs" role="tablist">
        {(
          [
            ["orgs", "Организации"],
            ["users", "Пользователи"],
            ["audit", "Журнал действий"],
          ] as [Tab, string][]
        ).map(([key, label]) => (
          <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? "tab active" : "tab"} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </div>
      {tab === "orgs" && <OrgsTab />}
      {tab === "users" && <UsersTab />}
      {tab === "audit" && <AuditTab />}
    </Shell>
  );
}

const EMPTY_ORG = { type: "contractor" as OrgType, inn: "", legal_name: "", ogrn: "", kpp: "", bank_bik: "", bank_account: "" };

function OrgsTab() {
  const [orgs, setOrgs] = useState<Org[] | null>(null);
  const [selected, setSelected] = useState<Org | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState(EMPTY_ORG);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setOrgs((await api<Page<Org>>("/organizations?limit=200")).items);
    } catch (err) {
      setError(errorText(err));
    }
  }, []);
  useEffect(() => void load(), [load]);

  async function create(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const body = Object.fromEntries(Object.entries(form).map(([k, v]) => [k, v.trim() || null]));
    try {
      const org = await api<Org>("/organizations", { method: "POST", body });
      setForm(EMPTY_ORG);
      setCreating(false);
      await load();
      setSelected(org);
    } catch (err) {
      setError(errorText(err));
    }
  }

  const field = (key: keyof typeof EMPTY_ORG, label: string, hint?: string) => (
    <div>
      <label htmlFor={`org-${key}`}>{label}</label>
      <input id={`org-${key}`} value={form[key]} placeholder={hint} onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
    </div>
  );

  return (
    <div className="split">
      <section className="card">
        <div className="row between">
          <h2>Организации</h2>
          {!creating && (
            <button className="primary small" onClick={() => setCreating(true)}>
              Добавить организацию
            </button>
          )}
        </div>
        {creating && (
          <form onSubmit={create} className="grid2 boxed">
            <div>
              <label htmlFor="org-type">Тип</label>
              <select id="org-type" value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value as OrgType })}>
                <option value="contractor">Подрядчик</option>
                <option value="supplier">Поставщик</option>
              </select>
            </div>
            {field("legal_name", "Название", "ООО «Ремонт»")}
            {field("inn", "ИНН", "10 или 12 цифр")}
            {field("ogrn", "ОГРН / ОГРНИП")}
            {field("kpp", "КПП")}
            {field("bank_bik", "БИК банка")}
            {field("bank_account", "Расчётный счёт", "20 цифр")}
            <div className="row">
              <button type="submit" className="primary">Сохранить организацию</button>
              <button type="button" className="secondary" onClick={() => { setCreating(false); setError(null); }}>
                Отменить
              </button>
            </div>
          </form>
        )}
        <ErrorNote text={error} />
        {orgs === null ? (
          <Loading />
        ) : orgs.length === 0 ? (
          <div className="empty">
            <p>Организаций пока нет.</p>
            <p className="muted">Добавьте первого подрядчика или поставщика, чтобы пригласить их сотрудников.</p>
          </div>
        ) : (
          <div className="table-wrap"><table>
            <thead>
              <tr>
                <th>Название</th>
                <th>Тип</th>
                <th>ИНН</th>
                <th>Проверка</th>
              </tr>
            </thead>
            <tbody>
              {orgs.map((o) => (
                <tr key={o.id} className={selected?.id === o.id ? "selected" : undefined} onClick={() => setSelected(o)}>
                  <td>
                    <button className="link">{o.legal_name}</button>
                  </td>
                  <td>{ORG_TYPE_LABELS[o.type]}</td>
                  <td>{o.inn}</td>
                  <td>{o.verified_at ? <span className="ok">Проверена</span> : <span className="muted">Нет</span>}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </section>
      {selected && (
        <OrgDetail
          key={selected.id}
          org={selected}
          onChanged={async (org) => {
            setSelected(org);
            await load();
          }}
        />
      )}
    </div>
  );
}

function OrgDetail({ org, onChanged }: { org: Org; onChanged: (org: Org) => Promise<void> }) {
  const roles = ROLES_BY_ORG_TYPE[org.type];
  const [members, setMembers] = useState<Member[] | null>(null);
  const [phone, setPhone] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<OrgRole>(roles[0]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setMembers(await api<Member[]>(`/organizations/${org.id}/members`));
    } catch (err) {
      setError(errorText(err));
    }
  }, [org.id]);
  useEffect(() => void load(), [load]);

  async function add(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api(`/organizations/${org.id}/members`, { method: "POST", body: { phone, role, full_name: name || null } });
      setPhone("");
      setName("");
      await load();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function remove(m: Member) {
    setError(null);
    try {
      await api(`/organizations/${org.id}/members/${m.id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function verify() {
    try {
      await onChanged(await api<Org>(`/organizations/${org.id}/verify`, { method: "POST" }));
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <section className="card">
      <div className="row between">
        <h2>{org.legal_name}</h2>
        {org.type !== "platform" && !org.verified_at && (
          <button className="secondary small" onClick={() => void verify()}>
            Отметить как проверенную
          </button>
        )}
      </div>
      <dl className="facts">
        <dt>Тип</dt>
        <dd>{ORG_TYPE_LABELS[org.type]}</dd>
        <dt>ИНН</dt>
        <dd>{org.inn}</dd>
        {org.ogrn && (<><dt>ОГРН</dt><dd>{org.ogrn}</dd></>)}
        {org.kpp && (<><dt>КПП</dt><dd>{org.kpp}</dd></>)}
        {org.bank_account && (<><dt>Счёт</dt><dd>{org.bank_account}, БИК {org.bank_bik}</dd></>)}
      </dl>

      <h3>Сотрудники</h3>
      {members === null ? (
        <Loading />
      ) : members.length === 0 ? (
        <p className="muted">Сотрудников пока нет. Добавьте руководителя по номеру телефона, дальше он пригласит команду сам.</p>
      ) : (
        <ul className="list">
          {members.map((m) => (
            <li key={m.id}>
              <div>
                <b>{m.full_name || formatPhone(m.phone)}</b>
                <div className="muted">
                  {ROLE_LABELS[m.role]} · {formatPhone(m.phone)}
                </div>
              </div>
              <button className="secondary small" onClick={() => void remove(m)}>
                Убрать
              </button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={add} className="grid2 boxed">
        <div>
          <label htmlFor="m-phone">Телефон сотрудника</label>
          <input id="m-phone" type="tel" value={phone} placeholder="+7 900 000-00-00" onChange={(e) => setPhone(e.target.value)} />
        </div>
        <div>
          <label htmlFor="m-name">Имя (необязательно)</label>
          <input id="m-name" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label htmlFor="m-role">Роль</label>
          <select id="m-role" value={role} onChange={(e) => setRole(e.target.value as OrgRole)}>
            {roles.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABELS[r]}
              </option>
            ))}
          </select>
        </div>
        <div className="row end">
          <button type="submit" className="primary">Добавить сотрудника</button>
        </div>
      </form>
      <ErrorNote text={error} />
    </section>
  );
}

/** Телефон ищем по цифрам без кода страны, остальное — как есть. */
function searchQuery(raw: string): string {
  const q = raw.trim();
  if (!/^[\d\s()+-]+$/.test(q)) return q;
  const digits = q.replace(/\D/g, "");
  return digits.length === 11 && /^[78]/.test(digits) ? digits.slice(1) : digits;
}

function UsersTab() {
  const [q, setQ] = useState("");
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (query: string) => {
    try {
      const params = query ? `?q=${encodeURIComponent(query)}&limit=100` : "?limit=100";
      setUsers((await api<Page<AdminUser>>(`/admin/users${params}`)).items);
    } catch (err) {
      setError(errorText(err));
    }
  }, []);
  useEffect(() => {
    const t = setTimeout(() => void load(searchQuery(q)), 250);
    return () => clearTimeout(t);
  }, [q, load]);

  async function toggle(u: AdminUser) {
    setError(null);
    try {
      await api(`/admin/users/${u.id}/${u.blocked_at ? "unblock" : "block"}`, { method: "POST" });
      await load(searchQuery(q));
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <section className="card">
      <label htmlFor="user-q">Поиск по телефону, имени или e-mail</label>
      <input id="user-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Например, 916 123" />
      <ErrorNote text={error} />
      {users === null ? (
        <Loading />
      ) : users.length === 0 ? (
        <p className="empty">Никого не нашли. Попробуйте другой запрос.</p>
      ) : (
        <div className="table-wrap"><table>
          <thead>
            <tr>
              <th>Телефон</th>
              <th>Имя</th>
              <th>Зарегистрирован</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{formatPhone(u.phone)}</td>
                <td>{u.full_name ?? <span className="muted">не указано</span>}</td>
                <td>{formatDate(u.created_at)}</td>
                <td className="end">
                  <button className={u.blocked_at ? "secondary small" : "danger small"} onClick={() => void toggle(u)}>
                    {u.blocked_at ? "Разблокировать" : `Заблокировать ${formatPhone(u.phone)}`}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </section>
  );
}

const ACTION_LABELS: Record<string, string> = {
  "user.registered": "Регистрация",
  "user.invited": "Приглашён по телефону",
  "user.updated": "Изменён профиль",
  "user.blocked": "Пользователь заблокирован",
  "user.unblocked": "Пользователь разблокирован",
  "auth.login": "Вход",
  "auth.logout": "Выход",
  "auth.device_revoked": "Завершён сеанс на устройстве",
  "organization.created": "Добавлена организация",
  "organization.updated": "Изменены реквизиты",
  "organization.verified": "Организация проверена",
  "org_member.added": "Добавлен сотрудник",
  "org_member.removed": "Убран сотрудник",
  "consent.granted.pd_processing": "Согласие на обработку ПДн",
  "consent.granted.marketing": "Согласие на рассылки",
  "consent.revoked.marketing": "Отзыв согласия на рассылки",
};

function AuditTab() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (from: string | null) => {
    try {
      const page = await api<Page<AuditEntry>>(`/admin/audit-logs?limit=50${from ? `&cursor=${from}` : ""}`);
      setEntries((prev) => (from ? [...prev, ...page.items] : page.items));
      setCursor(page.next_cursor);
      setLoaded(true);
    } catch (err) {
      setError(errorText(err));
    }
  }, []);
  useEffect(() => void load(null), [load]);

  return (
    <section className="card">
      <p className="muted">Каждое действие записывается сюда навсегда: записи нельзя изменить или удалить.</p>
      <ErrorNote text={error} />
      {!loaded ? (
        <Loading />
      ) : (
        <div className="table-wrap"><table>
          <thead>
            <tr>
              <th>Когда</th>
              <th>Действие</th>
              <th>Кто</th>
              <th>Подробности</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id}>
                <td className="nowrap">{formatDate(e.created_at)}</td>
                <td>{ACTION_LABELS[e.action] ?? e.action}</td>
                <td className="mono">{e.is_system ? "система" : e.actor_id?.slice(-8) ?? "—"}</td>
                <td className="mono small">{e.new_val ? JSON.stringify(e.new_val) : ""}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      {cursor && (
        <button className="secondary" onClick={() => void load(cursor)}>
          Показать ещё
        </button>
      )}
    </section>
  );
}

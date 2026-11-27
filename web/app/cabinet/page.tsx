"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api, errorText } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { describeDevice, formatDate, formatPhone, ORG_TYPE_LABELS, ROLE_LABELS } from "@/lib/format";
import type { AppNotification, Device, Me, OrgDevice } from "@/lib/types";

import { ErrorNote, Loading, Shell } from "../components";

export default function CabinetPage() {
  const { me, loading, reload } = useRequireAuth();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [devices, setDevices] = useState<Device[]>([]);

  const loadDevices = useCallback(async () => {
    try {
      setDevices(await api<Device[]>("/auth/devices"));
    } catch (err) {
      setError(errorText(err));
    }
  }, []);

  useEffect(() => {
    if (me) {
      setName(me.full_name ?? "");
      setEmail(me.email ?? "");
      void loadDevices();
    }
  }, [me, loadDevices]);

  if (loading || !me) return <Shell><Loading /></Shell>;

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    try {
      await api("/me", { method: "PATCH", body: { full_name: name || null, email: email || null } });
      await reload();
      setSaved(true);
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function revoke(id: string) {
    try {
      await api(`/auth/devices/${id}`, { method: "DELETE" });
      await loadDevices();
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <Shell>
      <h1>Личный кабинет</h1>
      <section className="card">
        <h2>Профиль</h2>
        <p className="muted">Телефон для входа: {formatPhone(me.phone)}</p>
        <form onSubmit={save} className="grid2">
          <div>
            <label htmlFor="name">Имя и фамилия</label>
            <input id="name" value={name} onChange={(e) => { setName(e.target.value); setSaved(false); }} autoComplete="name" />
          </div>
          <div>
            <label htmlFor="email">E-mail</label>
            <input id="email" type="email" value={email} onChange={(e) => { setEmail(e.target.value); setSaved(false); }} autoComplete="email" />
          </div>
          <div className="row">
            <button type="submit" className="primary">Сохранить профиль</button>
            {saved && <span className="ok">Сохранено</span>}
          </div>
        </form>
        <ErrorNote text={error} />
      </section>

      <section className="card">
        <h2>Мои роли</h2>
        {me.memberships.length === 0 ? (
          <p className="muted">
            Вы вошли как заказчик. Если вы работаете в строительной компании или у поставщика, попросите руководителя
            добавить вас по номеру телефона.
          </p>
        ) : (
          <ul className="list">
            {me.memberships.map((m) => (
              <li key={`${m.org_id}-${m.role}`}>
                <b>{ROLE_LABELS[m.role]}</b>
                <span className="muted">
                  {m.org_name} · {ORG_TYPE_LABELS[m.org_type]}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card">
        <h2>Устройства, где выполнен вход</h2>
        <ul className="list">
          {devices.map((d) => (
            <li key={d.id}>
              <div>
                <b>{describeDevice(d.user_agent)}</b>
                {d.is_current && <span className="badge">Это устройство</span>}
                <div className="muted">Последний вход: {formatDate(d.last_seen_at)}</div>
              </div>
              {!d.is_current && (
                <button type="button" className="secondary small" onClick={() => void revoke(d.id)}>
                  Завершить сеанс
                </button>
              )}
            </li>
          ))}
        </ul>
      </section>

      {me.memberships
        .filter((m) => m.role === "contractor_admin")
        .map((m) => (
          <TeamDevices key={m.org_id} orgId={m.org_id} orgName={m.org_name} me={me} />
        ))}

      <section className="card">
        <h2>Согласия</h2>
        <ul className="list">
          {me.consents.map((c) => (
            <li key={c.type}>
              <b>
                {c.type === "pd_processing"
                  ? "Обработка персональных данных"
                  : c.type === "marketing"
                    ? "Новости и предложения"
                    : "Фото и видео в квартире"}
              </b>
              <span className="muted">
                {formatDate(c.granted_at)} · версия {c.text_version}
              </span>
            </li>
          ))}
        </ul>
      </section>
      <Notifications />
    </Shell>
  );
}

function Notifications() {
  const [items, setItems] = useState<AppNotification[] | null>(null);
  useEffect(() => {
    api<AppNotification[]>("/me/notifications")
      .then(setItems)
      .catch(() => setItems([]));
  }, []);
  if (!items || items.length === 0) return null;
  return (
    <section className="card">
      <h2>Последние СМС-уведомления</h2>
      <ul className="list">
        {items.map((n) => (
          <li key={n.id}>
            <span>{n.text}</span>
            <span className="muted small nowrap">
              {formatDate(n.created_at)}
              {n.status === "queued" ? " · отправляется" : n.status === "failed" ? " · не доставлено" : ""}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Руководитель видит телефоны сотрудников и может отключить потерянный или чужой. */
function TeamDevices({ orgId, orgName, me }: { orgId: string; orgName: string; me: Me }) {
  const [devices, setDevices] = useState<OrgDevice[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setDevices(await api<OrgDevice[]>(`/organizations/${orgId}/devices`));
    } catch (err) {
      setError(errorText(err));
    }
  }, [orgId]);
  useEffect(() => void load(), [load]);

  async function revoke(id: string) {
    setError(null);
    try {
      await api(`/auth/devices/${id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(errorText(err));
    }
  }

  const others = devices?.filter((d) => d.user_id !== me.id) ?? [];
  return (
    <section className="card">
      <h2>Где вошли сотрудники · {orgName}</h2>
      {devices === null ? (
        error ? null : <Loading />
      ) : others.length === 0 ? (
        <p className="muted">Сотрудники пока не входили. Прорабу отправьте ссылку для входа со страницы объекта.</p>
      ) : (
        <ul className="list">
          {others.map((d) => (
            <li key={d.id}>
              <div>
                <b>{d.full_name ?? formatPhone(d.phone)}</b>
                {d.kind === "lite" && <span className="badge">Приложение бригады</span>}
                <div className="muted">
                  {describeDevice(d.user_agent)} · последний вход {formatDate(d.last_seen_at)}
                </div>
              </div>
              <button type="button" className="secondary small" onClick={() => void revoke(d.id)}>
                Завершить сеанс
              </button>
            </li>
          ))}
        </ul>
      )}
      <ErrorNote text={error} />
    </section>
  );
}

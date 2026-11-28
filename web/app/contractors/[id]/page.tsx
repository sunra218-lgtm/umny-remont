"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { api, errorText } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { formatDate } from "@/lib/format";
import type { ContractorFull } from "@/lib/types";

import { ErrorNote, Loading, Shell } from "../../components";
import { ContractorSummary, WorkTypes } from "../../market";

/** Профиль подрядчика: о себе, портфолио, отзывы. Руководитель подрядчика его редактирует. */
export default function ContractorPage() {
  const { id } = useParams<{ id: string }>();
  const { me, loading } = useRequireAuth();
  const [c, setC] = useState<ContractorFull | null>(null);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setC(await api<ContractorFull>(`/contractors/${id}`));
    } catch (err) {
      setError(errorText(err));
    }
  }, [id]);
  useEffect(() => {
    if (me) void load();
  }, [me, load]);

  if (loading || !me) return <Shell><Loading /></Shell>;
  return (
    <Shell>
      <p>
        <Link href="/contractors">← Все подрядчики</Link>
      </p>
      <ErrorNote text={error} />
      {!c ? (
        error ? null : <Loading />
      ) : (
        <>
          <h1>{c.name}</h1>
          {c.name !== c.legal_name && <p className="muted">{c.legal_name}</p>}
          {!c.verified && <p className="error">Организация ещё не прошла проверку и не видна заказчикам.</p>}
          <section className="card">
            <ContractorSummary c={c} link={false} />
            {c.about && <p className="pre">{c.about}</p>}
            {c.can_edit && !editing && (
              <button type="button" className="secondary" onClick={() => setEditing(true)}>
                Изменить профиль
              </button>
            )}
            {editing && (
              <ProfileForm
                c={c}
                onSaved={(next) => {
                  setC(next);
                  setEditing(false);
                }}
                onCancel={() => setEditing(false)}
              />
            )}
          </section>
          {me.is_platform_admin && c.verified && <VipToggle c={c} onChanged={load} />}
          <Portfolio c={c} onChanged={setC} />
          <section className="card">
            <h2>Отзывы заказчиков</h2>
            {c.review_list.length === 0 ? (
              <p className="muted">Отзывов пока нет. Их оставляют заказчики после завершения ремонта на платформе.</p>
            ) : (
              <ul className="list">
                {c.review_list.map((r, i) => (
                  <li key={i}>
                    <div>
                      <span className="stars">{"★".repeat(r.rating)}{"☆".repeat(5 - r.rating)}</span>{" "}
                      <span className="muted small">{formatDate(r.created_at)}</span>
                      {r.text && <p>{r.text}</p>}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </Shell>
  );
}

function ProfileForm({ c, onSaved, onCancel }: { c: ContractorFull; onSaved: (c: ContractorFull) => void; onCancel: () => void }) {
  const [kind, setKind] = useState(c.kind);
  const [name, setName] = useState(c.name);
  const [city, setCity] = useState(c.city ?? "");
  const [about, setAbout] = useState(c.about ?? "");
  const [types, setTypes] = useState(c.specializations);
  const [team, setTeam] = useState(c.team_size ? String(c.team_size) : "");
  const [since, setSince] = useState(c.since_year ? String(c.since_year) : "");
  const [accepts, setAccepts] = useState(c.accepts_tasks);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      onSaved(
        await api<ContractorFull>(`/contractors/${c.org_id}/profile`, {
          method: "PUT",
          body: {
            kind,
            display_name: name,
            city: city || null,
            about: about || null,
            specializations: types,
            team_size: team ? Number(team) : null,
            since_year: since ? Number(since) : null,
            accepts_tasks: accepts,
          },
        }),
      );
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <form onSubmit={submit} className="boxed">
      <label htmlFor="pf-kind">Кто вы</label>
      <select id="pf-kind" value={kind} onChange={(e) => setKind(e.target.value as "company" | "brigade")}>
        <option value="company">Строительная компания</option>
        <option value="brigade">Бригада или частный мастер</option>
      </select>
      <label htmlFor="pf-name">Название для заказчиков</label>
      <input id="pf-name" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
      <label htmlFor="pf-city">Город</label>
      <input id="pf-city" value={city} maxLength={100} onChange={(e) => setCity(e.target.value)} />
      <label htmlFor="pf-about">О себе</label>
      <textarea id="pf-about" rows={4} maxLength={4000} value={about} onChange={(e) => setAbout(e.target.value)} />
      <span className="field-title">Виды работ</span>
      <WorkTypes value={types} onChange={setTypes} />
      <div className="filters">
        <div>
          <label htmlFor="pf-team">Человек в команде</label>
          <input id="pf-team" inputMode="numeric" value={team} onChange={(e) => setTeam(e.target.value.replace(/\D/g, ""))} />
        </div>
        <div>
          <label htmlFor="pf-since">Работаете с года</label>
          <input id="pf-since" inputMode="numeric" maxLength={4} value={since} onChange={(e) => setSince(e.target.value.replace(/\D/g, ""))} />
        </div>
      </div>
      <label className="check">
        <input type="checkbox" checked={accepts} onChange={(e) => setAccepts(e.target.checked)} />
        <span>Получать заявки заказчиков с маркетплейса</span>
      </label>
      <ErrorNote text={error} />
      <div className="row">
        <button type="submit" className="primary" disabled={name.trim().length < 2}>
          Сохранить
        </button>
        <button type="button" className="link" onClick={onCancel}>
          Отмена
        </button>
      </div>
    </form>
  );
}

function Portfolio({ c, onChanged }: { c: ContractorFull; onChanged: (c: ContractorFull) => void }) {
  const [caption, setCaption] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function upload(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("photo", file);
    if (caption.trim()) form.append("caption", caption.trim());
    try {
      onChanged(await api<ContractorFull>(`/contractors/${c.org_id}/portfolio`, { form }));
      setCaption("");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(photoId: string) {
    try {
      onChanged(await api<ContractorFull>(`/contractors/${c.org_id}/portfolio/${photoId}`, { method: "DELETE" }));
    } catch (err) {
      setError(errorText(err));
    }
  }

  if (c.portfolio.length === 0 && !c.can_edit) return null;
  return (
    <section className="card">
      <h2>Портфолио</h2>
      {c.portfolio.length === 0 && <p className="muted">Добавьте фото готовых ремонтов — заказчики смотрят их перед выбором.</p>}
      <ul className="portfolio">
        {c.portfolio.map((p) => (
          <li key={p.id}>
            <a href={p.url} target="_blank" rel="noreferrer">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={p.url} alt={p.caption ?? "Фото работы"} loading="lazy" />
            </a>
            {p.caption && <div className="small">{p.caption}</div>}
            {c.can_edit && (
              <button type="button" className="link small" onClick={() => void remove(p.id)}>
                Убрать
              </button>
            )}
          </li>
        ))}
      </ul>
      {c.can_edit && (
        <div className="boxed">
          <label htmlFor="pf-caption">Подпись к фото (необязательно)</label>
          <input id="pf-caption" value={caption} maxLength={200} onChange={(e) => setCaption(e.target.value)} placeholder="Например, санузел 4 м², 2025" />
          <label htmlFor="pf-photo">Фото</label>
          <input id="pf-photo" type="file" accept="image/jpeg,image/webp,image/png" disabled={busy} onChange={(e) => void upload(e.target.files?.[0])} />
          <ErrorNote text={error} />
        </div>
      )}
    </section>
  );
}

/** Администратор отмечает VIP-подрядчиков: только они берут объекты «Люкс». */
function VipToggle({ c, onChanged }: { c: ContractorFull; onChanged: () => Promise<void> }) {
  const [error, setError] = useState<string | null>(null);
  async function toggle() {
    setError(null);
    try {
      await api(`/contractors/${c.org_id}/vip`, { method: "PUT", body: { vip: !c.vip } });
      await onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  }
  return (
    <section className="card">
      <h2>VIP-мастера</h2>
      <p className="muted small">
        {c.vip
          ? "Подрядчик в списке VIP: ему доступны объекты пакета «Люкс»."
          : "Объекты пакета «Люкс» берут только VIP-подрядчики с лучшими оценками и портфолио."}
      </p>
      <ErrorNote text={error} />
      <button type="button" className="secondary" onClick={() => void toggle()}>
        {c.vip ? "Убрать из VIP" : "Сделать VIP"}
      </button>
    </section>
  );
}

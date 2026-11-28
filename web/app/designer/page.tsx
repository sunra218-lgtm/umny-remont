"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api, ApiError, errorText } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { parseRub, rub } from "@/lib/format";

import { ErrorNote, Loading, Shell } from "../components";
import type { DesignInfo } from "../design";
import { CONTRACT_STATUS, type Contract, type Designer } from "../soul";

type Curated = { id: string; address: string; status: string; open_signals: number; worst: string | null };

/** Кабинет дизайнера интерьера и куратора: профиль, портфолио, приглашения, проекты. */
export default function DesignerPage() {
  const { me, loading } = useRequireAuth();
  const [profile, setProfile] = useState<Designer | null | undefined>(undefined);
  const [projects, setProjects] = useState<Contract[]>([]);
  const [curated, setCurated] = useState<Curated[]>([]);
  const [styles, setStyles] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [info, proj, cur] = await Promise.all([
        api<DesignInfo>("/design/info"),
        api<Contract[]>("/designers/me/projects"),
        api<Curated[]>("/curator/objects"),
      ]);
      setStyles(Object.fromEntries(info.styles.map((s) => [s.key, s.title])));
      setProjects(proj);
      setCurated(cur);
      if (me) {
        try {
          setProfile(await api<Designer>(`/designers/${me.id}`));
        } catch (err) {
          if (err instanceof ApiError && err.code === "not-found") setProfile(null);
          else throw err;
        }
      }
    } catch (err) {
      setError(errorText(err));
    }
  }, [me]);
  useEffect(() => {
    if (me) void load();
  }, [me, load]);

  if (loading || !me) return <Shell><Loading /></Shell>;
  if (profile === undefined) return <Shell>{error ? <ErrorNote text={error} /> : <Loading />}</Shell>;

  return (
    <Shell wide>
      <h1>Дизайнерам и кураторам</h1>
      <ErrorNote text={error} />
      <div className="split">
        <div>
          {me.is_platform_admin && <VerifyList onChanged={load} />}
          {curated.length > 0 && (
            <section className="card">
              <h2>Курирую</h2>
              <ul className="list">
                {curated.map((c) => (
                  <li key={c.id} className="row between">
                    <Link href={`/objects/${c.id}#curator`}>{c.address}</Link>
                    {c.open_signals > 0 ? (
                      <span className={`badge${c.worst === "info" ? "" : " warn"}`}>сигналов: {c.open_signals}</span>
                    ) : (
                      <span className="muted small">всё по плану</span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
          <section className="card">
            <h2>Проекты</h2>
            {projects.length === 0 ? (
              <p className="muted">
                {profile?.verified
                  ? "Приглашений пока нет. Заказчики видят вас в разделе «Ремонт с душой»."
                  : "Заполните профиль и добавьте фото работ: после проверки заказчики смогут вас пригласить."}
              </p>
            ) : (
              projects.map((c) => <ProjectCard key={c.id} c={c} styles={styles} onChanged={load} />)
            )}
          </section>
        </div>
        <div>
          <ProfileForm profile={profile} styles={styles} onSaved={load} />
          {profile && <Photos profile={profile} onChanged={load} />}
        </div>
      </div>
    </Shell>
  );
}

function ProjectCard({ c, styles, onChanged }: { c: Contract; styles: Record<string, string>; onChanged: () => Promise<void> }) {
  const [fee, setFee] = useState(c.fee_kop ? String(c.fee_kop / 100) : "");
  const [days, setDays] = useState(String(c.days ?? 21));
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      await onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  }
  function offer(e: FormEvent) {
    e.preventDefault();
    const kop = parseRub(fee);
    if (!kop) {
      setError("Укажите цену числом");
      return;
    }
    void act(() => api(`/designer-contracts/${c.id}/offer`, { method: "POST", body: { fee_kop: kop, days: Number(days) || 21, message: message.trim() || null } }));
  }
  return (
    <div className="boxed">
      <p>
        <b>{c.area_hint}</b>
        {c.area_m2 && <span className="muted"> · {c.area_m2} м²</span>}
        {c.style && <span className="muted"> · {styles[c.style] ?? c.style}</span>}
        <br />
        <span className={`badge${["invited", "working"].includes(c.status) ? " warn" : ""}`}>{CONTRACT_STATUS[c.status]}</span>
        {c.fee_kop !== null && <span className="small"> · {rub(c.fee_kop)}, {c.days} дн.</span>}
      </p>
      {c.message && <p className="pre small">Заказчик: {c.message}</p>}
      {c.feedback && c.status === "working" && <p className="small">Просит доработать: {c.feedback}</p>}
      <ErrorNote text={error} />
      {(c.status === "invited" || c.status === "offered") && (
        <form onSubmit={offer}>
          <div className="row">
            <label>
              Цена проекта, ₽
              <input inputMode="decimal" value={fee} onChange={(e) => setFee(e.target.value)} required />
            </label>
            <label>
              Срок, дней
              <input type="number" min={1} max={365} value={days} onChange={(e) => setDays(e.target.value)} />
            </label>
          </div>
          <label htmlFor={`m-${c.id}`}>Сообщение заказчику</label>
          <textarea id={`m-${c.id}`} rows={2} maxLength={2000} value={message} onChange={(e) => setMessage(e.target.value)} />
          <p className="row">
            <button type="submit">{c.status === "offered" ? "Изменить условия" : "Взяться за проект"}</button>
            <button type="button" className="link" onClick={() => void act(() => api(`/designer-contracts/${c.id}/decline`, { method: "POST" }))}>
              Отказаться
            </button>
          </p>
        </form>
      )}
      {["working", "submitted", "approved"].includes(c.status) && (
        <Link className="button" href={`/objects/${c.object_id}/design`}>
          Открыть проект
        </Link>
      )}
    </div>
  );
}

function ProfileForm({ profile, styles, onSaved }: { profile: Designer | null; styles: Record<string, string>; onSaved: () => Promise<void> }) {
  const [f, setF] = useState({
    display_name: profile?.display_name ?? "",
    city: profile?.city ?? "",
    about: profile?.about ?? "",
    price: profile?.price_m2_kop ? String(profile.price_m2_kop / 100) : "",
    since_year: profile?.since_year ? String(profile.since_year) : "",
    styles: profile?.styles ?? [],
    accepts_projects: profile?.accepts_projects ?? true,
  });
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api("/designers/me", {
        method: "PUT",
        body: {
          display_name: f.display_name,
          city: f.city || null,
          about: f.about || null,
          price_m2_kop: f.price ? parseRub(f.price) : null,
          since_year: f.since_year ? Number(f.since_year) : null,
          styles: f.styles,
          accepts_projects: f.accepts_projects,
        },
      });
      setSaved(true);
      await onSaved();
    } catch (err) {
      setError(errorText(err));
    }
  }
  return (
    <section className="card">
      <h2>{profile ? "Мой профиль" : "Стать дизайнером платформы"}</h2>
      {!profile && (
        <p className="small">
          Сервис подбирает отделку, а в «Ремонте с душой» последнее слово за дизайнером. Заказчики приглашают вас в свой проект, вы
          называете цену и срок.
        </p>
      )}
      {profile && !profile.verified && <p className="badge warn">Профиль на проверке у администратора</p>}
      <ErrorNote text={error} />
      <form onSubmit={submit}>
        <label htmlFor="d-name">Имя или название студии</label>
        <input id="d-name" required minLength={2} value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} />
        <div className="row">
          <label>
            Город
            <input value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} />
          </label>
          <label>
            В профессии с
            <input type="number" min={1950} max={2100} value={f.since_year} onChange={(e) => setF({ ...f, since_year: e.target.value })} />
          </label>
          <label>
            Цена за м², ₽
            <input inputMode="decimal" value={f.price} onChange={(e) => setF({ ...f, price: e.target.value })} />
          </label>
        </div>
        <span className="field-title">Стили</span>
        <div className="chips" role="group" aria-label="Стили">
          {Object.entries(styles).map(([k, v]) => (
            <button
              key={k}
              type="button"
              className="chip"
              aria-pressed={f.styles.includes(k)}
              onClick={() => setF({ ...f, styles: f.styles.includes(k) ? f.styles.filter((x) => x !== k) : [...f.styles, k] })}
            >
              {v}
            </button>
          ))}
        </div>
        <label htmlFor="d-about">О себе</label>
        <textarea id="d-about" rows={4} maxLength={5000} value={f.about} onChange={(e) => setF({ ...f, about: e.target.value })} />
        <label className="check">
          <input type="checkbox" checked={f.accepts_projects} onChange={(e) => setF({ ...f, accepts_projects: e.target.checked })} /> Беру новые проекты
        </label>
        <button type="submit">{saved ? "Сохранено ✓" : "Сохранить"}</button>
      </form>
    </section>
  );
}

function Photos({ profile, onChanged }: { profile: Designer; onChanged: () => Promise<void> }) {
  const [error, setError] = useState<string | null>(null);
  async function upload(file: File) {
    setError(null);
    const form = new FormData();
    form.append("photo", file);
    try {
      await api("/designers/me/photos", { method: "POST", form });
      await onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  }
  return (
    <section className="card">
      <h2>Портфолио</h2>
      <ErrorNote text={error} />
      {profile.photos.length > 0 && (
        <ul className="portfolio">
          {profile.photos.map((p) => (
            <li key={p.id}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={p.url} alt={p.caption ?? ""} loading="lazy" />
              <button type="button" className="link small" onClick={() => void api(`/designers/me/photos/${p.id}`, { method: "DELETE" }).then(onChanged)}>
                Убрать
              </button>
            </li>
          ))}
        </ul>
      )}
      <label className="field-title">
        Добавить фото работы
        <input
          type="file"
          accept="image/jpeg,image/png,image/webp"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void upload(f);
            e.target.value = "";
          }}
        />
      </label>
    </section>
  );
}

/** Администратор проверяет дизайнеров перед тем, как их увидят заказчики. */
function VerifyList({ onChanged }: { onChanged: () => Promise<void> }) {
  const [list, setList] = useState<Designer[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setList(await api<Designer[]>("/designers?all=true"));
    } catch (err) {
      setError(errorText(err));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  if (!list || list.length === 0) return null;
  return (
    <section className="card">
      <h2>Дизайнеры платформы</h2>
      <ErrorNote text={error} />
      <ul className="list">
        {list.map((d) => (
          <li key={d.user_id} className="row between">
            <span>
              <b>{d.display_name}</b> <span className="muted small">{d.city}</span>
              {!d.verified && <span className="badge warn">на проверке</span>}
            </span>
            <button
              type="button"
              className="secondary small"
              onClick={() =>
                void api(`/designers/${d.user_id}/verify`, { method: "PUT", body: { verified: !d.verified } })
                  .then(load)
                  .then(onChanged)
                  .catch((err) => setError(errorText(err)))
              }
            >
              {d.verified ? "Снять проверку" : "Проверен"}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

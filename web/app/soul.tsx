"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api, errorText } from "@/lib/api";
import { formatDate, formatPhone, parseRub, rub } from "@/lib/format";
import type { RepairObject } from "@/lib/types";

import { ErrorNote } from "./components";

export type Designer = {
  user_id: string;
  display_name: string;
  about: string | null;
  city: string | null;
  styles: string[];
  price_m2_kop: number | null;
  since_year: number | null;
  accepts_projects: boolean;
  verified: boolean;
  projects_done: number;
  cover_url: string | null;
  photos: { id: string; url: string; caption: string | null }[];
  phone: string | null;
  can_edit: boolean;
};

export type Contract = {
  id: string;
  object_id: string;
  area_hint: string | null;
  area_m2: number | null;
  style: string | null;
  designer: Designer | null;
  status: string;
  message: string | null;
  fee_kop: number | null;
  days: number | null;
  concept: string | null;
  room_notes: Record<string, string>;
  feedback: string | null;
  rounds: number;
  submitted_at: string | null;
  approved_at: string | null;
  created_at: string;
};

export type Soul = {
  soul: boolean;
  can_change: boolean;
  is_designer: boolean;
  finalized_at: string | null;
  contract: Contract | null;
  history: Contract[];
};

export type Decor = {
  id: string;
  room_key: string;
  title: string;
  note: string | null;
  link: string | null;
  furniture_item_id: string | null;
  price_kop: number | null;
  qty: number;
  by_designer: boolean;
};

export const CONTRACT_STATUS: Record<string, string> = {
  invited: "Приглашён, ждём ответа",
  offered: "Назвал цену и срок",
  working: "Работает над проектом",
  submitted: "Сдал проект, ждёт вашего решения",
  approved: "Проект утверждён",
  declined: "Отказался",
  cancelled: "Отменено",
};

function DesignerCard({ d, styles, action }: { d: Designer; styles: Record<string, string>; action?: React.ReactNode }) {
  return (
    <div className="contractor">
      {d.cover_url && (
        // eslint-disable-next-line @next/next/no-img-element
        <img className="contractor-cover" src={d.cover_url} alt="" loading="lazy" />
      )}
      <div className="grow">
        <b>{d.display_name}</b>
        <div className="muted small">
          {[d.city, d.since_year && `с ${d.since_year} г.`, d.styles.map((s) => styles[s] ?? s).join(", ")].filter(Boolean).join(" · ")}
        </div>
        <div className="muted small">
          {d.price_m2_kop ? `от ${rub(d.price_m2_kop)} за м²` : "цена по договорённости"} · проектов на платформе: {d.projects_done}
        </div>
        {d.about && <p className="small">{d.about}</p>}
        {d.phone && <p className="small">Телефон: {formatPhone(d.phone)}</p>}
        {action}
      </div>
    </div>
  );
}

/** «Ремонт с душой» на странице дизайн-проекта: дизайнер, его концепция, утверждение. */
export function SoulPanel({
  objectId,
  styles,
  rooms,
  onChanged,
}: {
  objectId: string;
  styles: Record<string, string>;
  rooms: { key: string; name: string }[];
  onChanged: () => Promise<void>;
}) {
  const [soul, setSoul] = useState<Soul | null>(null);
  const [designers, setDesigners] = useState<Designer[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");

  const load = useCallback(async () => {
    try {
      setSoul(await api<Soul>(`/objects/${objectId}/soul`));
    } catch (err) {
      setError(errorText(err));
    }
  }, [objectId]);
  useEffect(() => {
    void load();
  }, [load]);

  const c = soul?.contract ?? null;
  const choosing = !!soul?.soul && soul.can_change && (!c || c.status === "declined" || c.status === "cancelled");
  useEffect(() => {
    if (choosing && designers === null) void api<Designer[]>("/designers").then(setDesigners).catch(() => setDesigners([]));
  }, [choosing, designers]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      const r = await fn();
      if (r && typeof r === "object" && "soul" in r) setSoul(r as Soul);
      else await load();
      await onChanged();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  if (!soul) return error ? <ErrorNote text={error} /> : null;
  if (!soul.soul && !soul.can_change) return null;

  return (
    <section className={`card${soul.soul ? " soul-card" : ""}`} id="designer">
      <h2>Ремонт с душой</h2>
      <ErrorNote text={error} />
      {!soul.soul ? (
        <>
          <p>
            Сервис подбирает отделку сам, а в «Ремонте с душой» финальное слово за живым дизайнером интерьера. Он доводит
            проект до вашего характера, пишет концепцию и наполняет комнаты мебелью, светом и декором.
          </p>
          <button type="button" disabled={busy} onClick={() => void act(() => api(`/objects/${objectId}/soul`, { method: "PUT", body: { soul: true } }))}>
            Хочу дизайнера
          </button>
        </>
      ) : (
        <>
          {soul.finalized_at && (
            <p className="final-note">
              Дизайн утверждён {formatDate(soul.finalized_at)}. Чтобы что-то поменять, попросите дизайнера доработать проект.
            </p>
          )}
          {c && c.designer && !choosing && (
            <div className="boxed">
              <DesignerCard d={c.designer} styles={styles} />
              <p>
                <span className={`badge${c.status === "submitted" || c.status === "offered" ? " warn" : ""}`}>{CONTRACT_STATUS[c.status]}</span>
                {c.fee_kop !== null && (
                  <span className="small">
                    {" "}
                    · {rub(c.fee_kop)}, {c.days} дн.
                  </span>
                )}
              </p>
              {c.message && <p className="pre small">{c.message}</p>}
              {c.concept && (
                <>
                  <h3>Концепция</h3>
                  <p className="pre">{c.concept}</p>
                </>
              )}
              {c.feedback && c.status === "working" && <p className="muted small">Вы просили: {c.feedback}</p>}
              {soul.can_change && c.status === "offered" && (
                <p className="row">
                  <button type="button" disabled={busy} onClick={() => void act(() => api(`/designer-contracts/${c.id}/accept`, { method: "POST" }))}>
                    Согласен, начинаем
                  </button>
                  <button type="button" className="link" disabled={busy} onClick={() => void act(() => api(`/designer-contracts/${c.id}/cancel`, { method: "POST" }))}>
                    Выбрать другого
                  </button>
                </p>
              )}
              {soul.can_change && (c.status === "submitted" || c.status === "approved") && (
                <div>
                  {c.status === "submitted" && (
                    <button type="button" disabled={busy} onClick={() => void act(() => api(`/designer-contracts/${c.id}/review`, { method: "POST", body: { approve: true } }))}>
                      Утвердить дизайн
                    </button>
                  )}
                  <label htmlFor="soul-fb">Что доработать</label>
                  <textarea id="soul-fb" rows={2} maxLength={5000} value={feedback} onChange={(e) => setFeedback(e.target.value)} />
                  <button
                    type="button"
                    className="secondary"
                    disabled={busy || !feedback.trim()}
                    onClick={() =>
                      void act(() => api(`/designer-contracts/${c.id}/review`, { method: "POST", body: { approve: false, feedback } })).then(() => setFeedback(""))
                    }
                  >
                    Попросить доработать
                  </button>
                </div>
              )}
              {soul.can_change && ["invited", "working"].includes(c.status) && (
                <button type="button" className="link" disabled={busy} onClick={() => void act(() => api(`/designer-contracts/${c.id}/cancel`, { method: "POST" }))}>
                  Отказаться от дизайнера
                </button>
              )}
            </div>
          )}
          {soul.is_designer && c && c.status === "working" && <ConceptEditor contract={c} rooms={rooms} onSaved={load} />}
          {choosing && (
            <>
              <p>Выберите дизайнера. Он посмотрит проект и назовёт цену и срок, до этого вы ничего не платите.</p>
              {designers === null ? null : designers.length === 0 ? (
                <p className="muted">Скоро здесь появятся дизайнеры. Мы сообщим.</p>
              ) : (
                <ul className="list">
                  {designers.map((d) => (
                    <li key={d.user_id}>
                      <DesignerCard
                        d={d}
                        styles={styles}
                        action={
                          <button
                            type="button"
                            className="secondary"
                            disabled={busy}
                            onClick={() => void act(() => api(`/objects/${objectId}/designer`, { method: "POST", body: { designer_id: d.user_id } }))}
                          >
                            Пригласить
                          </button>
                        }
                      />
                    </li>
                  ))}
                </ul>
              )}
              {soul.can_change && !c?.status.match(/working|submitted/) && (
                <button type="button" className="link" disabled={busy} onClick={() => void act(() => api(`/objects/${objectId}/soul`, { method: "PUT", body: { soul: false } }))}>
                  Обойдусь без дизайнера
                </button>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}

function ConceptEditor({ contract, rooms, onSaved }: { contract: Contract; rooms: { key: string; name: string }[]; onSaved: () => Promise<void> }) {
  const [concept, setConcept] = useState(contract.concept ?? "");
  const [notes, setNotes] = useState<Record<string, string>>(contract.room_notes);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  async function save(submit: boolean) {
    setError(null);
    try {
      await api(`/designer-contracts/${contract.id}/concept`, { method: "PUT", body: { concept, room_notes: notes } });
      if (submit) await api(`/designer-contracts/${contract.id}/submit`, { method: "POST" });
      setSaved(true);
      await onSaved();
    } catch (err) {
      setError(errorText(err));
    }
  }
  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void save(false);
  }
  return (
    <form className="boxed" onSubmit={onSubmit}>
      <h3>Ваша концепция</h3>
      <p className="muted small">Отделку меняйте прямо в комнатах ниже, предметы добавляйте в «Наполнение».</p>
      <ErrorNote text={error} />
      <label htmlFor="cc">Идея интерьера</label>
      <textarea id="cc" rows={4} maxLength={10000} value={concept} onChange={(e) => { setConcept(e.target.value); setSaved(false); }} />
      {rooms.map((r) => (
        <div key={r.key}>
          <label htmlFor={`rn-${r.key}`}>{r.name}</label>
          <textarea
            id={`rn-${r.key}`}
            rows={2}
            maxLength={3000}
            value={notes[r.key] ?? ""}
            onChange={(e) => {
              setNotes({ ...notes, [r.key]: e.target.value });
              setSaved(false);
            }}
          />
        </div>
      ))}
      <p className="row">
        <button type="submit" className="secondary">
          {saved ? "Сохранено ✓" : "Сохранить"}
        </button>
        <button type="button" disabled={!concept.trim()} onClick={() => void save(true)}>
          Сдать проект заказчику
        </button>
      </p>
    </form>
  );
}

/** Наполнение комнаты предметами: от дизайнера и от заказчика. */
export function RoomDecor({
  objectId,
  roomKey,
  items,
  canEdit,
  note,
  onChanged,
}: {
  objectId: string;
  roomKey: string;
  items: Decor[];
  canEdit: boolean;
  note?: string;
  onChanged: (next: Decor[]) => void;
}) {
  const [title, setTitle] = useState("");
  const [link, setLink] = useState("");
  const [price, setPrice] = useState("");
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function add(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      onChanged(
        await api<Decor[]>(`/objects/${objectId}/design/decor`, {
          method: "POST",
          body: { room_key: roomKey, title, link: link.trim() || null, price_kop: price ? parseRub(price) : null },
        }),
      );
      setTitle("");
      setLink("");
      setPrice("");
      setOpen(false);
    } catch (err) {
      setError(errorText(err));
    }
  }
  async function remove(id: string) {
    try {
      onChanged(await api<Decor[]>(`/objects/${objectId}/design/decor/${id}`, { method: "DELETE" }));
    } catch (err) {
      setError(errorText(err));
    }
  }
  if (!items.length && !canEdit && !note) return null;
  return (
    <div className="decor">
      {note && <p className="designer-note">«{note}»</p>}
      <b className="small">Наполнение</b>
      <ErrorNote text={error} />
      {items.length > 0 && (
        <ul className="list">
          {items.map((d) => (
            <li key={d.id} className="row between small">
              <span>
                {d.link ? (
                  <a href={d.link} target="_blank" rel="noreferrer nofollow">
                    {d.title}
                  </a>
                ) : (
                  d.title
                )}
                {d.qty > 1 && ` × ${d.qty}`}
                {d.by_designer && <span className="badge soul-badge">дизайнер</span>}
                {d.price_kop && <span className="muted"> · ≈ {rub(d.price_kop)}</span>}
              </span>
              {canEdit && (
                <button type="button" className="link" onClick={() => void remove(d.id)}>
                  Убрать
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {canEdit &&
        (open ? (
          <form onSubmit={add} className="decor-form">
            <input aria-label="Предмет" placeholder="Например: торшер из латуни" value={title} minLength={2} required onChange={(e) => setTitle(e.target.value)} />
            <input aria-label="Ссылка" type="url" placeholder="Ссылка (необязательно)" value={link} onChange={(e) => setLink(e.target.value)} />
            <input aria-label="Цена, ₽" inputMode="decimal" placeholder="Цена, ₽" value={price} onChange={(e) => setPrice(e.target.value)} />
            <button type="submit">Добавить</button>
          </form>
        ) : (
          <button type="button" className="link small" onClick={() => setOpen(true)}>
            + предмет
          </button>
        ))}
    </div>
  );
}

// ---------- куратор ----------

type Person = { id: string; name: string | null; phone: string };
type Signal = { id: string; code: string; severity: "info" | "warn" | "alert"; text: string; by_human: boolean; created_at: string; resolved_at: string | null };
type CuratorState = {
  kind: "ai" | "human";
  curator: Person | null;
  is_curator: boolean;
  can_choose: boolean;
  can_assign: boolean;
  signals: Signal[];
  notes: Signal[];
};

const SEVERITY: Record<string, string> = { alert: "Важно", warn: "Внимание", info: "К сведению" };

/** Куратор проекта на странице объекта: ИИ или человек, сигналы и заметки. */
export function CuratorSection({ obj }: { obj: RepairObject }) {
  const [state, setState] = useState<CuratorState | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [showDone, setShowDone] = useState(false);

  useEffect(() => {
    void api<CuratorState>(`/objects/${obj.id}/curator`)
      .then((s) => {
        setState(s);
        if (s.can_assign) void api<Person[]>("/curator/candidates").then(setPeople).catch(() => undefined);
      })
      .catch((err) => setError(errorText(err)));
  }, [obj.id]);

  async function act(fn: () => Promise<CuratorState>) {
    setError(null);
    try {
      setState(await fn());
    } catch (err) {
      setError(errorText(err));
    }
  }
  if (!state) return error ? <ErrorNote text={error} /> : null;
  const open = state.signals.filter((s) => !s.resolved_at);
  const done = state.signals.filter((s) => s.resolved_at);
  const canHandle = state.is_curator || state.can_assign;
  return (
    <section className="card" id="curator">
      <h2>Куратор проекта</h2>
      <ErrorNote text={error} />
      {state.can_choose && (
        <div className="chips" role="radiogroup" aria-label="Кто куратор">
          <button type="button" role="radio" className="chip" aria-checked={state.kind === "ai"} aria-pressed={state.kind === "ai"} onClick={() => void act(() => api(`/objects/${obj.id}/curator`, { method: "PUT", body: { kind: "ai" } }))}>
            ИИ-куратор
          </button>
          <button type="button" role="radio" className="chip" aria-checked={state.kind === "human"} aria-pressed={state.kind === "human"} onClick={() => void act(() => api(`/objects/${obj.id}/curator`, { method: "PUT", body: { kind: "human", user_id: state.curator?.id ?? null } }))}>
            Человек
          </button>
        </div>
      )}
      <p className="small">
        {state.kind === "ai"
          ? "Каждый час проверяет фотоотчёты, сроки этапов, приёмку, замечания технадзора и споры, и сразу подсказывает, если что-то идёт не так."
          : state.curator
            ? `Куратор: ${state.curator.name ? `${state.curator.name}, ` : ""}${formatPhone(state.curator.phone)}. Видит все сигналы ИИ и держит ремонт под контролем.`
            : "Назначаем куратора, он позвонит в течение дня. Пока следит ИИ-куратор."}
      </p>
      {state.can_assign && state.kind === "human" && (
        <label className="field-title">
          Куратор-человек
          <select
            value={state.curator?.id ?? ""}
            onChange={(e) => void act(() => api(`/objects/${obj.id}/curator`, { method: "PUT", body: { kind: "human", user_id: e.target.value || null } }))}
          >
            <option value="">Не назначен</option>
            {people.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name ? `${p.name}, ${formatPhone(p.phone)}` : formatPhone(p.phone)}
              </option>
            ))}
          </select>
        </label>
      )}
      {open.length === 0 ? (
        <p className="muted small">Сейчас всё идёт по плану.</p>
      ) : (
        <ul className="list signals">
          {open.map((s) => (
            <li key={s.id} className={`signal signal-${s.severity}`}>
              <span className="grow">
                <b className="small">{SEVERITY[s.severity]}</b> {s.text}
              </span>
              {canHandle && (
                <button type="button" className="link small" onClick={() => void act(() => api(`/curator-signals/${s.id}/resolve`, { method: "POST" }))}>
                  Разобрались
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {done.length > 0 && (
        <button type="button" className="link small" aria-expanded={showDone} onClick={() => setShowDone(!showDone)}>
          {showDone ? "Скрыть решённое" : `Решённое (${done.length})`}
        </button>
      )}
      {showDone && (
        <ul className="list muted small">
          {done.map((s) => (
            <li key={s.id}>{s.text}</li>
          ))}
        </ul>
      )}
      {state.notes.length > 0 && (
        <>
          <h3>Заметки куратора</h3>
          <ul className="list">
            {state.notes.map((n) => (
              <li key={n.id}>
                <span className="muted small">{formatDate(n.created_at)}</span>
                <p className="pre">{n.text}</p>
              </li>
            ))}
          </ul>
        </>
      )}
      {canHandle && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void act(() => api(`/objects/${obj.id}/curator/notes`, { method: "POST", body: { text: note } })).then(() => setNote(""));
          }}
        >
          <label htmlFor="cur-note">Заметка заказчику</label>
          <textarea id="cur-note" rows={2} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />
          <button type="submit" disabled={note.trim().length < 2}>
            Отправить
          </button>
        </form>
      )}
      {(state.can_choose || canHandle) && (
        <button type="button" className="link small" onClick={() => void act(() => api(`/objects/${obj.id}/curator/check`, { method: "POST" }))}>
          Проверить сейчас
        </button>
      )}
    </section>
  );
}

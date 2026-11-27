"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent, type ReactElement } from "react";

import { api, errorText } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import {
  formatDate,
  FRAUD_FLAG_LABELS,
  INSPECTION_RESULT_LABELS,
  personName,
  ACT_STATUS_LABELS,
  DISPUTE_OUTCOME_LABELS,
  DISPUTE_STATUS_LABELS,
  PAYMENT_STATUS_LABELS,
  REVIEW_LABELS,
  rub,
  STAGE_STATUS_LABELS,
  STAGE_TYPE_LABELS,
} from "@/lib/format";
import type { Act, Dispute, Inspection, MediaItem, Me, Member, Page, Payment, Stage, StageChecklist, TechCard } from "@/lib/types";

import { ErrorNote, Loading, Shell } from "../../components";
import { TechCardView } from "../../techcard";

type StageDetail = {
  stage: Stage;
  object_id: string;
  object_status: string;
  address: string;
  can_work: boolean;
  can_review: boolean;
  is_admin: boolean;
  is_customer: boolean;
  can_dispute: boolean;
};

export default function StagePage() {
  const { id } = useParams<{ id: string }>();
  const { me, loading } = useRequireAuth();
  const [detail, setDetail] = useState<StageDetail | null>(null);
  const [checklist, setChecklist] = useState<StageChecklist | null>(null);
  const [inspections, setInspections] = useState<Inspection[]>([]);
  const [card, setCard] = useState<TechCard | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [d, c, i, t] = await Promise.all([
        api<StageDetail>(`/stages/${id}`),
        api<StageChecklist>(`/stages/${id}/checklist`),
        api<Inspection[]>(`/stages/${id}/inspections`),
        api<TechCard | null>(`/stages/${id}/tech-card`),
      ]);
      setDetail(d);
      setChecklist(c);
      setInspections(i);
      setCard(t);
    } catch (err) {
      setError(errorText(err));
    }
  }, [id]);
  useEffect(() => {
    if (me) void load();
  }, [me, load]);

  if (loading || !me) return <Shell><Loading /></Shell>;
  if (!detail || !checklist) return <Shell>{error ? <ErrorNote text={error} /> : <Loading />}</Shell>;
  const { stage } = detail;

  return (
    <Shell wide>
      <p>
        <Link href={`/objects/${detail.object_id}`}>← {detail.address}</Link>
      </p>
      <h1>
        {stage.seq}. {stage.title}
      </h1>
      <p>
        <span className={`status status-${stage.status}`}>{STAGE_STATUS_LABELS[stage.status] ?? stage.status}</span>
        {stage.is_stop_point && <span className="badge">проверка технадзора</span>}
        {stage.stage_type && <span className="muted small"> · {STAGE_TYPE_LABELS[stage.stage_type] ?? stage.stage_type}</span>}
      </p>
      <ErrorNote text={error} />
      <div className="split">
        <div>
          <Disputes detail={detail} onChanged={load} />
          <Money detail={detail} onChanged={load} />
          <Acts detail={detail} onChanged={load} />
          <Actions detail={detail} checklist={checklist} onChanged={load} />
          <Photos detail={detail} onChanged={load} />
        </div>
        <div>
          <Checklist checklist={checklist} />
          <Inspections detail={detail} inspections={inspections} me={me} card={card} onChanged={load} />
          {card && <TechCardView card={card} />}
        </div>
      </div>
    </Shell>
  );
}

function Actions({ detail, checklist, onChanged }: { detail: StageDetail; checklist: StageChecklist; onChanged: () => Promise<void> }) {
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const { stage } = detail;

  async function run(path: string, done: (data: unknown) => string | null) {
    setError(null);
    setNote(null);
    try {
      const data = await api<unknown>(`/stages/${stage.id}/${path}`, { method: "POST" });
      setNote(done(data));
      await onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  }

  const buttons: ReactElement[] = [];
  if (detail.can_work && stage.status === "ready") {
    buttons.push(
      <button key="start" className="primary" onClick={() => void run("start", () => "Работы начаты.")}>
        Начать работы
      </button>,
    );
  }
  if (detail.can_work && stage.status === "in_progress") {
    buttons.push(
      <button
        key="submit"
        className="primary"
        onClick={() =>
          void run("submit", (d) => {
            const missing = (d as { missing: string[] }).missing;
            return missing.length ? `Этап сдан. Без фото: ${missing.join(", ")}.` : "Этап сдан.";
          })
        }
      >
        {stage.is_stop_point ? "Сдать на проверку технадзору" : "Сдать заказчику на приёмку"}
      </button>,
    );
  }
  if (buttons.length === 0 && !note && !error) return null;
  const missing = checklist.items.filter((i) => checklist.missing.includes(i.id));
  return (
    <section className="card">
      <h2>Что дальше</h2>
      {stage.status === "in_progress" && missing.length > 0 && (
        <p className="devnote">Нет фото по пунктам чек-листа: {missing.map((m) => m.title).join(", ")}. Сдать этап можно, но проверяющий это увидит.</p>
      )}
      <div className="row">{buttons}</div>
      {note && (
        <p className="ok" role="status">
          {note}
        </p>
      )}
      <ErrorNote text={error} />
    </section>
  );
}

function Checklist({ checklist }: { checklist: StageChecklist }) {
  return (
    <section className="card">
      <h2>Чек-лист фото</h2>
      {checklist.items.length === 0 ? (
        <p className="muted">Для этого вида работ чек-листа нет. Прораб снимает фото этапа без пунктов.</p>
      ) : (
        <ul className="checklist">
          {checklist.items.map((i) => {
            const done = i.photos > 0;
            return (
              <li key={i.id}>
                <span className={done ? "mark ok" : i.photo_required ? "mark no" : "mark"} aria-hidden="true">
                  {done ? "✓" : i.photo_required ? "!" : "·"}
                </span>
                <div>
                  <b>{i.title}</b>
                  {i.required_angle && <div className="muted small">{i.required_angle}</div>}
                  <div className="muted small">
                    {done ? `Фото: ${i.photos}` : i.photo_required ? "Нет фото" : "Фото по желанию"}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function Photos({ detail, onChanged }: { detail: StageDetail; onChanged: () => Promise<void> }) {
  const [items, setItems] = useState<MediaItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [comment, setComment] = useState("");

  const load = useCallback(async () => {
    try {
      setItems((await api<Page<MediaItem>>(`/objects/${detail.object_id}/feed?stage_id=${detail.stage.id}&limit=100`)).items);
    } catch (err) {
      setError(errorText(err));
    }
  }, [detail.object_id, detail.stage.id]);
  useEffect(() => void load(), [load]);

  async function review(id: string, status: "approved" | "rejected", text?: string) {
    setError(null);
    try {
      await api(`/media/${id}/review`, { method: "POST", body: { status, comment: text || null } });
      setRejecting(null);
      setComment("");
      await Promise.all([load(), onChanged()]);
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <section className="card">
      <h2>Фото этапа</h2>
      {items === null ? (
        error ? null : <Loading />
      ) : items.length === 0 ? (
        <p className="muted">Фото пока нет. Прораб снимает их в приложении бригады.</p>
      ) : (
        <ul className="photos">
          {items.map((m) => (
            <li key={m.id}>
              <figure>
                <a href={m.url} target="_blank" rel="noreferrer">
                  <img src={m.url} alt={`${m.checklist_item_title ?? m.stage_title ?? "Фото"}, ${formatDate(m.received_server_at)}`} loading="lazy" />
                </a>
                <figcaption>
                  {m.checklist_item_title ?? "Без пункта"} · {formatDate(m.received_server_at)}
                  {m.fraud_flags &&
                    Object.keys(m.fraud_flags).map((f) => (
                      <span className="flag" key={f}>
                        {FRAUD_FLAG_LABELS[f] ?? f}
                      </span>
                    ))}
                  {m.review_status !== "pending" && (
                    <div className={m.review_status === "rejected" ? "error-text" : "ok"}>
                      {REVIEW_LABELS[m.review_status]}
                      {m.review_comment && `: ${m.review_comment}`}
                    </div>
                  )}
                  {detail.can_review && m.review_status === "pending" && rejecting !== m.id && (
                    <div className="row">
                      <button className="secondary small" onClick={() => void review(m.id, "approved")}>
                        Принять
                      </button>
                      <button className="secondary small" onClick={() => setRejecting(m.id)}>
                        Отклонить
                      </button>
                    </div>
                  )}
                  {rejecting === m.id && (
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        void review(m.id, "rejected", comment);
                      }}
                    >
                      <label htmlFor={`why-${m.id}`}>Что не так</label>
                      <input id={`why-${m.id}`} value={comment} onChange={(e) => setComment(e.target.value)} autoFocus />
                      <div className="row">
                        <button type="submit" className="danger small" disabled={!comment.trim()}>
                          Отклонить фото
                        </button>
                        <button type="button" className="secondary small" onClick={() => setRejecting(null)}>
                          Отмена
                        </button>
                      </div>
                    </form>
                  )}
                </figcaption>
              </figure>
            </li>
          ))}
        </ul>
      )}
      <ErrorNote text={error} />
    </section>
  );
}

function Inspections({
  detail,
  inspections,
  me,
  card,
  onChanged,
}: {
  detail: StageDetail;
  inspections: Inspection[];
  me: Me;
  card: TechCard | null;
  onChanged: () => Promise<void>;
}) {
  const points = card?.steps.filter((s) => s.inspector) ?? [];
  const [checks, setChecks] = useState<Record<string, { ok: boolean | null; note: string }>>({});
  const [inspectors, setInspectors] = useState<Member[]>([]);
  const [inspector, setInspector] = useState("");
  const [slot, setSlot] = useState("");
  const [result, setResult] = useState<"passed" | "remarks" | "failed">("passed");
  const [remarks, setRemarks] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { stage } = detail;

  useEffect(() => {
    const pid = me.memberships.find((m) => m.org_type === "platform")?.org_id;
    if (detail.is_admin && pid)
      void api<Member[]>(`/organizations/${pid}/members`).then((m) => setInspectors(m.filter((x) => x.role === "inspector")));
  }, [detail.is_admin, me]);

  if (!stage.is_stop_point) return null;
  const open = inspections.find((i) => i.result === null);

  async function schedule(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api(`/stages/${stage.id}/inspections`, {
        method: "POST",
        body: { inspector_id: inspector, scheduled_at: slot ? new Date(slot).toISOString() : null },
      });
      await onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function sign(e: FormEvent) {
    e.preventDefault();
    if (!open) return;
    setError(null);
    try {
      const marked = points
        .filter((p) => checks[p.key]?.ok != null)
        .map((p) => ({ key: p.key, ok: checks[p.key].ok, note: checks[p.key].note || null }));
      await api(`/inspections/${open.id}/result`, { method: "POST", body: { result, remarks: remarks || null, checks: marked } });
      setRemarks("");
      setChecks({});
      await onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  }

  const canSign = open && open.inspector_id === me.id && stage.status === "inspection";
  return (
    <section className="card">
      <h2>Проверка технадзора</h2>
      {inspections.length === 0 && <p className="muted">Выезд инженера ещё не назначен.</p>}
      <ul className="list">
        {inspections.map((i) => (
          <li key={i.id}>
            <div>
              <b>{i.result ? INSPECTION_RESULT_LABELS[i.result] : "Выезд назначен"}</b>
              <div className="muted">
                {i.inspector_name}
                {i.scheduled_at && ` · ${formatDate(i.scheduled_at)}`}
                {i.signed_at && ` · подписано ${formatDate(i.signed_at)}`}
              </div>
              {i.remarks && <div>{i.remarks}</div>}
              {i.checks && i.checks.length > 0 && (
                <ul className="small">
                  {i.checks.map((c) => (
                    <li key={c.key} className={c.ok ? undefined : "error-text"}>
                      {c.ok ? "✓" : "✗"} {c.title}
                      {c.note && ` — ${c.note}`}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </li>
        ))}
      </ul>
      {detail.is_admin && !open && !["accepted", "paid"].includes(stage.status) && (
        <form onSubmit={schedule} className="boxed">
          <label htmlFor="insp">Инженер</label>
          <select id="insp" value={inspector} onChange={(e) => setInspector(e.target.value)}>
            <option value="">Выберите инженера</option>
            {inspectors.map((m) => (
              <option key={m.user_id} value={m.user_id}>
                {personName(m)}
              </option>
            ))}
          </select>
          <label htmlFor="insp-slot">Дата и время выезда</label>
          <input id="insp-slot" type="datetime-local" value={slot} onChange={(e) => setSlot(e.target.value)} />
          <button type="submit" className="secondary" disabled={!inspector}>
            Назначить выезд
          </button>
        </form>
      )}
      {open && open.inspector_id === me.id && stage.status !== "inspection" && (
        <p className="muted">Результат можно внести, когда прораб сдаст этап на проверку.</p>
      )}
      {canSign && (
        <form onSubmit={sign} className="boxed">
          {points.length > 0 && (
            <fieldset>
              <legend>Пункты техкарты</legend>
              {points.map((p) => {
                const c = checks[p.key] ?? { ok: null, note: "" };
                const set = (patch: Partial<typeof c>) => setChecks((all) => ({ ...all, [p.key]: { ...c, ...patch } }));
                return (
                  <div key={p.key} className="check-row">
                    <div>
                      <b>{p.title}</b>
                      {p.tolerance && <div className="muted small">Допуск: {p.tolerance}</div>}
                    </div>
                    <div className="row">
                      <label className="check">
                        <input type="radio" name={`c-${p.key}`} checked={c.ok === true} onChange={() => set({ ok: true })} />
                        <span>В норме</span>
                      </label>
                      <label className="check">
                        <input type="radio" name={`c-${p.key}`} checked={c.ok === false} onChange={() => set({ ok: false })} />
                        <span>Замечание</span>
                      </label>
                    </div>
                    {c.ok === false && (
                      <input
                        aria-label={`Замечание: ${p.title}`}
                        placeholder="Что не так"
                        value={c.note}
                        onChange={(e) => set({ note: e.target.value })}
                      />
                    )}
                  </div>
                );
              })}
            </fieldset>
          )}
          <fieldset>
            <legend>Результат проверки</legend>
            {(["passed", "remarks", "failed"] as const).map((r) => (
              <label key={r} className="check">
                <input type="radio" name="result" checked={result === r} onChange={() => setResult(r)} />
                <span>{INSPECTION_RESULT_LABELS[r]}</span>
              </label>
            ))}
          </fieldset>
          <label htmlFor="remarks">{result === "passed" ? "Комментарий (необязательно)" : "Что нужно исправить"}</label>
          <textarea id="remarks" rows={3} value={remarks} onChange={(e) => setRemarks(e.target.value)} />
          <button type="submit" className="primary" disabled={result !== "passed" && !remarks.trim()}>
            Подписать результат
          </button>
        </form>
      )}
      <ErrorNote text={error} />
    </section>
  );
}

function Money({ detail, onChanged }: { detail: StageDetail; onChanged: () => Promise<void> }) {
  const [payments, setPayments] = useState<Payment[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { stage } = detail;

  const load = useCallback(async () => {
    try {
      setPayments(await api<Payment[]>(`/stages/${stage.id}/payments`));
    } catch (err) {
      setError(errorText(err));
    }
  }, [stage.id]);
  useEffect(() => void load(), [load, stage.status]);

  async function hold() {
    setError(null);
    try {
      const p = await api<Payment>(`/stages/${stage.id}/payments/hold`, { method: "POST" });
      if (p.pay_url) location.assign(p.pay_url);
      else await Promise.all([load(), onChanged()]);
    } catch (err) {
      setError(errorText(err));
    }
  }

  if (payments === null) return null;
  const current = payments.find((p) => p.status !== "failed" && p.status !== "refunded");
  const total = stage.work_budget_kop + stage.material_budget_kop;
  const canHold = detail.is_customer && ["planned", "funding"].includes(stage.status) && total > 0 && ["contracting", "active"].includes(detail.object_status);
  return (
    <section className="card">
      <h2>Деньги этапа</h2>
      {current ? (
        <>
          <p>
            <span className={`status status-${current.status === "held" || current.status === "released" ? "approved" : "submitted"}`}>
              {PAYMENT_STATUS_LABELS[current.status]}
            </span>{" "}
            <b>{rub(current.amount_kop)}</b>
          </p>
          <dl className="facts">
            <dt>Работы</dt>
            <dd>{rub(current.breakdown.work_kop ?? 0)}</dd>
            <dt>Материалы</dt>
            <dd>{rub(current.breakdown.material_kop ?? 0)}</dd>
            {Boolean(current.breakdown.platform_fee_kop) && (
              <>
                <dt>Комиссия платформы</dt>
                <dd>{rub(current.breakdown.platform_fee_kop ?? 0)}</dd>
              </>
            )}
          </dl>
          {current.status === "held" && (
            <p className="muted">Деньги лежат в банке и уйдут подрядчику только после того, как вы подпишете акт этапа.</p>
          )}
          {current.status === "pending_funding" && detail.is_customer && current.pay_url && (
            <a className="button primary" href={current.pay_url}>
              Перейти к оплате
            </a>
          )}
        </>
      ) : canHold ? (
        <>
          <p>
            Чтобы бригада начала этап, зарезервируйте <b>{rub(total)}</b>. Деньги остаются в банке и уходят подрядчику только после
            вашей подписи акта.
          </p>
          <button className="primary" onClick={() => void hold()}>
            Зарезервировать {rub(total)}
          </button>
        </>
      ) : (
        <p className="muted">
          {total > 0 ? "Заказчик ещё не зарезервировал деньги на этап." : "Сумма этапа появится после утверждения полной сметы."}
        </p>
      )}
      {payments.some((p) => p.status === "failed") && !current && <p className="muted">Прошлая оплата не прошла, можно попробовать ещё раз.</p>}
      <ErrorNote text={error} />
    </section>
  );
}

function Acts({ detail, onChanged }: { detail: StageDetail; onChanged: () => Promise<void> }) {
  const [acts, setActs] = useState<Act[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [signing, setSigning] = useState<{ actId: string; signatureId: string; debug: string | null } | null>(null);
  const [code, setCode] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const { stage } = detail;

  const load = useCallback(async () => {
    try {
      setActs(await api<Act[]>(`/stages/${stage.id}/acts`));
    } catch (err) {
      setError(errorText(err));
    }
  }, [stage.id]);
  useEffect(() => void load(), [load, stage.status]);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      await Promise.all([load(), onChanged()]);
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function requestCode(a: Act) {
    setError(null);
    try {
      const r = await api<{ signature_id: string; debug_code: string | null }>(`/acts/${a.id}/sign/request`, { method: "POST" });
      setSigning({ actId: a.id, signatureId: r.signature_id, debug: r.debug_code });
      setCode("");
    } catch (err) {
      setError(errorText(err));
    }
  }

  if (acts === null) return null;
  const open = acts.find((a) => !["rejected"].includes(a.status));
  const canCreate = detail.can_work && !detail.is_customer && stage.status === "acceptance" && !open;
  if (acts.length === 0 && !canCreate) return null;
  return (
    <section className="card">
      <h2>Акт сдачи-приёмки</h2>
      {acts.map((a) => (
        <div key={a.id} className="boxed">
          <p>
            <b>Акт № {a.number}</b> · {rub(a.work_kop + a.material_kop)}{" "}
            <span className={`status status-${a.status === "released" ? "approved" : a.status === "rejected" ? "cancelled" : "submitted"}`}>
              {ACT_STATUS_LABELS[a.status]}
            </span>
          </p>
          <div className="muted small">
            {a.contractor_signed_at && <div>Подрядчик подписал {formatDate(a.contractor_signed_at)}</div>}
            {a.customer_signed_at && <div>Заказчик подписал {formatDate(a.customer_signed_at)}</div>}
            {a.reject_reason && <div>Причина отказа: {a.reject_reason}</div>}
          </div>
          {a.can_sign && signing?.actId !== a.id && (
            <div className="row">
              <button className="primary" onClick={() => void requestCode(a)}>
                {detail.is_customer ? "Принять работы и подписать" : "Подписать акт"}
              </button>
              {a.can_reject && (
                <button className="secondary" onClick={() => setRejecting(true)}>
                  Отклонить акт
                </button>
              )}
            </div>
          )}
          {signing?.actId === a.id && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void act(async () => {
                  await api(`/acts/${a.id}/sign/confirm`, { method: "POST", body: { signature_id: signing.signatureId, code } });
                  setSigning(null);
                });
              }}
            >
              {detail.is_customer && (
                <p className="muted">После подписи деньги этапа уйдут подрядчику. Подписывайте, только если работы приняты.</p>
              )}
              <label htmlFor="act-code">Код из СМС</label>
              <input id="act-code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
              {signing.debug && (
                <p className="devnote">
                  Режим разработки: код <b>{signing.debug}</b>
                </p>
              )}
              <button type="submit" className="primary" disabled={code.length !== 6}>
                Подписать кодом
              </button>
            </form>
          )}
          {rejecting && a.can_reject && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void act(async () => {
                  await api(`/acts/${a.id}/reject`, { method: "POST", body: { reason } });
                  setRejecting(false);
                  setReason("");
                });
              }}
            >
              <label htmlFor="act-reason">Что не так с работами</label>
              <textarea id="act-reason" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />
              <div className="row">
                <button type="submit" className="danger" disabled={reason.trim().length < 3}>
                  Отклонить акт и вернуть в работу
                </button>
                <button type="button" className="secondary" onClick={() => setRejecting(false)}>
                  Отмена
                </button>
              </div>
            </form>
          )}
        </div>
      ))}
      {canCreate && (
        <>
          <p className="muted">Этап сдан. Сформируйте акт: его подписываете вы, затем заказчик, и деньги этапа приходят вам.</p>
          <button className="primary" onClick={() => void act(() => api(`/stages/${stage.id}/acts`, { method: "POST" }))}>
            Сформировать акт
          </button>
        </>
      )}
      <ErrorNote text={error} />
    </section>
  );
}

const DISPUTABLE = ["funding", "ready", "in_progress", "inspection", "acceptance", "accepted"];

function Disputes({ detail, onChanged }: { detail: StageDetail; onChanged: () => Promise<void> }) {
  const [items, setItems] = useState<Dispute[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [opening, setOpening] = useState(false);
  const [reason, setReason] = useState("");
  const [outcome, setOutcome] = useState<"continue" | "refund">("continue");
  const [resolution, setResolution] = useState("");
  const [busy, setBusy] = useState(false);
  const { stage } = detail;

  const load = useCallback(async () => {
    try {
      const all = await api<Dispute[]>(`/objects/${detail.object_id}/disputes`);
      setItems(all.filter((d) => d.stage_id === stage.id));
    } catch (err) {
      setError(errorText(err));
    }
  }, [detail.object_id, stage.id]);
  useEffect(() => void load(), [load, stage.status]);

  async function run(path: string, body: unknown) {
    setBusy(true);
    setError(null);
    try {
      await api<Dispute>(path, { method: "POST", body });
      setOpening(false);
      setReason("");
      setResolution("");
      await Promise.all([load(), onChanged()]);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  if (items === null) return null;
  const open = items.find((d) => d.status === "open" || d.status === "in_review");
  const canOpen = detail.can_dispute && !open && DISPUTABLE.includes(stage.status);
  if (!open && items.length === 0 && !canOpen) return null;

  return (
    <section className="card">
      <h2>Спор по этапу</h2>
      {items.map((d) => {
        const active = d.status === "open" || d.status === "in_review";
        return (
          <div key={d.id} className={active ? "dispute" : "dispute closed"}>
            <p>
              <span className={`status ${active ? "status-frozen" : "status-approved"}`}>{DISPUTE_STATUS_LABELS[d.status]}</span>{" "}
              <span className="muted small">
                {d.opened_by_name}, {formatDate(d.created_at)}
              </span>
            </p>
            <p>{d.reason}</p>
            {active && (
              <p className="muted small">
                Работы и выплата по этапу на паузе{d.payment_status === "frozen" ? ", деньги заморожены в банке" : ""}. Спор разбирает
                администратор платформы.
              </p>
            )}
            {d.outcome && (
              <p>
                <b>{DISPUTE_OUTCOME_LABELS[d.outcome]}.</b> {d.resolution}
              </p>
            )}
            {active && detail.is_admin && (
              <form
                className="stack"
                onSubmit={(e) => {
                  e.preventDefault();
                  void run(`/disputes/${d.id}/resolve`, { outcome, resolution });
                }}
              >
                {d.status === "open" && (
                  <button type="button" className="secondary" disabled={busy} onClick={() => void run(`/disputes/${d.id}/review`, {})}>
                    Взять в работу
                  </button>
                )}
                <fieldset>
                  <legend>Решение</legend>
                  <div className="radio-row">
                    <label>
                      <input type="radio" name={`outcome-${d.id}`} checked={outcome === "continue"} onChange={() => setOutcome("continue")} />
                      Продолжить работы
                    </label>
                    <label>
                      <input type="radio" name={`outcome-${d.id}`} checked={outcome === "refund"} onChange={() => setOutcome("refund")} />
                      Вернуть деньги заказчику
                    </label>
                  </div>
                </fieldset>
                <label>
                  Обоснование для сторон
                  <textarea value={resolution} onChange={(e) => setResolution(e.target.value)} minLength={5} required />
                </label>
                <button className="primary" disabled={busy}>
                  {outcome === "refund" ? "Вернуть деньги и закрыть спор" : "Снять заморозку и закрыть спор"}
                </button>
              </form>
            )}
          </div>
        );
      })}
      {canOpen && !opening && (
        <>
          <p className="muted">Если что-то пошло не так и договориться не получается, откройте спор. Деньги этапа заморозятся до решения.</p>
          <button className="secondary" onClick={() => setOpening(true)}>
            Открыть спор
          </button>
        </>
      )}
      {opening && (
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            void run(`/objects/${detail.object_id}/disputes`, { stage_id: stage.id, reason });
          }}
        >
          <label>
            Что случилось
            <textarea value={reason} onChange={(e) => setReason(e.target.value)} minLength={10} required placeholder="Например: стяжка с перепадом 2 см, бригада отказывается переделать" />
          </label>
          <p className="muted small">Пока спор открыт, работы по этапу не принимаются и деньги никому не уходят.</p>
          <div className="row">
            <button className="primary" disabled={busy}>
              Открыть спор и заморозить деньги
            </button>
            <button type="button" className="secondary" onClick={() => setOpening(false)}>
              Отмена
            </button>
          </div>
        </form>
      )}
      <ErrorNote text={error} />
    </section>
  );
}

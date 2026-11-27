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
  REVIEW_LABELS,
  STAGE_STATUS_LABELS,
  STAGE_TYPE_LABELS,
} from "@/lib/format";
import type { Inspection, MediaItem, Me, Member, Page, Stage, StageChecklist } from "@/lib/types";

import { ErrorNote, Loading, Shell } from "../../components";

type StageDetail = {
  stage: Stage;
  object_id: string;
  object_status: string;
  address: string;
  can_work: boolean;
  can_review: boolean;
  is_admin: boolean;
  is_customer: boolean;
};

export default function StagePage() {
  const { id } = useParams<{ id: string }>();
  const { me, loading } = useRequireAuth();
  const [detail, setDetail] = useState<StageDetail | null>(null);
  const [checklist, setChecklist] = useState<StageChecklist | null>(null);
  const [inspections, setInspections] = useState<Inspection[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [d, c, i] = await Promise.all([
        api<StageDetail>(`/stages/${id}`),
        api<StageChecklist>(`/stages/${id}/checklist`),
        api<Inspection[]>(`/stages/${id}/inspections`),
      ]);
      setDetail(d);
      setChecklist(c);
      setInspections(i);
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
          <Actions detail={detail} checklist={checklist} onChanged={load} />
          <Photos detail={detail} onChanged={load} />
        </div>
        <div>
          <Checklist checklist={checklist} />
          <Inspections detail={detail} inspections={inspections} me={me} onChanged={load} />
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
  if (detail.is_admin && ["planned", "funding"].includes(stage.status)) {
    buttons.push(
      <button key="fund" className="secondary" onClick={() => void run("mark-funded", () => "Этап готов к работе.")}>
        Отметить резерв денег
      </button>,
    );
  }
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
      {detail.is_admin && ["planned", "funding"].includes(stage.status) && (
        <p className="muted">
          Пока банк не подключён, резерв денег отмечает администратор. Начать работы можно после резерва.
        </p>
      )}
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

function Inspections({ detail, inspections, me, onChanged }: { detail: StageDetail; inspections: Inspection[]; me: Me; onChanged: () => Promise<void> }) {
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
      await api(`/inspections/${open.id}/result`, { method: "POST", body: { result, remarks: remarks || null } });
      setRemarks("");
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

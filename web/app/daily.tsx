"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api, errorText } from "@/lib/api";
import type { DailyPlan, Stage } from "@/lib/types";

import { ErrorNote } from "./components";

const STATUS: Record<DailyPlan["status"], string> = {
  planned: "план",
  reported: "ждёт приёмки",
  accepted: "принят",
  returned: "вернули с замечанием",
};

/** Сегодняшняя дата по Москве в формате ГГГГ-ММ-ДД. */
export function mskToday(): string {
  return new Date(Date.now() + 3 * 3600 * 1000).toISOString().slice(0, 10);
}

function dayLabel(day: string): string {
  const today = mskToday();
  if (day === today) return "Сегодня";
  return new Date(`${day}T12:00:00Z`).toLocaleDateString("ru-RU", { weekday: "short", day: "numeric", month: "long" });
}

/**
 * Работы по дням: прораб пишет план на день и сдаёт его вечером,
 * заказчик или инженер технадзора принимает день или возвращает с замечанием.
 */
export function DailyPlans({
  objectId,
  stages,
  canPlan,
  online = true,
  nested = false,
}: {
  objectId: string;
  stages: Stage[];
  canPlan: boolean;
  online?: boolean;
  nested?: boolean;
}) {
  const [plans, setPlans] = useState<DailyPlan[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);

  const load = useCallback(async () => {
    try {
      setPlans(await api<DailyPlan[]>(`/objects/${objectId}/daily-plans?days=14`));
    } catch (err) {
      setError(errorText(err));
    }
  }, [objectId]);
  useEffect(() => {
    if (online) void load();
  }, [load, online]);

  if (!online || plans === null) return null;
  const today = mskToday();
  const todayPlan = plans.find((p) => p.day === today);
  const others = plans.filter((p) => p.day !== today);
  if (!canPlan && plans.length === 0) return null;

  async function done(p: DailyPlan) {
    setPlans((all) => all?.map((x) => (x.id === p.id ? p : x)) ?? null);
    setEditing(false);
  }

  const Wrap = nested ? "div" : "section";
  return (
    <Wrap className={nested ? undefined : "card"} id="day">
      {nested ? <h3>План на сегодня</h3> : <h2>Работы по дням</h2>}
      <ErrorNote text={error} />
      {todayPlan && !editing ? (
        <PlanCard plan={todayPlan} onChanged={done} onEdit={() => setEditing(true)} />
      ) : canPlan ? (
        <PlanForm
          objectId={objectId}
          stages={stages}
          plan={todayPlan}
          onSaved={async () => {
            setEditing(false);
            await load();
          }}
          onCancel={todayPlan ? () => setEditing(false) : undefined}
        />
      ) : (
        <p className="muted">На сегодня план работ ещё не написан.</p>
      )}
      {others.length > 0 && (
        <details>
          <summary>Прошлые и следующие дни: {others.length}</summary>
          {others.map((p) => (
            <PlanCard key={p.id} plan={p} onChanged={done} />
          ))}
        </details>
      )}
    </Wrap>
  );
}

function PlanForm({
  objectId,
  stages,
  plan,
  onSaved,
  onCancel,
}: {
  objectId: string;
  stages: Stage[];
  plan?: DailyPlan;
  onSaved: () => Promise<void>;
  onCancel?: () => void;
}) {
  const [rows, setRows] = useState(plan?.items.map((i) => ({ title: i.title, stage_id: i.stage_id ?? "" })) ?? [{ title: "", stage_id: "" }]);
  const [error, setError] = useState<string | null>(null);
  const active = stages.filter((s) => ["ready", "in_progress"].includes(s.status));

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const items = rows.filter((r) => r.title.trim()).map((r) => ({ title: r.title.trim(), stage_id: r.stage_id || null }));
    try {
      await api(`/objects/${objectId}/daily-plans/${mskToday()}`, { method: "PUT", body: { items } });
      await onSaved();
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <form onSubmit={save} className="boxed">
      <p className="muted small">Что бригада делает сегодня. Заказчик увидит план, вечером отметьте, что сделано.</p>
      {rows.map((r, n) => (
        <div key={n} className="grid2">
          <div>
            <label htmlFor={`dp-${n}`}>Работа {n + 1}</label>
            <input
              id={`dp-${n}`}
              value={r.title}
              maxLength={300}
              placeholder="Например: штукатурка стен в кухне"
              onChange={(e) => setRows(rows.map((x, i) => (i === n ? { ...x, title: e.target.value } : x)))}
            />
          </div>
          {active.length > 0 && (
            <div>
              <label htmlFor={`dps-${n}`}>Этап</label>
              <select id={`dps-${n}`} value={r.stage_id} onChange={(e) => setRows(rows.map((x, i) => (i === n ? { ...x, stage_id: e.target.value } : x)))}>
                <option value="">Без этапа</option>
                {active.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.seq}. {s.title}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
      ))}
      <div className="row">
        <button type="button" className="secondary small" onClick={() => setRows([...rows, { title: "", stage_id: "" }])} disabled={rows.length >= 30}>
          Добавить работу
        </button>
        <button type="submit" className="primary" disabled={!rows.some((r) => r.title.trim().length >= 2)}>
          Сохранить план на сегодня
        </button>
        {onCancel && (
          <button type="button" className="secondary" onClick={onCancel}>
            Отмена
          </button>
        )}
      </div>
      <ErrorNote text={error} />
    </form>
  );
}

function PlanCard({ plan, onChanged, onEdit }: { plan: DailyPlan; onChanged: (p: DailyPlan) => Promise<void>; onEdit?: () => void }) {
  const [done, setDone] = useState<Record<string, boolean>>(() => Object.fromEntries(plan.items.map((i) => [i.id, i.done])));
  const [note, setNote] = useState("");
  const [review, setReview] = useState("");
  const [error, setError] = useState<string | null>(null);
  const reportable = plan.can_edit && plan.day <= mskToday();

  async function run(path: string, body: unknown) {
    setError(null);
    try {
      await onChanged(await api<DailyPlan>(path, { method: "POST", body }));
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <div className="boxed">
      <div className="row between">
        <b>{dayLabel(plan.day)}</b>
        <span className={`status status-day-${plan.status}`}>{STATUS[plan.status]}</span>
      </div>
      <ul className="list">
        {plan.items.map((i) => (
          <li key={i.id}>
            {reportable ? (
              <label className="check">
                <input type="checkbox" checked={done[i.id]} onChange={(e) => setDone({ ...done, [i.id]: e.target.checked })} />
                <span>
                  {i.title}
                  {i.stage_title && <span className="muted small"> · {i.stage_title}</span>}
                </span>
              </label>
            ) : (
              <div>
                {plan.status === "planned" ? "•" : i.done ? "✓" : "✗"} {i.title}
                {i.stage_title && <span className="muted small"> · {i.stage_title}</span>}
                {i.note && <div className="muted small">{i.note}</div>}
              </div>
            )}
          </li>
        ))}
      </ul>
      <p className="muted small">Фото за день: {plan.photos}</p>
      {plan.report_note && <p>Бригада: {plan.report_note}</p>}
      {plan.review_note && <p className={plan.status === "returned" ? "error-text" : undefined}>Замечание: {plan.review_note}</p>}
      {reportable && (
        <>
          <label htmlFor={`rn-${plan.id}`}>Комментарий к дню (необязательно)</label>
          <input id={`rn-${plan.id}`} value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
          <div className="row">
            <button
              type="button"
              className="primary"
              onClick={() =>
                void run(`/daily-plans/${plan.id}/report`, {
                  items: plan.items.map((i) => ({ id: i.id, done: done[i.id] })),
                  note: note || null,
                })
              }
            >
              Сдать день
            </button>
            {onEdit && (
              <button type="button" className="secondary" onClick={onEdit}>
                Изменить план
              </button>
            )}
          </div>
        </>
      )}
      {plan.can_review && (
        <>
          <label htmlFor={`rv-${plan.id}`}>Замечание (если что-то не так)</label>
          <input id={`rv-${plan.id}`} value={review} maxLength={2000} onChange={(e) => setReview(e.target.value)} />
          <div className="row">
            <button type="button" className="primary" onClick={() => void run(`/daily-plans/${plan.id}/review`, { accepted: true, note: review || null })}>
              Принять день
            </button>
            <button
              type="button"
              className="secondary"
              disabled={!review.trim()}
              onClick={() => void run(`/daily-plans/${plan.id}/review`, { accepted: false, note: review })}
            >
              Вернуть с замечанием
            </button>
          </div>
        </>
      )}
      <ErrorNote text={error} />
    </div>
  );
}

"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { api, errorText } from "@/lib/api";
import { formatDate, parseRub, rub, STAGE_TYPE_LABELS } from "@/lib/format";
import type { ContractorCard, ContractorReview, MarketBid, MarketTask, RepairObject } from "@/lib/types";

import { ErrorNote } from "./components";

export const KIND_LABELS = { company: "Строительная компания", brigade: "Бригада или мастер" } as const;

export function Rating({ value, count }: { value: number | null; count: number }) {
  if (value === null) return <span className="muted small">Пока без оценок</span>;
  return (
    <span className="small" aria-label={`Оценка ${value} из 5`}>
      <span className="stars">{"★".repeat(Math.round(value))}{"☆".repeat(5 - Math.round(value))}</span> {value.toFixed(1)} · {count}{" "}
      {count === 1 ? "отзыв" : count < 5 ? "отзыва" : "отзывов"}
    </span>
  );
}

export function ContractorSummary({ c, link = true }: { c: ContractorCard; link?: boolean }) {
  return (
    <div className="contractor">
      {c.cover_url && (
        // eslint-disable-next-line @next/next/no-img-element
        <img className="contractor-cover" src={c.cover_url} alt="" loading="lazy" />
      )}
      <div className="grow">
        {link ? <Link href={`/contractors/${c.org_id}`}><b>{c.name}</b></Link> : <b>{c.name}</b>}
        <div className="muted small">
          {KIND_LABELS[c.kind]}
          {c.city && ` · ${c.city}`}
          {c.since_year && ` · с ${c.since_year} г.`}
          {c.team_size && ` · ${c.team_size} чел.`}
        </div>
        <Rating value={c.rating} count={c.reviews} />
        <div className="muted small">
          На платформе: завершено {c.objects_done}, в работе {c.objects_in_work}
        </div>
        {c.specializations.length > 0 && (
          <div className="muted small">{c.specializations.map((s) => STAGE_TYPE_LABELS[s] ?? s).join(", ")}</div>
        )}
      </div>
    </div>
  );
}

function WorkTypes({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="chips" role="group" aria-label="Виды работ">
      {Object.entries(STAGE_TYPE_LABELS).map(([k, label]) => (
        <button
          key={k}
          type="button"
          className="chip"
          aria-pressed={value.includes(k)}
          onClick={() => onChange(value.includes(k) ? value.filter((x) => x !== k) : [...value, k])}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

export { WorkTypes };

const OPEN = ["draft", "survey", "estimating", "contracting"];

/** Карточка объекта: как ищем подрядчика, заявка и отклики, оценка после ремонта. */
export function ObjectMarket({ obj, onChanged }: { obj: RepairObject; onChanged: () => Promise<void> }) {
  const owner = obj.access.is_customer || obj.access.is_admin;
  const [task, setTask] = useState<MarketTask | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const mode = obj.match_mode ?? "platform";

  const load = useCallback(async () => {
    try {
      setTask(await api<MarketTask | null>(`/objects/${obj.id}/market-task`));
    } catch (err) {
      setError(errorText(err));
    }
  }, [obj.id]);
  useEffect(() => void load(), [load, obj.contractor_org_id]);

  async function setMode(m: "platform" | "market") {
    setError(null);
    try {
      await api(`/objects/${obj.id}/match-mode`, { method: "PUT", body: { mode: m } });
      await onChanged();
      await load();
    } catch (err) {
      setError(errorText(err));
    }
  }

  if (task === undefined) return null;
  const choosing = owner && !obj.contractor_org_id && OPEN.includes(obj.status) && task?.status !== "awarded";
  const showReview = obj.access.is_customer && obj.status === "completed" && obj.contractor_org_id;
  if (!choosing && !task && !showReview) return null;

  return (
    <section className="card" id="market">
      <h2>Подрядчик</h2>
      <ErrorNote text={error} />
      {choosing && (
        <div className="mode-pick" role="radiogroup" aria-label="Как найти подрядчика">
          <button type="button" role="radio" aria-checked={mode === "platform"} className="mode" onClick={() => void setMode("platform")}>
            <b>Подберёт платформа</b>
            <span className="muted small">Мы предложим проверенного подрядчика под ваш ремонт.</span>
          </button>
          <button type="button" role="radio" aria-checked={mode === "market"} className="mode" onClick={() => void setMode("market")}>
            <b>Выберу сам</b>
            <span className="muted small">
              Опубликуйте заявку: проверенные компании и бригады пришлют цену и сроки. <Link href="/contractors">Каталог</Link>
            </span>
          </button>
        </div>
      )}
      {choosing && mode === "platform" && <p className="muted">Администратор платформы назначит подрядчика и сообщит вам.</p>}
      {mode === "market" && choosing && (!task || task.status !== "open") && (
        <TaskForm objectId={obj.id} area={obj.area_m2} initial={task ?? null} onSaved={setTask} />
      )}
      {task && task.status === "open" && owner && <OpenTask task={task} onChanged={async () => { await onChanged(); await load(); }} />}
      {task && task.status === "awarded" && (
        <p>
          Подрядчик выбран по заявке «{task.title}»
          {task.bids.find((b) => b.status === "accepted")?.contractor?.name &&
            `: ${task.bids.find((b) => b.status === "accepted")?.contractor?.name}`}
          .
        </p>
      )}
      {showReview && <ReviewForm objectId={obj.id} />}
    </section>
  );
}

function TaskForm({
  objectId,
  area,
  initial,
  onSaved,
}: {
  objectId: string;
  area: string | null;
  initial: MarketTask | null;
  onSaved: (t: MarketTask) => void;
}) {
  const [title, setTitle] = useState(initial?.title ?? (area ? `Ремонт квартиры ${Number(area)} м²` : "Ремонт квартиры"));
  const [areaHint, setAreaHint] = useState(initial?.area_hint ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [types, setTypes] = useState<string[]>(initial?.work_types ?? []);
  const [budget, setBudget] = useState(initial?.budget_kop ? String(initial.budget_kop / 100) : "");
  const [start, setStart] = useState(initial?.desired_start ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const budgetKop = budget.trim() ? parseRub(budget) : null;
    if (budget.trim() && budgetKop === null) {
      setError("Бюджет — сумма в рублях, например 450 000");
      return;
    }
    setBusy(true);
    try {
      onSaved(
        await api<MarketTask>(`/objects/${objectId}/market-task`, {
          method: "PUT",
          body: {
            title,
            area_hint: areaHint,
            description: description || null,
            work_types: types,
            budget_kop: budgetKop,
            desired_start: start || null,
          },
        }),
      );
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit}>
      <h3>Заявка для подрядчиков</h3>
      <label htmlFor="mt-title">Что нужно сделать</label>
      <input id="mt-title" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
      <label htmlFor="mt-area">Город и район</label>
      <input id="mt-area" value={areaHint} maxLength={100} onChange={(e) => setAreaHint(e.target.value)} placeholder="Например, Москва, СВАО" />
      <p className="muted small">Точный адрес подрядчик увидит, только когда вы его выберете.</p>
      <label htmlFor="mt-desc">Подробнее (необязательно)</label>
      <textarea id="mt-desc" rows={3} maxLength={4000} value={description} onChange={(e) => setDescription(e.target.value)} />
      <span className="field-title">Виды работ</span>
      <WorkTypes value={types} onChange={setTypes} />
      <label htmlFor="mt-budget">Бюджет на работы, ₽ (необязательно)</label>
      <input id="mt-budget" inputMode="numeric" value={budget} onChange={(e) => setBudget(e.target.value)} />
      <label htmlFor="mt-start">Когда начать (необязательно)</label>
      <input id="mt-start" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
      <ErrorNote text={error} />
      <button type="submit" className="primary" disabled={busy || title.trim().length < 5 || areaHint.trim().length < 2}>
        Опубликовать заявку
      </button>
    </form>
  );
}

function OpenTask({ task, onChanged }: { task: MarketTask; onChanged: () => Promise<void> }) {
  const [error, setError] = useState<string | null>(null);
  const active = task.bids.filter((b) => b.status === "submitted");

  async function act(path: string) {
    setError(null);
    try {
      await api(path, { method: "POST" });
      await onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <div>
      <p>
        Заявка «{task.title}» опубликована {formatDate(task.created_at)}.{" "}
        {active.length === 0 ? "Откликов пока нет: подрядчикам пришло уведомление." : `Откликов: ${active.length}.`}
      </p>
      <ErrorNote text={error} />
      {active.length > 0 && (
        <ul className="list">
          {active.map((b) => (
            <BidRow key={b.id} bid={b} onAccept={() => void act(`/market-bids/${b.id}/accept`)} />
          ))}
        </ul>
      )}
      <button type="button" className="link" onClick={() => void act(`/market-tasks/${task.id}/close`)}>
        Снять заявку
      </button>
    </div>
  );
}

function BidRow({ bid, onAccept }: { bid: MarketBid; onAccept: () => void }) {
  const [sure, setSure] = useState(false);
  return (
    <li>
      <div className="grow">
        {bid.contractor && <ContractorSummary c={bid.contractor} />}
        <p>
          <b>{rub(bid.price_kop)}</b> за работы · {bid.days} дн.
          {bid.start_date && ` · начнут ${new Date(bid.start_date).toLocaleDateString("ru-RU")}`}
        </p>
        {bid.message && <p className="pre small">{bid.message}</p>}
        {sure ? (
          <div className="row">
            <button type="button" className="primary" onClick={onAccept}>
              Да, выбрать {bid.contractor?.name ?? "подрядчика"}
            </button>
            <button type="button" className="link" onClick={() => setSure(false)}>
              Отмена
            </button>
          </div>
        ) : (
          <button type="button" className="secondary" onClick={() => setSure(true)}>
            Выбрать
          </button>
        )}
      </div>
    </li>
  );
}

function ReviewForm({ objectId }: { objectId: string }) {
  const [mine, setMine] = useState<ContractorReview | null | undefined>(undefined);
  const [rating, setRating] = useState(0);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<ContractorReview | null>(`/objects/${objectId}/contractor-review`).then(setMine).catch(() => setMine(null));
  }, [objectId]);

  async function send() {
    setError(null);
    try {
      setMine(await api<ContractorReview>(`/objects/${objectId}/contractor-review`, { method: "POST", body: { rating, text: text || null } }));
    } catch (err) {
      setError(errorText(err));
    }
  }

  if (mine === undefined) return null;
  if (mine) return <p>Ваша оценка подрядчику: <span className="stars">{"★".repeat(mine.rating)}</span>. Спасибо!</p>;
  return (
    <div className="boxed">
      <h3>Оцените подрядчика</h3>
      <div className="row" role="radiogroup" aria-label="Оценка">
        {[1, 2, 3, 4, 5].map((n) => (
          <button key={n} type="button" role="radio" aria-checked={rating === n} className="star-btn" onClick={() => setRating(n)}>
            {n <= rating ? "★" : "☆"}
            <span className="sr-only">{n}</span>
          </button>
        ))}
      </div>
      <label htmlFor="rv-text">Отзыв (необязательно)</label>
      <textarea id="rv-text" rows={3} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} />
      <ErrorNote text={error} />
      <button type="button" className="primary" disabled={!rating} onClick={() => void send()}>
        Отправить оценку
      </button>
    </div>
  );
}

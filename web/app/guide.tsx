"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api, errorText } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { AssistantAnswer, CalcRow, EngineerQuestion, TechCard } from "@/lib/types";

import { ErrorNote } from "./components";

export function exampleUrl(card: TechCard, key: string): string {
  return `/api/v1/tech-cards/${card.stage_type}/steps/${key}/example`;
}

/** Калькулятор расхода материалов по техкарте. */
export function Calculator({ card }: { card: TechCard }) {
  const units = Array.from(new Set(card.materials.map((m) => m.per)));
  const [amount, setAmount] = useState<Record<string, string>>({});
  const [thickness, setThickness] = useState<Record<string, string>>(() =>
    Object.fromEntries(card.materials.filter((m) => m.per_mm).map((m) => [m.key, String(m.thickness_mm ?? "")])),
  );
  const [rows, setRows] = useState<CalcRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (card.materials.length === 0) return null;

  const field: Record<string, string> = { "м²": "area_m2", "м.п.": "length_m", шт: "count", "м³": "volume_m3" };
  const label: Record<string, string> = { "м²": "Площадь, м²", "м.п.": "Длина, м", шт: "Количество, шт", "м³": "Объём, м³" };

  async function calc(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const body: Record<string, unknown> = {
      thickness: Object.fromEntries(Object.entries(thickness).filter(([, v]) => Number(v) > 0).map(([k, v]) => [k, Number(v.replace(",", "."))])),
    };
    for (const u of units) if (Number(amount[u]?.replace(",", ".")) > 0) body[field[u]] = Number(amount[u].replace(",", "."));
    try {
      setRows(await api<CalcRow[]>(`/tech-cards/${card.stage_type}/calc`, { method: "POST", body }));
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <section className="card">
      <h2>Сколько нужно материала</h2>
      <form onSubmit={calc} className="grid2">
        {units.map((u) => (
          <div key={u}>
            <label htmlFor={`calc-${u}`}>{label[u] ?? u}</label>
            <input id={`calc-${u}`} inputMode="decimal" value={amount[u] ?? ""} onChange={(e) => setAmount({ ...amount, [u]: e.target.value })} />
          </div>
        ))}
        {card.materials
          .filter((m) => m.per_mm)
          .map((m) => (
            <div key={m.key}>
              <label htmlFor={`th-${m.key}`}>{m.name}: толщина слоя, мм</label>
              <input id={`th-${m.key}`} inputMode="decimal" value={thickness[m.key] ?? ""} onChange={(e) => setThickness({ ...thickness, [m.key]: e.target.value })} />
            </div>
          ))}
        <div className="row end">
          <button type="submit" className="primary" disabled={!units.some((u) => Number(amount[u]?.replace(",", ".")) > 0)}>
            Посчитать
          </button>
        </div>
      </form>
      <ErrorNote text={error} />
      {rows && (
        <ul className="list">
          {rows.length === 0 && <li className="muted">Для этих размеров материалов нет.</li>}
          {rows.map((r) => (
            <li key={r.key}>
              <div>
                <b>{r.name}</b>: {r.quantity.toLocaleString("ru-RU")} {r.unit}
                {r.packs != null && r.pack_title && (
                  <>
                    {" "}
                    — <b>{r.packs}</b> × {r.pack_title}
                  </>
                )}
                {r.note && <div className="muted small">{r.note}</div>}
              </div>
            </li>
          ))}
        </ul>
      )}
      {rows && <p className="muted small">С запасом на подрезку и потери. Точный расход — на упаковке материала.</p>}
    </section>
  );
}

/** Помощник: ответ по базе техкарт. */
export function Assistant({ stageType, onAskEngineer }: { stageType?: string | null; onAskEngineer?: (q: string) => void }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AssistantAnswer | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ask(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setAnswer(await api<AssistantAnswer>("/assistant/ask", { method: "POST", body: { question, stage_type: stageType ?? null } }));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Спросить помощника</h2>
      <form onSubmit={ask}>
        <label htmlFor="as-q">Вопрос по работе</label>
        <textarea id="as-q" rows={2} maxLength={1000} placeholder="Например: какой допуск по плитке на стене?" value={question} onChange={(e) => setQuestion(e.target.value)} />
        <button type="submit" className="primary" disabled={busy || question.trim().length < 3}>
          {busy ? "Ищем ответ…" : "Спросить"}
        </button>
      </form>
      <ErrorNote text={error} />
      {answer && (
        <div className="boxed">
          <p className="pre">{answer.answer}</p>
          {answer.sources.length > 0 && (
            <p className="muted small">По техкартам: {answer.sources.map((s) => `${s.card_title} — ${s.step_title}`).join("; ")}</p>
          )}
          <p className="muted small">Помощник отвечает по техкартам и может ошибаться. Если сомневаетесь — спросите инженера.</p>
          {onAskEngineer && (
            <button type="button" className="secondary" onClick={() => onAskEngineer(question)}>
              Спросить инженера
            </button>
          )}
        </div>
      )}
    </section>
  );
}

/** «Спросить инженера» с фото и список вопросов по объекту. */
export function AskEngineer({ stageId, objectId, card, draft }: { stageId: string; objectId: string; card: TechCard | null; draft?: string }) {
  const [text, setText] = useState(draft ?? "");
  const [stepKey, setStepKey] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [questions, setQuestions] = useState<EngineerQuestion[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (draft) setText(draft);
  }, [draft]);

  const load = useCallback(async () => {
    try {
      setQuestions((await api<EngineerQuestion[]>(`/objects/${objectId}/questions`)).filter((q) => q.stage_id === stageId));
    } catch {
      // Список подгрузится, когда появится связь.
    }
  }, [objectId, stageId]);
  useEffect(() => void load(), [load]);

  async function send(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNote(null);
    const form = new FormData();
    form.append("text", text);
    if (stepKey) form.append("step_key", stepKey);
    if (file) form.append("photo", file);
    try {
      await api(`/stages/${stageId}/questions`, { method: "POST", form });
      setText("");
      setFile(null);
      setNote("Вопрос отправлен. Инженер ответит здесь, а вам придёт СМС.");
      await load();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" id="ask">
      <h2>Спросить инженера</h2>
      <form onSubmit={send}>
        <label htmlFor="q-text">Вопрос</label>
        <textarea id="q-text" rows={3} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} />
        {card && (
          <>
            <label htmlFor="q-step">Шаг работы (необязательно)</label>
            <select id="q-step" value={stepKey} onChange={(e) => setStepKey(e.target.value)}>
              <option value="">Не выбран</option>
              {card.steps.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.title}
                </option>
              ))}
            </select>
          </>
        )}
        <label htmlFor="q-photo">Фото (необязательно)</label>
        <input id="q-photo" type="file" accept="image/jpeg,image/webp,image/png" capture="environment" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <button type="submit" className="primary" disabled={busy || text.trim().length < 3}>
          {busy ? "Отправляем…" : "Отправить инженеру"}
        </button>
      </form>
      {note && <p className="ok">{note}</p>}
      <ErrorNote text={error} />
      {questions.length > 0 && (
        <ul className="list">
          {questions.map((q) => (
            <li key={q.id}>
              <div>
                <div>{q.text}</div>
                <div className="muted small">{formatDate(q.created_at)}</div>
                {q.answer ? <div className="boxed">Инженер: {q.answer}</div> : <div className="muted small">Ждёт ответа инженера</div>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

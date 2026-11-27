"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { api, errorText } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { formatDate, STAGE_STATUS_LABELS } from "@/lib/format";
import type { EngineerQuestion, Inspection, Page } from "@/lib/types";

import { ErrorNote, Loading, Shell } from "../components";

/** Выезды инженера технадзора (администратор видит все). */
export default function InspectionsPage() {
  const { me, loading } = useRequireAuth();
  const [items, setItems] = useState<Inspection[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!me) return;
    api<Page<Inspection>>("/inspections?limit=100")
      .then((p) => setItems(p.items))
      .catch((err) => setError(errorText(err)));
  }, [me]);

  if (loading || !me) return <Shell><Loading /></Shell>;
  return (
    <Shell>
      <h1>Выезды технадзора</h1>
      <ErrorNote text={error} />
      {items === null ? (
        error ? null : <Loading />
      ) : items.length === 0 ? (
        <section className="card empty">
          <p>Открытых выездов нет. Когда администратор назначит вас на проверку, она появится здесь.</p>
        </section>
      ) : (
        <ul className="cards">
          {items.map((i) => (
            <li key={i.id}>
              <Link href={`/stages/${i.stage_id}`} className="card linkcard">
                <b>{i.address}</b>
                <div>{i.stage_title}</div>
                <div className="muted">
                  {i.scheduled_at ? formatDate(i.scheduled_at) : "Время не назначено"} ·{" "}
                  {i.stage_status === "inspection" ? "Этап сдан, ждёт проверки" : STAGE_STATUS_LABELS[i.stage_status]}
                </div>
                {me.is_platform_admin && <div className="muted">Инженер: {i.inspector_name}</div>}
              </Link>
            </li>
          ))}
        </ul>
      )}
      <Questions />
    </Shell>
  );
}

/** Вопросы бригад инженеру: текст, фото, ответ. */
function Questions() {
  const [items, setItems] = useState<EngineerQuestion[] | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(await api<EngineerQuestion[]>("/questions"));
    } catch (err) {
      setError(errorText(err));
    }
  }, []);
  useEffect(() => void load(), [load]);

  async function answer(q: EngineerQuestion) {
    setError(null);
    try {
      await api(`/questions/${q.id}/answer`, { method: "POST", body: { answer: answers[q.id] } });
      await load();
    } catch (err) {
      setError(errorText(err));
    }
  }

  if (items === null) return null;
  return (
    <section className="card" id="questions">
      <h2>Вопросы бригад</h2>
      <ErrorNote text={error} />
      {items.length === 0 ? (
        <p className="muted">Новых вопросов нет.</p>
      ) : (
        <ul className="list">
          {items.map((q) => (
            <li key={q.id}>
              <div className="grow">
                <b>{q.address}</b>
                <div className="muted small">
                  {q.stage_title ?? "Без этапа"} · {q.author_name} · {formatDate(q.created_at)}
                </div>
                <p>{q.text}</p>
                {q.photo_url && (
                  <a href={q.photo_url} target="_blank" rel="noreferrer">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img className="example-photo" src={q.photo_url} alt="Фото к вопросу" loading="lazy" />
                  </a>
                )}
                {q.can_answer && (
                  <>
                    <label htmlFor={`a-${q.id}`}>Ответ</label>
                    <textarea id={`a-${q.id}`} rows={3} value={answers[q.id] ?? ""} onChange={(e) => setAnswers({ ...answers, [q.id]: e.target.value })} />
                    <button type="button" className="primary" disabled={(answers[q.id] ?? "").trim().length < 2} onClick={() => void answer(q)}>
                      Ответить
                    </button>
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

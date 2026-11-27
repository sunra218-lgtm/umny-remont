"use client";

import { useEffect, useState } from "react";

import { api, errorText } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { STAGE_TYPE_LABELS } from "@/lib/format";
import type { ContractorCard } from "@/lib/types";

import { ErrorNote, Loading, Shell } from "../components";
import { ContractorSummary } from "../market";

/** Каталог проверенных подрядчиков: компании и бригады с оценками и портфолио. */
export default function ContractorsPage() {
  const { me, loading } = useRequireAuth();
  const [items, setItems] = useState<ContractorCard[] | null>(null);
  const [kind, setKind] = useState("");
  const [work, setWork] = useState("");
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!me) return;
    const params = new URLSearchParams();
    if (kind) params.set("kind", kind);
    if (work) params.set("work_type", work);
    if (q.trim().length >= 2) params.set("q", q.trim());
    const t = window.setTimeout(() => {
      api<ContractorCard[]>(`/contractors?${params}`)
        .then(setItems)
        .catch((err) => setError(errorText(err)));
    }, 250);
    return () => window.clearTimeout(t);
  }, [me, kind, work, q]);

  if (loading || !me) return <Shell><Loading /></Shell>;
  return (
    <Shell>
      <h1>Подрядчики</h1>
      <p className="muted">
        Все подрядчики прошли проверку платформы. Деньги за этап они получают только после приёмки, оценки ставят заказчики
        после ремонта.
      </p>
      <div className="filters">
        <div>
          <label htmlFor="ct-q">Поиск</label>
          <input id="ct-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Название или город" />
        </div>
        <div>
          <label htmlFor="ct-kind">Кто</label>
          <select id="ct-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">Все</option>
            <option value="company">Строительные компании</option>
            <option value="brigade">Бригады и мастера</option>
          </select>
        </div>
        <div>
          <label htmlFor="ct-work">Виды работ</label>
          <select id="ct-work" value={work} onChange={(e) => setWork(e.target.value)}>
            <option value="">Любые</option>
            {Object.entries(STAGE_TYPE_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </div>
      </div>
      <ErrorNote text={error} />
      {items === null ? (
        error ? null : <Loading />
      ) : items.length === 0 ? (
        <section className="card empty">
          <p>Под эти условия подрядчиков нет. Попробуйте убрать фильтр.</p>
        </section>
      ) : (
        <ul className="cards">
          {items.map((c) => (
            <li key={c.org_id} className="card">
              <ContractorSummary c={c} />
            </li>
          ))}
        </ul>
      )}
    </Shell>
  );
}

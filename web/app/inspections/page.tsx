"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api, errorText } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { formatDate, STAGE_STATUS_LABELS } from "@/lib/format";
import type { Inspection, Page } from "@/lib/types";

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
    </Shell>
  );
}

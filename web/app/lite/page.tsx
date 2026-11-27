"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api, errorText } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useLite } from "@/lib/lite";
import type { Page, RepairObject, Stage } from "@/lib/types";

import { cacheGet, cacheSet, LiteGate } from "./parts";

type ObjectWithStages = { obj: RepairObject; stages: Stage[] };

export default function LiteHome() {
  return (
    <LiteGate>
      <Home />
    </LiteGate>
  );
}

function Home() {
  const { me, queue, online, sync, syncing } = useLite();
  const [data, setData] = useState<ObjectWithStages[] | null>(() => cacheGet("lite:objects"));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!me) return;
    (async () => {
      try {
        const objects = (await api<Page<RepairObject>>("/objects?limit=50")).items.filter(
          (o) => o.status !== "cancelled" && o.status !== "completed",
        );
        const rows = await Promise.all(
          objects.map(async (obj) => ({ obj, stages: await api<Stage[]>(`/objects/${obj.id}/stages`) })),
        );
        setData(rows);
        cacheSet("lite:objects", rows);
      } catch (err) {
        setError(errorText(err));
      }
    })();
  }, [me]);

  const failed = queue.filter((q) => q.error);
  return (
    <>
      <h1>Фотоотчёт</h1>
      {queue.length > 0 && (
        <section className="card">
          <h2>Ждут отправки: {queue.length}</h2>
          <p className="muted">
            {online ? "Фото уходят на сервер сами." : "Как только появится связь, фото отправятся сами. Приложение можно закрыть."}
          </p>
          {failed.map((q) => (
            <p key={q.id} className="error">
              {q.stageTitle}, {formatDate(q.capturedAt)}: {q.error}
            </p>
          ))}
          {online && (
            <button type="button" className="secondary" disabled={syncing} onClick={() => void sync()}>
              {syncing ? "Отправляем…" : "Отправить сейчас"}
            </button>
          )}
        </section>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {data === null ? (
        <div className="skeleton" aria-busy="true" aria-label="Загрузка" />
      ) : data.length === 0 ? (
        <section className="card empty">
          <p>Вас пока не назначили ни на один объект. Руководитель назначит вас и пришлёт ссылку.</p>
        </section>
      ) : (
        data.map(({ obj, stages }) => (
          <section className="card" key={obj.id}>
            <h2>{obj.address_text}</h2>
            {stages.length === 0 ? (
              <p className="muted">В смете пока нет этапов. Снимок можно будет сделать, когда их добавят.</p>
            ) : (
              <ul className="list">
                {stages.map((s) => (
                  <li key={s.id}>
                    <div>
                      <b>
                        {s.seq}. {s.title}
                      </b>
                      {s.is_stop_point && <div className="muted">Стоп-точка: нужна приёмка</div>}
                    </div>
                    <Link
                      className="button primary"
                      href={`/lite/capture?stage=${s.id}&title=${encodeURIComponent(s.title)}&address=${encodeURIComponent(obj.address_text)}`}
                    >
                      Снять отчёт
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>
        ))
      )}
    </>
  );
}

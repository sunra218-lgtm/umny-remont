"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api, errorText } from "@/lib/api";
import { formatDate, STAGE_STATUS_LABELS } from "@/lib/format";
import { useLite } from "@/lib/lite";
import type { Page, RepairObject, Stage, StageChecklist } from "@/lib/types";

import { DailyPlans } from "../daily";
import { LiteMaterials } from "./materials";
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
  const [note, setNote] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{ stage: Stage; missing: string[] } | null>(null);

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

  async function act(stage: Stage, action: "start" | "submit") {
    setError(null);
    setNote(null);
    try {
      const out = await api<Stage | { stage: Stage; missing: string[] }>(`/stages/${stage.id}/${action}`, { method: "POST" });
      const updated = "stage" in out ? out.stage : out;
      const missing = "missing" in out ? out.missing : [];
      setData((rows) => {
        const next = rows?.map((r) => ({ ...r, stages: r.stages.map((s) => (s.id === updated.id ? updated : s)) })) ?? null;
        cacheSet("lite:objects", next);
        return next;
      });
      setNote(
        action === "start"
          ? `«${stage.title}»: работы начаты.`
          : `«${stage.title}» сдан${missing.length ? `. Не хватает фото: ${missing.join(", ")}` : ""}.`,
      );
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function trySubmit(stage: Stage) {
    setError(null);
    setNote(null);
    try {
      const c = await api<StageChecklist>(`/stages/${stage.id}/checklist`);
      const missing = c.items.filter((i) => c.missing.includes(i.id)).map((i) => i.title);
      const waiting = queue.some((q) => q.stageId === stage.id);
      if (missing.length || waiting) {
        setConfirm({ stage, missing: waiting ? [...missing, "часть фото ещё не отправлена"] : missing });
        return;
      }
      await act(stage, "submit");
    } catch (err) {
      setError(errorText(err));
    }
  }

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
      {note && (
        <p className="ok" role="status">
          {note}
        </p>
      )}
      {confirm && (
        <section className="card" role="alertdialog" aria-labelledby="confirm-title">
          <h2 id="confirm-title">Сдать «{confirm.stage.title}»?</h2>
          <p>Не хватает: {confirm.missing.join(", ")}. Проверяющий это увидит.</p>
          <div className="row">
            <button
              type="button"
              className="primary"
              onClick={() => {
                const st = confirm.stage;
                setConfirm(null);
                void act(st, "submit");
              }}
            >
              Сдать без этих фото
            </button>
            <button type="button" className="secondary" onClick={() => setConfirm(null)}>
              Доснять фото
            </button>
          </div>
        </section>
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
                      <div className="muted small">
                        {STAGE_STATUS_LABELS[s.status] ?? s.status}
                        {s.is_stop_point && " · проверка технадзора"}
                      </div>
                    </div>
                    <div className="row">
                      {s.status === "ready" && online && (
                        <button type="button" className="secondary" onClick={() => void act(s, "start")}>
                          Начать работы
                        </button>
                      )}
                      {s.status === "in_progress" && online && (
                        <button type="button" className="secondary" onClick={() => void trySubmit(s)}>
                          Сдать этап
                        </button>
                      )}
                      <Link
                        className="button secondary"
                        href={`/lite/guide?stage=${s.id}&object=${obj.id}&title=${encodeURIComponent(s.title)}`}
                      >
                        Как делать
                      </Link>
                      <Link
                        className="button primary"
                        href={`/lite/capture?stage=${s.id}&title=${encodeURIComponent(s.title)}&address=${encodeURIComponent(obj.address_text)}`}
                      >
                        Снять отчёт
                      </Link>
                    </div>
                  </li>
                ))}
              </ul>
            )}
            <DailyPlans objectId={obj.id} stages={stages} canPlan online={online} nested />
            <LiteMaterials objectId={obj.id} stages={stages} online={online} />
          </section>
        ))
      )}
    </>
  );
}

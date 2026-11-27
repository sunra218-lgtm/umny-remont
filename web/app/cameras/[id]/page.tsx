"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { api, errorText } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type { Camera, CameraFrames, Stage } from "@/lib/types";

import { CAMERA_STATE } from "../../cameras";
import { ErrorNote, Loading, Shell } from "../../components";

const SPEEDS = [
  { label: "Медленно", ms: 600 },
  { label: "Обычно", ms: 250 },
  { label: "Быстро", ms: 100 },
];

function time(iso: string): string {
  return new Date(iso).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Moscow" });
}

function dayLabel(day: string): string {
  return new Date(`${day}T12:00:00+03:00`).toLocaleDateString("ru-RU", { day: "numeric", month: "short", weekday: "short" });
}

/** Таймлапс камеры за день: проигрывание кадров, скрытые работы, разметка инженером. */
export default function CameraPage() {
  const { id } = useParams<{ id: string }>();
  const { me, loading } = useRequireAuth();
  const [cam, setCam] = useState<Camera | null>(null);
  const [data, setData] = useState<CameraFrames | null>(null);
  const [day, setDay] = useState<string | null>(null);
  const [hiddenOnly, setHiddenOnly] = useState(false);
  const [pos, setPos] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(SPEEDS[1].ms);
  const [stages, setStages] = useState<Stage[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const q = new URLSearchParams();
      if (day) q.set("day", day);
      if (hiddenOnly) q.set("hidden_only", "true");
      const r = await api<CameraFrames>(`/cameras/${id}/frames?${q}`);
      setData(r);
      setPos((p) => Math.min(p, Math.max(0, r.frames.length - 1)));
    } catch (err) {
      setError(errorText(err));
    }
  }, [id, day, hiddenOnly]);

  useEffect(() => {
    if (!me) return;
    api<Camera>(`/cameras/${id}`)
      .then((c) => {
        setCam(c);
        return api<Stage[]>(`/objects/${c.object_id}/stages`).then(setStages);
      })
      .catch((err) => setError(errorText(err)));
  }, [me, id]);
  useEffect(() => {
    if (me) void load();
  }, [me, load]);

  const count = data?.frames.length ?? 0;
  useEffect(() => {
    if (!playing || count < 2) return;
    const t = window.setInterval(() => {
      setPos((p) => {
        if (p + 1 >= count) {
          setPlaying(false);
          return p;
        }
        return p + 1;
      });
    }, speed);
    return () => window.clearInterval(t);
  }, [playing, speed, count]);

  if (loading || !me) return <Shell><Loading /></Shell>;
  const frame = data?.frames[pos] ?? null;

  return (
    <Shell>
      {cam && (
        <p>
          <Link href={`/objects/${cam.object_id}#cameras`}>← К объекту</Link>
        </p>
      )}
      <h1>Таймлапс{cam ? `: ${cam.title}` : ""}</h1>
      {cam && <p className="muted">{CAMERA_STATE[cam.state]} · снимает с {cam.hour_from}:00 до {cam.hour_to}:00</p>}
      <ErrorNote text={error} />
      {data === null ? (
        error ? null : <Loading />
      ) : (
        <>
          {data.days.length > 0 && (
            <div className="chips" role="group" aria-label="День">
              {data.days.map((d) => (
                <button
                  key={d.day}
                  type="button"
                  className="chip"
                  aria-pressed={d.day === data.day}
                  onClick={() => {
                    setDay(d.day);
                    setPos(0);
                    setPlaying(false);
                  }}
                >
                  {dayLabel(d.day)} · {d.frames}
                </button>
              ))}
            </div>
          )}
          <label className="check">
            <input type="checkbox" checked={hiddenOnly} onChange={(e) => { setHiddenOnly(e.target.checked); setPos(0); }} />
            <span>Только кадры со скрытыми работами</span>
          </label>
          {count === 0 ? (
            <section className="card empty">
              <p>
                {data.days.length === 0
                  ? "Кадров пока нет. Камера начнёт снимать, когда заказчик её включит, в рабочие часы."
                  : "За этот день подходящих кадров нет."}
              </p>
            </section>
          ) : (
            <section className="card timelapse">
              {frame && (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={frame.url} alt={`Кадр ${time(frame.taken_at)}`} />
              )}
              <div className="row between">
                <b>{frame ? time(frame.taken_at) : ""}</b>
                <span className="muted small">
                  Кадр {pos + 1} из {count}
                </span>
              </div>
              <input
                type="range"
                min={0}
                max={count - 1}
                value={pos}
                aria-label="Кадр"
                onChange={(e) => {
                  setPlaying(false);
                  setPos(Number(e.target.value));
                }}
              />
              <div className="row">
                <button type="button" className="primary" onClick={() => {
                  if (!playing && pos >= count - 1) setPos(0);
                  setPlaying(!playing);
                }}>
                  {playing ? "Пауза" : "Смотреть"}
                </button>
                <label htmlFor="tl-speed" className="sr-only">Скорость</label>
                <select id="tl-speed" style={{ width: "auto" }} value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
                  {SPEEDS.map((s) => (
                    <option key={s.ms} value={s.ms}>{s.label}</option>
                  ))}
                </select>
              </div>
              {frame && (
                <FrameInfo
                  key={frame.id}
                  frame={frame}
                  stages={stages}
                  canLabel={data.can_label && !playing}
                  onSaved={load}
                />
              )}
            </section>
          )}
        </>
      )}
    </Shell>
  );
}

function FrameInfo({
  frame,
  stages,
  canLabel,
  onSaved,
}: {
  frame: CameraFrames["frames"][number];
  stages: Stage[];
  canLabel: boolean;
  onSaved: () => Promise<void>;
}) {
  const [stageId, setStageId] = useState(frame.stage_id ?? "");
  const [hidden, setHidden] = useState(frame.hidden_work);
  const [note, setNote] = useState(frame.labels.note ?? "");
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setError(null);
    try {
      await api(`/camera-frames/${frame.id}`, {
        method: "PATCH",
        body: { stage_id: stageId || null, hidden_work: hidden, note: note.trim() || null },
      });
      await onSaved();
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <div className="boxed">
      <p className="small">
        {frame.stage_title ? `Этап: ${frame.stage_title}` : "Этап не определён"}
        {frame.hidden_work && " · скрытые работы"}
        {!frame.changed && " · без изменений с прошлого кадра"}
        {" · "}
        {frame.labeled_by === "engineer" ? "размечено инженером" : "по графику работ"}
      </p>
      {frame.hidden_work && frame.labels.hidden_steps && frame.labels.hidden_steps.length > 0 && (
        <p className="muted small">Что закроют: {frame.labels.hidden_steps.join(", ")}</p>
      )}
      {frame.labels.note && <p className="small">Инженер: {frame.labels.note}</p>}
      {canLabel && (
        <>
          <label htmlFor="fl-stage">Этап на кадре</label>
          <select id="fl-stage" value={stageId} onChange={(e) => setStageId(e.target.value)}>
            <option value="">Не определён</option>
            {stages.map((s) => (
              <option key={s.id} value={s.id}>{s.title}</option>
            ))}
          </select>
          <label className="check">
            <input type="checkbox" checked={hidden} onChange={(e) => setHidden(e.target.checked)} />
            <span>На кадре видны скрытые работы</span>
          </label>
          <label htmlFor="fl-note">Заметка</label>
          <input id="fl-note" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} />
          <ErrorNote text={error} />
          <button type="button" className="secondary" onClick={() => void save()}>
            Сохранить разметку
          </button>
        </>
      )}
    </div>
  );
}

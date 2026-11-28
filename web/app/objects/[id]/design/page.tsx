"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import { api, errorText } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { qty, rub } from "@/lib/format";

import { ErrorNote, Loading, Shell } from "../../../components";
import {
  RoomPreview,
  roughRub,
  type DesignEstimate,
  type DesignInfo,
  type DesignLine,
  type DesignMaterial,
  type DesignProject,
  type DesignRoom,
} from "../../../design";

const VIDEO_STATUS: Record<string, string> = {
  processing: "Ролик готовится — обычно это занимает несколько часов",
  done: "Ролик готов",
  failed: "Ролик не получился, попробуйте заказать ещё раз",
};

/** Умный дизайн-проект: стиль → план БТИ → отделка по комнатам → стоимость → картинки и ролик. */
export default function DesignPage() {
  const { id } = useParams<{ id: string }>();
  const { me, loading } = useRequireAuth();
  const [info, setInfo] = useState<DesignInfo | null>(null);
  const [catalog, setCatalog] = useState<DesignMaterial[]>([]);
  const [project, setProject] = useState<DesignProject | null | undefined>(undefined);
  const [estimate, setEstimate] = useState<DesignEstimate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const apply = useCallback(
    async (p: DesignProject | null) => {
      setProject(p);
      setEstimate(p ? await api<DesignEstimate>(`/objects/${id}/design/estimate`) : null);
    },
    [id],
  );

  const load = useCallback(async () => {
    try {
      const [i, mats, p] = await Promise.all([
        api<DesignInfo>("/design/info"),
        api<DesignMaterial[]>("/design/materials"),
        api<DesignProject | null>(`/objects/${id}/design`),
      ]);
      setInfo(i);
      setCatalog(mats);
      await apply(p);
    } catch (err) {
      setError(errorText(err));
    }
  }, [id, apply]);
  useEffect(() => {
    if (me) void load();
  }, [me, load]);

  // Пока рисуются картинки или готовится ролик — тихо обновляем.
  const waiting =
    !!project &&
    (project.renders.some((r) => r.status === "queued" || r.status === "running") || project.video_status === "processing");
  useEffect(() => {
    if (!waiting) return;
    const t = setInterval(async () => {
      try {
        setProject(await api<DesignProject | null>(`/objects/${id}/design`));
      } catch {
        /* следующая попытка */
      }
    }, 8000);
    return () => clearInterval(t);
  }, [waiting, id]);

  async function run(fn: () => Promise<DesignProject>) {
    setBusy(true);
    setError(null);
    try {
      await apply(await fn());
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  if (loading || !me) return <Shell><Loading /></Shell>;
  if (project === undefined || !info) return <Shell>{error ? <ErrorNote text={error} /> : <Loading />}</Shell>;

  const canEdit = project ? project.can_edit : true;
  return (
    <Shell wide>
      <p>
        <Link href={`/objects/${id}`}>← К объекту</Link>
      </p>
      <h1>Дизайн-проект</h1>
      <ErrorNote text={error} />
      <StylePicker
        info={info}
        current={project?.style ?? null}
        disabled={busy || !canEdit}
        onPick={(style, roomsCount) =>
          run(() => api<DesignProject>(`/objects/${id}/design`, { method: "PUT", body: { style, rooms_count: roomsCount } }))
        }
        isNew={!project}
      />
      {project && (
        <>
          <PlanAndRooms
            info={info}
            project={project}
            busy={busy}
            onUpload={(file) => {
              const form = new FormData();
              form.append("plan", file);
              return run(() => api<DesignProject>(`/objects/${id}/design/plan`, { method: "POST", form }));
            }}
            onSave={(rooms) =>
              run(() => api<DesignProject>(`/objects/${id}/design/rooms`, { method: "PUT", body: { rooms } }))
            }
          />
          <Rooms
            info={info}
            project={project}
            catalog={catalog}
            busy={busy}
            onChoose={(room, slot, materialId) =>
              run(() =>
                api<DesignProject>(`/objects/${id}/design/rooms/${room}/${slot}`, {
                  method: "PUT",
                  body: { material_id: materialId },
                }),
              )
            }
          />
          {estimate && <EstimateView estimate={estimate} />}
          <Media
            info={info}
            project={project}
            busy={busy}
            onRenders={() => run(() => api<DesignProject>(`/objects/${id}/design/renders`, { method: "POST" }))}
            onVideo={() => run(() => api<DesignProject>(`/objects/${id}/design/video`, { method: "POST" }))}
          />
        </>
      )}
    </Shell>
  );
}

function StylePicker({
  info,
  current,
  disabled,
  isNew,
  onPick,
}: {
  info: DesignInfo;
  current: string | null;
  disabled: boolean;
  isNew: boolean;
  onPick: (style: string, roomsCount: number) => void;
}) {
  const [rooms, setRooms] = useState(2);
  return (
    <section className="card">
      <h2>1. Стиль</h2>
      {isNew && (
        <label className="field-title">
          Сколько жилых комнат
          <select value={rooms} onChange={(e) => setRooms(Number(e.target.value))}>
            {[1, 2, 3, 4].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      )}
      <div className="style-grid" role="radiogroup" aria-label="Стиль интерьера">
        {info.styles.map((s) => (
          <button
            key={s.key}
            type="button"
            role="radio"
            aria-checked={current === s.key}
            className="style-card"
            disabled={disabled}
            onClick={() => onPick(s.key, rooms)}
          >
            <span className="palette" aria-hidden="true">
              {s.palette.map((c) => (
                <span key={c} style={{ background: c }} />
              ))}
            </span>
            <b>{s.title}</b>
            <span className="muted small">{s.about}</span>
          </button>
        ))}
      </div>
      {current && <p className="muted small">Сменить стиль можно в любой момент — то, что вы выбрали сами, останется.</p>}
    </section>
  );
}

function PlanAndRooms({
  info,
  project,
  busy,
  onUpload,
  onSave,
}: {
  info: DesignInfo;
  project: DesignProject;
  busy: boolean;
  onUpload: (file: File) => Promise<void>;
  onSave: (rooms: DesignRoom[]) => Promise<void>;
}) {
  const [rooms, setRooms] = useState<DesignRoom[]>(project.rooms);
  useEffect(() => setRooms(project.rooms), [project.rooms]);
  const dirty = JSON.stringify(rooms) !== JSON.stringify(project.rooms);
  const total = rooms.reduce((s, r) => s + (Number(r.area) || 0), 0);

  function set(i: number, patch: Partial<DesignRoom>) {
    setRooms((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  }
  function add() {
    let n = rooms.length + 1;
    while (rooms.some((r) => r.key === `x${n}`)) n++;
    setRooms([...rooms, { key: `x${n}`, name: "Комната", type: "bedroom", area: 10, height: 2.7, perimeter: null, doors: 1, windows: 1 }]);
  }
  function save(e: FormEvent) {
    e.preventDefault();
    void onSave(rooms.map((r) => ({ ...r, area: Number(r.area), height: Number(r.height) })));
  }

  return (
    <section className="card">
      <h2>2. План и помещения</h2>
      {project.can_edit && (
        <label className="field-title">
          План БТИ (PDF, JPEG или PNG)
          <input
            type="file"
            accept="application/pdf,image/jpeg,image/png"
            disabled={busy}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void onUpload(f);
              e.target.value = "";
            }}
          />
        </label>
      )}
      {project.plan_url ? (
        <p className="small">
          <a href={project.plan_url} target="_blank" rel="noreferrer">
            Загруженный план
          </a>
          {!info.plan_reader && " · помещения проверьте и поправьте вручную"}
        </p>
      ) : (
        <p className="muted small">
          {info.plan_reader
            ? "Загрузите план — помещения и площади прочитаем с экспликации."
            : "Помещения заготовлены по площади квартиры. Поправьте площади по плану БТИ."}
        </p>
      )}
      <form onSubmit={save}>
        <div className="table-wrap">
          <table className="rooms-table">
            <thead>
              <tr>
                <th>Помещение</th>
                <th>Тип</th>
                <th>Площадь, м²</th>
                <th>Высота, м</th>
                <th>Окна</th>
                {project.can_edit && <th aria-label="Удалить" />}
              </tr>
            </thead>
            <tbody>
              {rooms.map((r, i) => (
                <tr key={r.key}>
                  <td>
                    <input aria-label="Название" value={r.name} disabled={!project.can_edit} onChange={(e) => set(i, { name: e.target.value })} />
                  </td>
                  <td>
                    <select aria-label="Тип" value={r.type} disabled={!project.can_edit} onChange={(e) => set(i, { type: e.target.value })}>
                      {Object.entries(info.room_types).map(([k, t]) => (
                        <option key={k} value={k}>
                          {t}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <input
                      aria-label="Площадь"
                      inputMode="decimal"
                      value={String(r.area)}
                      disabled={!project.can_edit}
                      onChange={(e) => set(i, { area: e.target.value.replace(",", ".") as unknown as number })}
                    />
                  </td>
                  <td>
                    <input
                      aria-label="Высота"
                      inputMode="decimal"
                      value={String(r.height)}
                      disabled={!project.can_edit}
                      onChange={(e) => set(i, { height: e.target.value.replace(",", ".") as unknown as number })}
                    />
                  </td>
                  <td>
                    <input
                      aria-label="Окна"
                      type="number"
                      min={0}
                      max={10}
                      value={r.windows}
                      disabled={!project.can_edit}
                      onChange={(e) => set(i, { windows: Number(e.target.value) })}
                    />
                  </td>
                  {project.can_edit && (
                    <td>
                      <button type="button" className="link" disabled={rooms.length < 2} onClick={() => setRooms(rooms.filter((_, j) => j !== i))}>
                        Убрать
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted small">Всего {qty(total.toFixed(1))} м²</p>
        {project.can_edit && (
          <div className="row">
            <button type="button" className="secondary" onClick={add}>
              Добавить помещение
            </button>
            <button type="submit" disabled={busy || !dirty}>
              Сохранить помещения
            </button>
          </div>
        )}
      </form>
    </section>
  );
}

function Rooms({
  info,
  project,
  catalog,
  busy,
  onChoose,
}: {
  info: DesignInfo;
  project: DesignProject;
  catalog: DesignMaterial[];
  busy: boolean;
  onChoose: (room: string, slot: string, materialId: string | null) => void;
}) {
  const byId = useMemo(() => {
    const m = new Map<string, DesignMaterial>();
    for (const x of [...catalog, ...project.materials]) m.set(x.id, x);
    return m;
  }, [catalog, project.materials]);
  const renders = new Map(project.renders.filter((r) => r.url).map((r) => [r.room_key, r.url as string]));

  return (
    <section className="card">
      <h2>3. Отделка по комнатам</h2>
      <p className="muted small">
        Всё подобрано из нашего каталога — эти же материалы привезёт закупка. Меняйте по вкусу: картинка и стоимость
        пересчитаются сразу.
      </p>
      <div className="design-rooms">
        {project.rooms.map((room) => {
          const sel = project.selections[room.key] ?? {};
          const pinned = project.pinned[room.key] ?? [];
          const pick = (slot: string) => (sel[slot] ? byId.get(sel[slot]) : undefined);
          const render = renders.get(room.key);
          return (
            <article key={room.key} className="boxed design-room">
              <h3>
                {room.name} <span className="muted small">{qty(room.area)} м²</span>
              </h3>
              {render ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img className="room-render" src={render} alt={`Как может выглядеть: ${room.name}`} loading="lazy" />
              ) : (
                <RoomPreview room={room} pick={pick} />
              )}
              <dl className="slots">
                {Object.entries(info.room_slots[room.type] ?? {}).map(([slot, kinds]) => {
                  const options = catalog
                    .filter((m) => kinds.includes(m.kind))
                    .sort((a, b) => Number(b.styles.includes(project.style)) - Number(a.styles.includes(project.style)) || a.price_kop - b.price_kop);
                  const current = pick(slot);
                  return (
                    <div key={slot} className="slot">
                      <dt>
                        <span className="swatch" style={{ background: current?.color ?? "transparent" }} aria-hidden="true" />
                        {info.slots[slot]}
                        {pinned.includes(slot) && <span className="badge">ваш выбор</span>}
                      </dt>
                      <dd>
                        {project.can_edit ? (
                          <select
                            aria-label={`${room.name}: ${info.slots[slot]}`}
                            value={pinned.includes(slot) ? sel[slot] ?? "" : ""}
                            disabled={busy}
                            onChange={(e) => onChoose(room.key, slot, e.target.value || null)}
                          >
                            <option value="">По стилю: {current ? current.title : "нет в каталоге"}</option>
                            {options.map((m) => (
                              <option key={m.id} value={m.id}>
                                {m.title} — {rub(m.price_kop)}/{m.unit}
                                {m.styles.includes(project.style) ? "" : " (другой стиль)"}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <span>{current?.title ?? "—"}</span>
                        )}
                        {current?.brand && <span className="muted small"> {current.brand}</span>}
                      </dd>
                    </div>
                  );
                })}
              </dl>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function EstimateView({ estimate }: { estimate: DesignEstimate }) {
  const [open, setOpen] = useState(false);
  const groups: [string, DesignLine[]][] = [
    ["Чистовые материалы", estimate.lines.filter((l) => l.kind === "material" && l.group === "finish")],
    ["Черновые материалы", estimate.lines.filter((l) => l.kind === "material" && l.group === "rough")],
    ["Работы", estimate.lines.filter((l) => l.kind === "work")],
  ];
  return (
    <section className="card">
      <h2>4. Сколько это будет стоить</h2>
      <p className="estimate-range">
        {roughRub(estimate.low_kop)} – {roughRub(estimate.high_kop)}
      </p>
      <p>
        Материалы: <b>{rub(estimate.materials_kop)}</b> · Работы умного ремонта: <b>{rub(estimate.works_kop)}</b>
      </p>
      <p className="muted small">
        Пол {qty(estimate.floor_area)} м², стены {qty(estimate.wall_area)} м². Это оценка по плану: точную смету составит
        инженер после замера, разброс обычно ±15 %.
      </p>
      <button type="button" className="link" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? "Скрыть расчёт" : "Показать расчёт по позициям"}
      </button>
      {open &&
        groups.map(
          ([title, lines]) =>
            lines.length > 0 && (
              <div key={title}>
                <h3>{title}</h3>
                <ul className="list">
                  {lines.map((l, i) => (
                    <li key={i} className="row between">
                      <span>
                        {l.title}
                        {l.room && <span className="muted small"> · {l.room}</span>}
                        <br />
                        <span className="muted small">
                          {qty(l.qty)} {l.unit}
                          {l.packs ? ` · ${l.packs} × ${l.pack_title ?? "упак."}` : ""} × {rub(l.unit_price_kop)}
                        </span>
                      </span>
                      <b>{rub(l.total_kop)}</b>
                    </li>
                  ))}
                </ul>
              </div>
            ),
        )}
    </section>
  );
}

function Media({
  info,
  project,
  busy,
  onRenders,
  onVideo,
}: {
  info: DesignInfo;
  project: DesignProject;
  busy: boolean;
  onRenders: () => void;
  onVideo: () => void;
}) {
  if (!info.renderer && !info.video) {
    return (
      <section className="card">
        <h2>5. Картинки и ролик</h2>
        <p className="muted small">
          Сейчас комнаты показаны схемой в цветах выбранной отделки. Фотореалистичные картинки и ролик «как это будет
          выглядеть» появятся здесь, когда подключим сервисы.
        </p>
      </section>
    );
  }
  const drawing = project.renders.filter((r) => r.status === "queued" || r.status === "running").length;
  const failed = project.renders.filter((r) => r.status === "failed").length;
  return (
    <section className="card">
      <h2>5. Картинки и ролик</h2>
      {info.renderer && (
        <p>
          {project.can_edit && (
            <button type="button" disabled={busy || drawing > 0} onClick={onRenders}>
              {project.renders.length ? "Нарисовать заново" : "Нарисовать комнаты"}
            </button>
          )}{" "}
          {drawing > 0 && <span className="muted small">Рисуем: осталось {drawing}</span>}
          {failed > 0 && <span className="badge warn">не получилось: {failed}</span>}
        </p>
      )}
      {info.video && (
        <>
          {project.video_status && <p>{VIDEO_STATUS[project.video_status] ?? project.video_status}</p>}
          {project.video_url && (
            <video className="design-video" src={project.video_url} controls preload="metadata" />
          )}
          {project.can_edit && project.video_status !== "processing" && (
            <button type="button" disabled={busy} onClick={onVideo}>
              {project.video_url ? "Заказать новый ролик" : "Заказать ролик"}
            </button>
          )}
        </>
      )}
    </section>
  );
}

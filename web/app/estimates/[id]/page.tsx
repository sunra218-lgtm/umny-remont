"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api, errorText } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { ESTIMATE_STATUS_LABELS, ESTIMATE_TYPE_LABELS, formatDate, parseRub, rub } from "@/lib/format";
import type { Estimate, EstimateItem, EstimateSummary, PriceItem, Stage } from "@/lib/types";

import { ErrorNote, Loading, Shell } from "../../components";

export default function EstimatePage() {
  const { id } = useParams<{ id: string }>();
  const { me, loading } = useRequireAuth();
  const [est, setEst] = useState<Estimate | null>(null);
  const [stages, setStages] = useState<Stage[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api<Estimate>(`/estimates/${id}`);
      setEst(data);
      setStages(await api<Stage[]>(`/objects/${data.object_id}/stages`));
    } catch (err) {
      setError(errorText(err));
    }
  }, [id]);
  useEffect(() => {
    if (me) void load();
  }, [me, load]);

  if (loading || !me) return <Shell><Loading /></Shell>;
  if (!est) return <Shell>{error ? <ErrorNote text={error} /> : <Loading />}</Shell>;

  async function act(fn: () => Promise<Estimate | unknown>) {
    setError(null);
    try {
      await fn();
      await load();
    } catch (err) {
      setError(errorText(err));
    }
  }

  const groups = groupByStage(est.items, stages);
  return (
    <Shell wide>
      <p>
        <Link href={`/objects/${est.object_id}`}>← К объекту</Link>
      </p>
      <div className="row between">
        <h1>
          {ESTIMATE_TYPE_LABELS[est.type]} смета, версия {est.version}
        </h1>
        <span className={`status status-${est.status}`}>{ESTIMATE_STATUS_LABELS[est.status]}</span>
      </div>
      {est.approved_at && <p className="ok">Утверждена {formatDate(est.approved_at)} простой электронной подписью.</p>}
      <ErrorNote text={error} />
      {est.can_approve && <Approve est={est} onDone={load} />}
      {est.can_edit && <Editor est={est} stages={stages} act={act} />}

      <section className="card">
        {est.items.length === 0 ? (
          <p className="empty muted">Позиций пока нет. Добавьте их вручную, из справочника или загрузите Excel.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Наименование</th>
                  <th>Помещение</th>
                  <th className="num">Кол-во</th>
                  <th className="num">Цена</th>
                  <th className="num">Сумма</th>
                  {est.can_edit && <th />}
                </tr>
              </thead>
              {groups.map((g) => (
                <tbody key={g.key}>
                  <tr className="group">
                    <td colSpan={est.can_edit ? 6 : 5}>
                      {g.title} · {rub(g.items.reduce((s, i) => s + i.total_kop, 0))}
                    </td>
                  </tr>
                  {g.items.map((i) => (
                    <ItemRow key={i.id} item={i} editable={est.can_edit} act={act} />
                  ))}
                </tbody>
              ))}
            </table>
          </div>
        )}
        <dl className="totals">
          <dt>Работы</dt>
          <dd className="num">{rub(est.total_work_kop)}</dd>
          <dt>Материалы</dt>
          <dd className="num">{rub(est.total_material_kop)}</dd>
          <dt>Резерв на непредвиденное</dt>
          <dd className="num">{rub(est.reserve_kop)}</dd>
          <dt className="grand">Итого</dt>
          <dd className="num grand">{rub(est.total_kop)}</dd>
        </dl>
      </section>
      <Versions est={est} />
    </Shell>
  );
}

function groupByStage(items: EstimateItem[], stages: Stage[]) {
  const byId = new Map(stages.map((s) => [s.id, s]));
  const groups = new Map<string, { key: string; title: string; seq: number; items: EstimateItem[] }>();
  for (const item of items) {
    const stage = item.stage_id ? byId.get(item.stage_id) : undefined;
    const key = stage?.id ?? "none";
    if (!groups.has(key))
      groups.set(key, { key, title: stage ? `${stage.seq}. ${stage.title}` : "Без этапа", seq: stage?.seq ?? 9999, items: [] });
    groups.get(key)!.items.push(item);
  }
  return [...groups.values()].sort((a, b) => a.seq - b.seq);
}

function ItemRow({ item, editable, act }: { item: EstimateItem; editable: boolean; act: (fn: () => Promise<unknown>) => Promise<void> }) {
  const [qty, setQty] = useState(String(Number(item.qty)));
  const save = () => {
    const value = qty.replace(",", ".");
    if (Number(value) !== Number(item.qty) && Number(value) > 0) void act(() => api(`/estimate-items/${item.id}`, { method: "PATCH", body: { qty: value } }));
  };
  return (
    <tr>
      <td>
        {item.title}
        {item.category === "material" && <span className="badge">материал</span>}
      </td>
      <td>{item.room ?? ""}</td>
      <td className="num">
        {editable ? (
          <input aria-label={`Количество: ${item.title}`} style={{ width: 96, minHeight: 36 }} inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} onBlur={save} />
        ) : (
          Number(item.qty).toLocaleString("ru-RU")
        )}{" "}
        {item.unit}
      </td>
      <td className="num">{rub(item.unit_price_kop)}</td>
      <td className="num">{rub(item.total_kop)}</td>
      {editable && (
        <td className="end">
          <button className="secondary small" onClick={() => void act(() => api(`/estimate-items/${item.id}`, { method: "DELETE" }))}>
            Удалить
          </button>
        </td>
      )}
    </tr>
  );
}

function Editor({ est, stages, act }: { est: Estimate; stages: Stage[]; act: (fn: () => Promise<unknown>) => Promise<void> }) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<PriceItem[]>([]);
  const [picked, setPicked] = useState<PriceItem | null>(null);
  const [manual, setManual] = useState({ category: "work", title: "", unit: "", price: "" });
  const [qty, setQty] = useState("");
  const [stageId, setStageId] = useState("");
  const [room, setRoom] = useState("");
  const [reserve, setReserve] = useState(String(est.reserve_kop / 100));
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState("replace");

  useEffect(() => {
    if (picked || q.trim().length < 2) {
      setFound([]);
      return;
    }
    const t = setTimeout(() => {
      void api<PriceItem[]>(`/price-items?q=${encodeURIComponent(q.trim())}`).then((r) => setFound(r.slice(0, 8)), () => setFound([]));
    }, 250);
    return () => clearTimeout(t);
  }, [q, picked]);

  function addItem(e: FormEvent) {
    e.preventDefault();
    const base = { qty: qty.replace(",", "."), stage_id: stageId || null, room: room || null };
    const body = picked
      ? { ...base, price_item_id: picked.id }
      : { ...base, category: manual.category, title: manual.title || q, unit: manual.unit, unit_price_kop: parseRub(manual.price) };
    void act(() => api(`/estimates/${est.id}/items`, { method: "POST", body })).then(() => {
      setPicked(null);
      setQ("");
      setQty("");
      setManual({ category: "work", title: "", unit: "", price: "" });
    });
  }

  function upload(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    void act(() => api(`/estimates/${est.id}/import-xlsx?mode=${mode}`, { form })).then(() => setFile(null));
  }

  return (
    <section className="card">
      <h2>Составление сметы</h2>
      <form onSubmit={addItem} className="grid2 boxed">
        <div style={{ gridColumn: "1 / -1" }}>
          <label htmlFor="item-q">Позиция: найдите в справочнике или впишите свою</label>
          <input
            id="item-q"
            value={picked ? `${picked.title} — ${rub(picked.unit_price_kop)} за ${picked.unit}` : q}
            onChange={(e) => {
              setPicked(null);
              setQ(e.target.value);
            }}
            placeholder="Например, штукатурка"
            autoComplete="off"
          />
          {found.length > 0 && (
            <ul className="list suggest">
              {found.map((p) => (
                <li key={p.id}>
                  <button type="button" className="link" onClick={() => setPicked(p)}>
                    {p.title}
                  </button>
                  <span className="muted">
                    {rub(p.unit_price_kop)} / {p.unit}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
        {!picked && (
          <>
            <div>
              <label htmlFor="item-cat">Тип</label>
              <select id="item-cat" value={manual.category} onChange={(e) => setManual({ ...manual, category: e.target.value })}>
                <option value="work">Работа</option>
                <option value="material">Материал</option>
              </select>
            </div>
            <div>
              <label htmlFor="item-unit">Единица</label>
              <input id="item-unit" value={manual.unit} onChange={(e) => setManual({ ...manual, unit: e.target.value })} placeholder="м², шт, м.п." />
            </div>
            <div>
              <label htmlFor="item-price">Цена за единицу, ₽</label>
              <input id="item-price" inputMode="decimal" value={manual.price} onChange={(e) => setManual({ ...manual, price: e.target.value })} />
            </div>
          </>
        )}
        <div>
          <label htmlFor="item-qty">Количество</label>
          <input id="item-qty" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} />
        </div>
        <div>
          <label htmlFor="item-stage">Этап</label>
          <select id="item-stage" value={stageId} onChange={(e) => setStageId(e.target.value)}>
            <option value="">{est.type === "full" ? "Выберите этап" : "Без этапа"}</option>
            {stages.map((s) => (
              <option key={s.id} value={s.id}>
                {s.seq}. {s.title}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="item-room">Помещение (необязательно)</label>
          <input id="item-room" value={room} onChange={(e) => setRoom(e.target.value)} placeholder="Кухня" />
        </div>
        <div className="row end">
          <button type="submit" className="primary" disabled={!qty || (!picked && (!q.trim() || !manual.unit || parseRub(manual.price) === null))}>
            Добавить позицию
          </button>
        </div>
      </form>

      <form onSubmit={upload} className="grid2 boxed">
        <div>
          <label htmlFor="xlsx">Загрузить из Excel (.xlsx)</label>
          <input id="xlsx" type="file" accept=".xlsx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} style={{ paddingTop: 10 }} />
        </div>
        <div>
          <label htmlFor="xlsx-mode">Что сделать с текущими позициями</label>
          <select id="xlsx-mode" value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="replace">Заменить позициями из файла</option>
            <option value="append">Добавить к ним</option>
          </select>
        </div>
        <div className="row">
          <button type="submit" className="secondary" disabled={!file}>
            Загрузить файл
          </button>
          <a href="/api/v1/estimates/import-template.xlsx">Скачать шаблон</a>
        </div>
      </form>

      <div className="grid2">
        <div>
          <label htmlFor="reserve">Резерв на непредвиденные расходы, ₽</label>
          <input
            id="reserve"
            inputMode="decimal"
            value={reserve}
            onChange={(e) => setReserve(e.target.value)}
            onBlur={() => {
              const kop = parseRub(reserve);
              if (kop !== null && kop !== est.reserve_kop) void act(() => api(`/estimates/${est.id}`, { method: "PATCH", body: { reserve_kop: kop } }));
            }}
          />
        </div>
        <div className="row end">
          <button className="primary" disabled={est.items.length === 0} onClick={() => void act(() => api(`/estimates/${est.id}/submit`, { method: "POST" }))}>
            Отправить заказчику
          </button>
        </div>
      </div>
      <p className="muted small">После отправки версию нельзя изменить: правки делаются в новой версии.</p>
    </section>
  );
}

function Approve({ est, onDone }: { est: Estimate; onDone: () => Promise<void> }) {
  const [sig, setSig] = useState<{ signature_id: string; debug_code: string | null } | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function request() {
    setError(null);
    setBusy(true);
    try {
      setSig(await api(`/estimates/${est.id}/approve/request`, { method: "POST" }));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function confirm(e: FormEvent) {
    e.preventDefault();
    if (!sig) return;
    setError(null);
    setBusy(true);
    try {
      await api(`/estimates/${est.id}/approve`, { method: "POST", body: { signature_id: sig.signature_id, code } });
      await onDone();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Утверждение сметы</h2>
      <p>
        Проверьте позиции ниже. Утверждая смету на <b>{rub(est.total_kop)}</b>, вы подписываете её простой электронной подписью:
        код придёт в СМС.
      </p>
      {sig ? (
        <form onSubmit={confirm} className="row">
          {sig.debug_code && <p className="devnote" style={{ width: "100%" }}>Режим разработки: код — <b>{sig.debug_code}</b></p>}
          <div style={{ flex: "1 1 160px" }}>
            <label htmlFor="sign-code">Код из СМС</label>
            <input id="sign-code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
          </div>
          <button type="submit" className="primary" style={{ alignSelf: "flex-end" }} disabled={busy || code.length !== 6}>
            Подписать и утвердить
          </button>
        </form>
      ) : (
        <button className="primary" disabled={busy} onClick={() => void request()}>
          Получить код для подписи
        </button>
      )}
      <ErrorNote text={error} />
    </section>
  );
}

function Versions({ est }: { est: Estimate }) {
  const [list, setList] = useState<EstimateSummary[]>([]);
  useEffect(() => {
    void api<EstimateSummary[]>(`/estimates/${est.id}/versions`).then(setList, () => setList([]));
  }, [est.id, est.status]);
  if (list.length < 2) return null;
  return (
    <section className="card">
      <h2>Все версии</h2>
      <ul className="list">
        {list.map((v) => (
          <li key={v.id}>
            {v.id === est.id ? <b>Версия {v.version} (открыта)</b> : <Link href={`/estimates/${v.id}`}>Версия {v.version}</Link>}
            <span className="muted">
              {rub(v.total_kop)} · {ESTIMATE_STATUS_LABELS[v.status]}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

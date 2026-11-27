"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api, errorText } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { formatDate } from "@/lib/format";
import type { Me, TechCard, TechCardBrief, TechMaterial, TechStep } from "@/lib/types";

import { ErrorNote, Loading, Shell } from "../components";
import { TechCardView } from "../techcard";

function canEdit(me: Me): boolean {
  return me.is_platform_admin || me.memberships.some((m) => m.org_type === "platform" && m.role === "inspector");
}

/** База технологических карт: допуски по нормам, шаги, материалы. */
export default function TechCardsPage() {
  const { me, loading } = useRequireAuth();
  const [cards, setCards] = useState<TechCardBrief[] | null>(null);
  const [card, setCard] = useState<TechCard | null>(null);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setCards(await api<TechCardBrief[]>("/tech-cards"));
    } catch (err) {
      setError(errorText(err));
    }
  }, []);
  useEffect(() => {
    if (me) void load();
  }, [me, load]);

  async function open(stageType: string) {
    setError(null);
    setEditing(false);
    try {
      setCard(await api<TechCard>(`/tech-cards/${stageType}`));
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function verify() {
    if (!card) return;
    try {
      setCard(await api<TechCard>(`/tech-cards/${card.stage_type}/verify`, { method: "POST" }));
      await load();
    } catch (err) {
      setError(errorText(err));
    }
  }

  if (loading || !me) return <Shell><Loading /></Shell>;
  const editor = canEdit(me);
  return (
    <Shell wide>
      <h1>Технологические карты</h1>
      <p className="muted">
        Как делать каждый вид работ и какие допуски по нормам. Из карт собираются чек-листы фото для бригады и пункты проверки
        инженера на стоп-точках.
      </p>
      <ErrorNote text={error} />
      <div className="split">
        <div>
          <section className="card">
            <h2>Виды работ</h2>
            {cards === null ? (
              <Loading />
            ) : (
              <ul className="list">
                {cards.map((c) => (
                  <li key={c.stage_type}>
                    <button type="button" className="link" onClick={() => void open(c.stage_type)}>
                      {c.title}
                    </button>
                    <span className="muted small">
                      {c.steps} шагов · {c.verified_at ? `проверено ${formatDate(c.verified_at)}` : "ждёт проверки инженера"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
        <div>
          {card && !editing && (
            <>
              {editor && (
                <div className="row">
                  <button type="button" className="secondary" onClick={() => setEditing(true)}>
                    Изменить карту
                  </button>
                  {!card.verified_at && (
                    <button type="button" className="primary" onClick={() => void verify()}>
                      Отметить: проверено инженером
                    </button>
                  )}
                </div>
              )}
              <TechCardView card={card} title={card.title} />
              {card.materials.length > 0 && (
                <section className="card">
                  <h2>Материалы и расход</h2>
                  <ul className="list">
                    {card.materials.map((m) => (
                      <li key={m.key}>
                        <div>
                          <b>{m.name}</b>
                          <div className="muted small">
                            {m.rate} {m.unit} на 1 {m.per}
                            {m.per_mm && ` на 1 мм слоя (по умолчанию ${m.thickness_mm} мм)`}, запас {m.reserve_pct} %
                            {m.pack_title && ` · ${m.pack_title}`}
                          </div>
                          {m.note && <div className="muted small">{m.note}</div>}
                        </div>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </>
          )}
          {card && editing && (
            <CardEditor
              card={card}
              onSaved={async (c) => {
                setCard(c);
                setEditing(false);
                await load();
              }}
              onCancel={() => setEditing(false)}
            />
          )}
          {!card && <p className="muted">Выберите вид работ слева.</p>}
        </div>
      </div>
    </Shell>
  );
}

const lines = (text: string) =>
  text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);

function CardEditor({ card, onSaved, onCancel }: { card: TechCard; onSaved: (c: TechCard) => Promise<void>; onCancel: () => void }) {
  const [title, setTitle] = useState(card.title);
  const [summary, setSummary] = useState(card.summary ?? "");
  const [norms, setNorms] = useState(card.norms.join("\n"));
  const [steps, setSteps] = useState<TechStep[]>(card.steps);
  const [materials, setMaterials] = useState<TechMaterial[]>(card.materials);
  const [error, setError] = useState<string | null>(null);

  const setStep = (n: number, patch: Partial<TechStep>) => setSteps(steps.map((s, i) => (i === n ? { ...s, ...patch } : s)));
  const setMat = (n: number, patch: Partial<TechMaterial>) => setMaterials(materials.map((m, i) => (i === n ? { ...m, ...patch } : m)));
  const newKey = (prefix: string, taken: string[]) => {
    let i = taken.length + 1;
    while (taken.includes(`${prefix}${i}`)) i += 1;
    return `${prefix}${i}`;
  };

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const body = {
        title,
        summary: summary || null,
        norms: lines(norms),
        steps: steps.map((s) => ({ ...s, tolerance: s.tolerance || null, check: s.check || null, photo: s.photo || null })),
        materials: materials.map((m) => ({ ...m, thickness_mm: m.per_mm ? m.thickness_mm : null, note: m.note || null, pack_title: m.pack_title || null })),
      };
      await onSaved(await api<TechCard>(`/tech-cards/${card.stage_type}`, { method: "PUT", body }));
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <form className="card" onSubmit={save}>
      <h2>Правка: {card.title}</h2>
      <p className="devnote">После сохранения отметка «проверено инженером» снимается, чек-лист фото пересобирается из шагов.</p>
      <label htmlFor="tc-title">Название</label>
      <input id="tc-title" value={title} onChange={(e) => setTitle(e.target.value)} />
      <label htmlFor="tc-summary">Коротко для заказчика</label>
      <textarea id="tc-summary" rows={2} value={summary} onChange={(e) => setSummary(e.target.value)} />
      <label htmlFor="tc-norms">Нормы (каждая с новой строки)</label>
      <textarea id="tc-norms" rows={3} value={norms} onChange={(e) => setNorms(e.target.value)} />

      <h3>Шаги</h3>
      {steps.map((s, n) => (
        <fieldset key={s.key} className="boxed">
          <legend>
            Шаг {n + 1}: {s.title || "новый"}
          </legend>
          <label htmlFor={`s-t-${n}`}>Название</label>
          <input id={`s-t-${n}`} value={s.title} onChange={(e) => setStep(n, { title: e.target.value })} />
          <label htmlFor={`s-h-${n}`}>Как делать</label>
          <textarea id={`s-h-${n}`} rows={3} value={s.how} onChange={(e) => setStep(n, { how: e.target.value })} />
          <label htmlFor={`s-tol-${n}`}>Допуск</label>
          <textarea id={`s-tol-${n}`} rows={2} value={s.tolerance ?? ""} onChange={(e) => setStep(n, { tolerance: e.target.value })} />
          <label htmlFor={`s-c-${n}`}>Как проверить</label>
          <input id={`s-c-${n}`} value={s.check ?? ""} onChange={(e) => setStep(n, { check: e.target.value })} />
          <label htmlFor={`s-m-${n}`}>Частые ошибки (каждая с новой строки)</label>
          <textarea id={`s-m-${n}`} rows={2} value={s.mistakes.join("\n")} onChange={(e) => setStep(n, { mistakes: lines(e.target.value) })} />
          <label htmlFor={`s-p-${n}`}>Ракурс фото (пусто — фото не нужно)</label>
          <input id={`s-p-${n}`} value={s.photo ?? ""} onChange={(e) => setStep(n, { photo: e.target.value })} />
          <label className="check">
            <input type="checkbox" checked={s.hidden} onChange={(e) => setStep(n, { hidden: e.target.checked })} />
            <span>Скрытые работы</span>
          </label>
          <label className="check">
            <input type="checkbox" checked={s.inspector} onChange={(e) => setStep(n, { inspector: e.target.checked })} />
            <span>Проверяет инженер на стоп-точке</span>
          </label>
          <div className="row">
            <button type="button" className="secondary small" disabled={n === 0} onClick={() => setSteps(steps.map((x, i) => (i === n - 1 ? steps[n] : i === n ? steps[n - 1] : x)))}>
              Выше
            </button>
            <button type="button" className="secondary small" disabled={steps.length === 1} onClick={() => setSteps(steps.filter((_, i) => i !== n))}>
              Убрать шаг
            </button>
          </div>
        </fieldset>
      ))}
      <button
        type="button"
        className="secondary"
        onClick={() =>
          setSteps([
            ...steps,
            { key: newKey("step", steps.map((s) => s.key)), title: "", how: "", tolerance: null, check: null, mistakes: [], photo: null, hidden: false, inspector: false },
          ])
        }
      >
        Добавить шаг
      </button>

      <h3>Материалы</h3>
      {materials.map((m, n) => (
        <fieldset key={m.key} className="boxed grid2">
          <div>
            <label htmlFor={`m-n-${n}`}>Материал</label>
            <input id={`m-n-${n}`} value={m.name} onChange={(e) => setMat(n, { name: e.target.value })} />
          </div>
          <div>
            <label htmlFor={`m-r-${n}`}>Расход, {m.unit} на 1 {m.per}{m.per_mm ? " на 1 мм" : ""}</label>
            <input id={`m-r-${n}`} type="number" step="0.01" min="0" value={m.rate} onChange={(e) => setMat(n, { rate: Number(e.target.value) })} />
          </div>
          <div>
            <label htmlFor={`m-u-${n}`}>Единица</label>
            <input id={`m-u-${n}`} value={m.unit} onChange={(e) => setMat(n, { unit: e.target.value })} />
          </div>
          <div>
            <label htmlFor={`m-p-${n}`}>На что</label>
            <select id={`m-p-${n}`} value={m.per} onChange={(e) => setMat(n, { per: e.target.value })}>
              {["м²", "м.п.", "шт", "м³"].map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </div>
          <label className="check">
            <input type="checkbox" checked={m.per_mm} onChange={(e) => setMat(n, { per_mm: e.target.checked, thickness_mm: m.thickness_mm ?? 10 })} />
            <span>Расход на 1 мм толщины</span>
          </label>
          {m.per_mm && (
            <div>
              <label htmlFor={`m-th-${n}`}>Толщина слоя по умолчанию, мм</label>
              <input id={`m-th-${n}`} type="number" min="0.1" step="0.1" value={m.thickness_mm ?? ""} onChange={(e) => setMat(n, { thickness_mm: Number(e.target.value) })} />
            </div>
          )}
          <div>
            <label htmlFor={`m-pk-${n}`}>Фасовка ({m.unit})</label>
            <input id={`m-pk-${n}`} type="number" min="0" step="0.1" value={m.pack ?? ""} onChange={(e) => setMat(n, { pack: e.target.value ? Number(e.target.value) : null })} />
          </div>
          <div>
            <label htmlFor={`m-pt-${n}`}>Как называется упаковка</label>
            <input id={`m-pt-${n}`} value={m.pack_title ?? ""} placeholder="мешок 30 кг" onChange={(e) => setMat(n, { pack_title: e.target.value })} />
          </div>
          <div>
            <label htmlFor={`m-rs-${n}`}>Запас, %</label>
            <input id={`m-rs-${n}`} type="number" min="0" max="100" value={m.reserve_pct} onChange={(e) => setMat(n, { reserve_pct: Number(e.target.value) })} />
          </div>
          <div className="row end">
            <button type="button" className="secondary small" onClick={() => setMaterials(materials.filter((_, i) => i !== n))}>
              Убрать материал
            </button>
          </div>
        </fieldset>
      ))}
      <button
        type="button"
        className="secondary"
        onClick={() =>
          setMaterials([
            ...materials,
            { key: newKey("mat", materials.map((m) => m.key)), name: "", rate: 1, per: "м²", unit: "кг", per_mm: false, thickness_mm: null, pack: null, pack_title: null, reserve_pct: 10, note: null },
          ])
        }
      >
        Добавить материал
      </button>
      <div className="row end">
        <button type="button" className="secondary" onClick={onCancel}>
          Отмена
        </button>
        <button type="submit" className="primary">
          Сохранить карту
        </button>
      </div>
      <ErrorNote text={error} />
    </form>
  );
}

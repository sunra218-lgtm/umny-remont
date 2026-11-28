"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api, errorText } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import {
  ASSIGNMENT_LABELS,
  ESTIMATE_STATUS_LABELS,
  ESTIMATE_TYPE_LABELS,
  formatDate,
  formatPhone,
  FRAUD_FLAG_LABELS,
  OBJECT_STATUS_LABELS,
  OBJECT_STEPS,
  parseRub,
  personName,
  rub,
  REQUEST_STATUS_LABELS,
  REVIEW_LABELS,
  SERVICE_STATUS_LABELS,
  SERVICE_TYPE_LABELS,
  STAGE_STATUS_LABELS,
  STAGE_TYPE_LABELS,
} from "@/lib/format";
import type {
  Assignment,
  EstimateSummary,
  MaterialRequest,
  MediaItem,
  Me,
  Member,
  Org,
  Page,
  PurchaseOrder,
  RepairObject,
  ServiceOrder,
  Stage,
} from "@/lib/types";

import { ErrorNote, Loading, Shell } from "../../components";
import { Cameras } from "../../cameras";
import { ObjectMarket } from "../../market";
import { DesignEntry } from "../../design";
import { LuxSection } from "../../lux";
import { CuratorSection } from "../../soul";
import { DailyPlans } from "../../daily";
import { BasketForm, OrderCard } from "../../orders";

export default function ObjectPage() {
  const { id } = useParams<{ id: string }>();
  const { me, loading } = useRequireAuth();
  const [obj, setObj] = useState<RepairObject | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setObj(await api<RepairObject>(`/objects/${id}`));
    } catch (err) {
      setError(errorText(err));
    }
  }, [id]);
  useEffect(() => {
    if (me) void load();
  }, [me, load]);

  if (loading || !me) return <Shell><Loading /></Shell>;
  if (!obj) return <Shell>{error ? <ErrorNote text={error} /> : <Loading />}</Shell>;

  const stepIndex = OBJECT_STEPS.indexOf(obj.status);
  return (
    <Shell wide>
      <p>
        <Link href="/objects">← Все объекты</Link>
      </p>
      <h1>{obj.address_text}</h1>
      {obj.status === "cancelled" || obj.status === "dispute" ? (
        <p>
          <span className={`status status-${obj.status}`}>{OBJECT_STATUS_LABELS[obj.status]}</span>
        </p>
      ) : (
        <ol className="steps" aria-label="Этапы объекта">
          {OBJECT_STEPS.map((s, i) => (
            <li key={s} className={i < stepIndex ? "done" : i === stepIndex ? "current" : undefined} aria-current={i === stepIndex ? "step" : undefined}>
              {OBJECT_STATUS_LABELS[s]}
            </li>
          ))}
        </ol>
      )}
      <ErrorNote text={error} />
      <div className="split">
        <div>
          <DesignEntry obj={obj} />
          <ObjectMarket obj={obj} onChanged={load} />
          <ServiceOrders obj={obj} me={me} onObjectChanged={load} />
          <Estimates obj={obj} />
          <Stages obj={obj} />
          <ObjectDays obj={obj} />
          <Materials obj={obj} />
          <Feed obj={obj} />
        </div>
        <div>
          <ObjectInfo obj={obj} onChanged={load} />
          <LuxSection obj={obj} onChanged={load} />
          <CuratorSection obj={obj} />
          <Team obj={obj} me={me} />
          <Cameras obj={obj} />
        </div>
      </div>
    </Shell>
  );
}

function ObjectInfo({ obj, onChanged }: { obj: RepairObject; onChanged: () => Promise<void> }) {
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (obj.access.is_admin)
      void api<Page<Org>>("/organizations?type=contractor&limit=200").then((p) => setOrgs(p.items.filter((o) => o.verified_at)));
  }, [obj.access.is_admin]);

  async function setContractor(orgId: string) {
    setError(null);
    try {
      await api(`/objects/${obj.id}/contractor`, { method: "PUT", body: { contractor_org_id: orgId || null } });
      await onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function cancel() {
    setError(null);
    try {
      await api(`/objects/${obj.id}/cancel`, { method: "POST" });
      await onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  }

  const canCancel = (obj.access.is_customer || obj.access.is_admin) && ["draft", "survey", "estimating", "contracting"].includes(obj.status);
  return (
    <section className="card">
      <h2>Объект</h2>
      <dl className="facts">
        <dt>Площадь</dt>
        <dd>{obj.area_m2 ? `${obj.area_m2.replace(".", ",")} м²` : "не указана"}</dd>
        {obj.customer && !obj.access.is_customer && (
          <>
            <dt>Заказчик</dt>
            <dd>
              {personName(obj.customer)}
              {obj.customer.full_name ? `, ${formatPhone(obj.customer.phone)}` : ""}
            </dd>
          </>
        )}
        <dt>Подрядчик</dt>
        <dd>
          {obj.access.is_admin ? (
            <select aria-label="Подрядчик" value={obj.contractor_org_id ?? ""} onChange={(e) => void setContractor(e.target.value)}>
              <option value="">Не назначен</option>
              {orgs.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.legal_name}
                </option>
              ))}
            </select>
          ) : (
            obj.contractor_name ?? "Подберём после замера"
          )}
        </dd>
      </dl>
      {canCancel && (
        <button className="danger small" style={{ marginTop: 16 }} onClick={() => void cancel()}>
          Отменить ремонт по этому адресу
        </button>
      )}
      <ErrorNote text={error} />
    </section>
  );
}

function platformOrgId(me: Me): string | undefined {
  return me.memberships.find((m) => m.org_type === "platform")?.org_id;
}

function ServiceOrders({ obj, me, onObjectChanged }: { obj: RepairObject; me: Me; onObjectChanged: () => Promise<void> }) {
  const [orders, setOrders] = useState<ServiceOrder[] | null>(null);
  const [inspectors, setInspectors] = useState<Member[]>([]);
  const [ordering, setOrdering] = useState<"survey" | "acceptance" | null>(null);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setOrders(await api<ServiceOrder[]>(`/objects/${obj.id}/service-orders`));
    } catch (err) {
      setError(errorText(err));
    }
  }, [obj.id]);
  useEffect(() => void load(), [load]);
  useEffect(() => {
    const pid = platformOrgId(me);
    if (obj.access.is_admin && pid)
      void api<Member[]>(`/organizations/${pid}/members`).then((m) => setInspectors(m.filter((x) => x.role === "inspector")));
  }, [obj.access.is_admin, me]);

  async function run(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      await load();
      await onObjectChanged();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function order(e: FormEvent) {
    e.preventDefault();
    if (!ordering) return;
    await run(() => api(`/objects/${obj.id}/service-orders`, { method: "POST", body: { type: ordering, comment: comment || null } }));
    setOrdering(null);
    setComment("");
  }

  const active = (type: string) => orders?.some((o) => o.type === type && ["created", "scheduled", "paid"].includes(o.status));
  const canSurvey = obj.access.can_order_services && ["draft", "survey"].includes(obj.status) && !active("survey");
  const canAcceptance = obj.access.can_order_services && !["cancelled", "completed"].includes(obj.status) && !active("acceptance");

  return (
    <section className="card">
      <h2>Выезды специалиста</h2>
      {orders === null ? (
        <Loading />
      ) : (
        <>
          {orders.length === 0 && obj.access.is_customer && (
            <p className="muted">
              Начните с замера: инженер приедет, снимет размеры и сделает фото. По замеру составим полную смету. Если квартира
              ещё не принята у застройщика, закажите «Умную приёмку»: проверим квартиру перед подписанием акта.
            </p>
          )}
          <ul className="list">
            {orders.map((o) => (
              <OrderRow key={o.id} order={o} obj={obj} me={me} inspectors={inspectors} run={run} />
            ))}
          </ul>
        </>
      )}
      {ordering ? (
        <form onSubmit={order} className="boxed">
          <label htmlFor="so-comment">
            {SERVICE_TYPE_LABELS[ordering]}: когда вам удобно и как попасть в квартиру (необязательно)
          </label>
          <textarea id="so-comment" value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Например: будни после 18:00, домофон 10" />
          <div className="row" style={{ marginTop: 12 }}>
            <button type="submit" className="primary">
              Заказать {ordering === "survey" ? "замер" : "приёмку"}
            </button>
            <button type="button" className="secondary" onClick={() => setOrdering(null)}>
              Отменить
            </button>
          </div>
        </form>
      ) : (
        (canSurvey || canAcceptance) && (
          <div className="row" style={{ marginTop: 16 }}>
            {canSurvey && (
              <button className="primary" onClick={() => setOrdering("survey")}>
                Заказать замер
              </button>
            )}
            {canAcceptance && (
              <button className="secondary" onClick={() => setOrdering("acceptance")}>
                Заказать приёмку
              </button>
            )}
          </div>
        )
      )}
      <ErrorNote text={error} />
    </section>
  );
}

function OrderRow({
  order,
  obj,
  me,
  inspectors,
  run,
}: {
  order: ServiceOrder;
  obj: RepairObject;
  me: Me;
  inspectors: Member[];
  run: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const [scheduling, setScheduling] = useState(false);
  const [inspectorId, setInspectorId] = useState(order.inspector?.id ?? "");
  const [slot, setSlot] = useState(order.slot_at ? order.slot_at.slice(0, 16) : "");
  const [price, setPrice] = useState(String(order.price_kop / 100));
  const [finishing, setFinishing] = useState(false);
  const [notes, setNotes] = useState("");
  const isAdmin = obj.access.is_admin;
  const isInspector = order.inspector?.id === me.id;

  return (
    <li style={{ display: "block" }}>
      <div className="row between">
        <div>
          <b>{SERVICE_TYPE_LABELS[order.type]}</b> · {rub(order.price_kop)}
          <div className="muted">
            {order.slot_at ? `${formatDate(order.slot_at)}` : "Время согласуем по телефону"}
            {order.inspector ? ` · инженер ${personName(order.inspector)}` : ""}
          </div>
        </div>
        <span className={`status status-${order.status}`}>{SERVICE_STATUS_LABELS[order.status]}</span>
      </div>
      {order.customer_comment && <p className="muted small">Пожелания: {order.customer_comment}</p>}
      {order.result_notes && <p>Итог выезда: {order.result_notes}</p>}
      <div className="row" style={{ marginTop: 8 }}>
        {isAdmin && ["created", "scheduled", "paid"].includes(order.status) && !scheduling && (
          <button className="secondary small" onClick={() => setScheduling(true)}>
            {order.inspector ? "Перенести" : "Назначить инженера"}
          </button>
        )}
        {isAdmin && order.status === "scheduled" && (
          <button className="secondary small" onClick={() => void run(() => api(`/service-orders/${order.id}/mark-paid`, { method: "POST" }))}>
            Отметить оплату
          </button>
        )}
        {(isAdmin || isInspector) && order.status === "paid" && !finishing && (
          <button className="primary small" onClick={() => setFinishing(true)}>
            Завершить выезд
          </button>
        )}
        {(obj.access.is_customer || isAdmin) && ["created", "scheduled"].includes(order.status) && (
          <button className="secondary small" onClick={() => void run(() => api(`/service-orders/${order.id}/cancel`, { method: "POST" }))}>
            Отменить {order.type === "survey" ? "замер" : "приёмку"}
          </button>
        )}
      </div>
      {scheduling && (
        <form
          className="grid2 boxed"
          onSubmit={(e) => {
            e.preventDefault();
            const priceKop = parseRub(price);
            void run(() =>
              api(`/service-orders/${order.id}/schedule`, {
                method: "POST",
                body: { inspector_id: inspectorId, slot_at: new Date(slot).toISOString(), price_kop: priceKop },
              }),
            ).then(() => setScheduling(false));
          }}
        >
          <div>
            <label htmlFor={`insp-${order.id}`}>Инженер</label>
            <select id={`insp-${order.id}`} value={inspectorId} onChange={(e) => setInspectorId(e.target.value)}>
              <option value="">Выберите</option>
              {inspectors.map((m) => (
                <option key={m.user_id} value={m.user_id}>
                  {personName(m)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor={`slot-${order.id}`}>Дата и время</label>
            <input id={`slot-${order.id}`} type="datetime-local" value={slot} onChange={(e) => setSlot(e.target.value)} />
          </div>
          <div>
            <label htmlFor={`price-${order.id}`}>Цена, ₽</label>
            <input id={`price-${order.id}`} inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} disabled={!!order.paid_at} />
          </div>
          <div className="row end">
            <button type="submit" className="primary" disabled={!inspectorId || !slot}>
              Сохранить выезд
            </button>
            <button type="button" className="secondary" onClick={() => setScheduling(false)}>
              Отменить
            </button>
          </div>
          {inspectors.length === 0 && (
            <p className="muted" style={{ gridColumn: "1 / -1" }}>
              Инженеров пока нет: добавьте их в организацию платформы в админке.
            </p>
          )}
        </form>
      )}
      {finishing && (
        <form
          className="boxed"
          onSubmit={(e) => {
            e.preventDefault();
            void run(() => api(`/service-orders/${order.id}/complete`, { method: "POST", body: { result_notes: notes } })).then(() =>
              setFinishing(false),
            );
          }}
        >
          <label htmlFor={`notes-${order.id}`}>Итог выезда</label>
          <textarea id={`notes-${order.id}`} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Что сделано, что нашли, на что обратить внимание" />
          <div className="row" style={{ marginTop: 12 }}>
            <button type="submit" className="primary" disabled={notes.trim().length < 3}>
              Сохранить итог
            </button>
            <button type="button" className="secondary" onClick={() => setFinishing(false)}>
              Отменить
            </button>
          </div>
        </form>
      )}
    </li>
  );
}

function Estimates({ obj }: { obj: RepairObject }) {
  const [list, setList] = useState<EstimateSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setList(await api<EstimateSummary[]>(`/objects/${obj.id}/estimates`));
    } catch (err) {
      setError(errorText(err));
    }
  }, [obj.id]);
  useEffect(() => void load(), [load, obj.status]);

  async function create(type: "preliminary" | "full") {
    setError(null);
    try {
      const est = await api<EstimateSummary>(`/objects/${obj.id}/estimates`, { method: "POST", body: { type } });
      location.assign(`/estimates/${est.id}`);
    } catch (err) {
      setError(errorText(err));
    }
  }

  const editor = obj.access.can_edit_estimates;
  return (
    <section className="card">
      <h2>Сметы</h2>
      {list === null ? (
        <Loading />
      ) : list.length === 0 ? (
        <p className="muted">
          {editor ? "Смет пока нет." : "Смета появится здесь, когда сметчик её подготовит. Утвердить её можно будет кодом из СМС."}
        </p>
      ) : (
        <ul className="list">
          {list.map((e) => (
            <li key={e.id}>
              <div>
                <Link href={`/estimates/${e.id}`}>
                  {ESTIMATE_TYPE_LABELS[e.type]} смета, версия {e.version}
                </Link>
                <div className="muted">{rub(e.total_kop)}</div>
              </div>
              <span className={`status status-${e.status}`}>{ESTIMATE_STATUS_LABELS[e.status]}</span>
            </li>
          ))}
        </ul>
      )}
      {editor && (
        <div className="row" style={{ marginTop: 16 }}>
          {["draft", "survey", "estimating"].includes(obj.status) && (
            <button className="secondary" onClick={() => void create("preliminary")}>
              Новая версия предварительной
            </button>
          )}
          {["estimating", "contracting"].includes(obj.status) && (
            <button className="primary" onClick={() => void create("full")}>
              Новая версия полной
            </button>
          )}
        </div>
      )}
      <ErrorNote text={error} />
    </section>
  );
}

function ObjectDays({ obj }: { obj: RepairObject }) {
  const [stages, setStages] = useState<Stage[] | null>(null);
  const working = ["contracting", "active", "dispute", "completed"].includes(obj.status);
  useEffect(() => {
    if (working) void api<Stage[]>(`/objects/${obj.id}/stages`).then(setStages).catch(() => setStages([]));
  }, [obj.id, working]);
  if (!working || stages === null) return null;
  return <DailyPlans objectId={obj.id} stages={stages} canPlan={Boolean(obj.access.is_crew) && obj.status !== "completed"} />;
}

function Stages({ obj }: { obj: RepairObject }) {
  const [stages, setStages] = useState<Stage[] | null>(null);
  const [title, setTitle] = useState("");
  const [stageType, setStageType] = useState("");
  const [stop, setStop] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setStages(await api<Stage[]>(`/objects/${obj.id}/stages`));
    } catch (err) {
      setError(errorText(err));
    }
  }, [obj.id]);
  useEffect(() => void load(), [load, obj.status]);

  async function add(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api(`/objects/${obj.id}/stages`, {
        method: "POST",
        body: { title, is_stop_point: stop, stage_type: stageType || null },
      });
      setTitle("");
      setStageType("");
      setStop(false);
      await load();
    } catch (err) {
      setError(errorText(err));
    }
  }

  if (stages === null) return <section className="card"><Loading /></section>;
  if (stages.length === 0 && !obj.access.can_edit_estimates) return null;
  return (
    <section className="card">
      <h2>Этапы ремонта</h2>
      {stages.length === 0 ? (
        <p className="muted">Этапы появятся вместе с полной сметой: по ним идёт оплата и приёмка работ.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>№</th>
                <th>Этап</th>
                <th>Статус</th>
                <th className="num">Работы</th>
                <th className="num">Материалы</th>
              </tr>
            </thead>
            <tbody>
              {stages.map((s) => (
                <tr key={s.id}>
                  <td>{s.seq}</td>
                  <td>
                    <Link href={`/stages/${s.id}`}>{s.title}</Link>
                    {s.is_stop_point && <span className="badge">проверка технадзора</span>}
                  </td>
                  <td className="nowrap">
                    <span className={`status status-${s.status}`}>{STAGE_STATUS_LABELS[s.status] ?? s.status}</span>
                  </td>
                  <td className="num">{rub(s.work_budget_kop)}</td>
                  <td className="num">{rub(s.material_budget_kop)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {obj.access.can_edit_estimates && (
        <form onSubmit={add} className="grid2 boxed">
          <div>
            <label htmlFor="stage-title">Новый этап</label>
            <input id="stage-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Например, Электрика" />
          </div>
          <div>
            <label htmlFor="stage-type">Вид работ (для чек-листа фото)</label>
            <select
              id="stage-type"
              value={stageType}
              onChange={(e) => {
                setStageType(e.target.value);
                if (!title.trim() && e.target.value) setTitle(STAGE_TYPE_LABELS[e.target.value]);
                if (["electric", "plumbing", "waterproofing"].includes(e.target.value)) setStop(true);
              }}
            >
              <option value="">Другое</option>
              {Object.entries(STAGE_TYPE_LABELS).map(([code, label]) => (
                <option key={code} value={code}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <label className="check" style={{ alignSelf: "center" }}>
            <input type="checkbox" checked={stop} onChange={(e) => setStop(e.target.checked)} />
            <span>Скрытые работы: нужна проверка технадзора</span>
          </label>
          <div className="row">
            <button type="submit" className="secondary" disabled={title.trim().length < 2}>
              Добавить этап
            </button>
          </div>
        </form>
      )}
      <ErrorNote text={error} />
    </section>
  );
}

function Team({ obj, me }: { obj: RepairObject; me: Me }) {
  const [team, setTeam] = useState<Assignment[] | null>(null);
  const [candidates, setCandidates] = useState<{ user_id: string; label: string; role: Assignment["role"] }[]>([]);
  const [pick, setPick] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [linkNote, setLinkNote] = useState<{ name: string; phone: string; url: string | null } | null>(null);

  const load = useCallback(async () => {
    try {
      setTeam(await api<Assignment[]>(`/objects/${obj.id}/assignments`));
    } catch (err) {
      setError(errorText(err));
    }
  }, [obj.id]);
  useEffect(() => void load(), [load]);

  useEffect(() => {
    (async () => {
      const result: typeof candidates = [];
      if (obj.access.can_manage_team && obj.contractor_org_id) {
        const members = await api<Member[]>(`/organizations/${obj.contractor_org_id}/members`);
        members.filter((m) => m.role === "foreman").forEach((m) => result.push({ user_id: m.user_id, role: "foreman", label: `${personName(m)} — прораб` }));
      }
      const pid = platformOrgId(me);
      if (obj.access.is_admin && pid) {
        const members = await api<Member[]>(`/organizations/${pid}/members`);
        members
          .filter((m) => m.role === "inspector" || m.role === "procurement_manager")
          .forEach((m) =>
            result.push({ user_id: m.user_id, role: m.role as Assignment["role"], label: `${personName(m)} — ${ASSIGNMENT_LABELS[m.role]}` }),
          );
      }
      setCandidates(result);
    })().catch(() => undefined);
  }, [obj.access.can_manage_team, obj.access.is_admin, obj.contractor_org_id, me]);

  async function add(e: FormEvent) {
    e.preventDefault();
    const c = candidates.find((x) => `${x.user_id}:${x.role}` === pick);
    if (!c) return;
    setError(null);
    try {
      await api(`/objects/${obj.id}/assignments`, { method: "POST", body: { user_id: c.user_id, role: c.role } });
      setPick("");
      await load();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function remove(a: Assignment) {
    setError(null);
    try {
      await api(`/objects/${obj.id}/assignments/${a.id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function sendLink(a: Assignment) {
    setError(null);
    setLinkNote(null);
    try {
      const out = await api<{ debug_url: string | null; phone: string }>("/auth/links", {
        method: "POST",
        body: { user_id: a.user.id, object_id: obj.id },
      });
      setLinkNote({ name: personName(a.user), phone: out.phone, url: out.debug_url });
    } catch (err) {
      setError(errorText(err));
    }
  }

  const canRemove = (a: Assignment) => obj.access.is_admin || (a.role === "foreman" && obj.access.can_manage_team);
  return (
    <section className="card">
      <h2>Кто работает на объекте</h2>
      {team === null ? (
        <Loading />
      ) : team.length === 0 ? (
        <p className="muted">Пока никого не назначили.</p>
      ) : (
        <ul className="list">
          {team.map((a) => (
            <li key={a.id}>
              <div>
                <b>{personName(a.user)}</b>
                <div className="muted">{ASSIGNMENT_LABELS[a.role]}</div>
              </div>
              <div className="row">
                {a.role === "foreman" && obj.access.can_manage_team && (
                  <button className="secondary small" onClick={() => void sendLink(a)}>
                    Ссылка для входа
                  </button>
                )}
                {canRemove(a) && (
                  <button className="secondary small" onClick={() => void remove(a)}>
                    Снять с объекта
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {candidates.length > 0 && (
        <form onSubmit={add} className="boxed">
          <label htmlFor="team-pick">Назначить</label>
          <select id="team-pick" value={pick} onChange={(e) => setPick(e.target.value)}>
            <option value="">Выберите сотрудника</option>
            {candidates.map((c) => (
              <option key={`${c.user_id}:${c.role}`} value={`${c.user_id}:${c.role}`}>
                {c.label}
              </option>
            ))}
          </select>
          <button type="submit" className="secondary" disabled={!pick}>
            Назначить на объект
          </button>
        </form>
      )}
      {linkNote && (
        <div className="boxed" role="status">
          <p>
            Отправили СМС со ссылкой на {formatPhone(linkNote.phone)}. Пусть {linkNote.name} откроет её на рабочем телефоне в
            течение 30 минут.
          </p>
          {linkNote.url && (
            <p className="devnote">
              Режим разработки, СМС не отправляются. Ссылка: <a href={linkNote.url}>{linkNote.url}</a>
            </p>
          )}
        </div>
      )}
      <ErrorNote text={error} />
    </section>
  );
}

function Feed({ obj }: { obj: RepairObject }) {
  const [items, setItems] = useState<MediaItem[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (after: string | null) => {
      try {
        const page = await api<Page<MediaItem>>(`/objects/${obj.id}/feed?limit=24${after ? `&cursor=${after}` : ""}`);
        setItems((prev) => (after && prev ? [...prev, ...page.items] : page.items));
        setCursor(page.next_cursor);
      } catch (err) {
        setError(errorText(err));
      }
    },
    [obj.id],
  );
  useEffect(() => void load(null), [load]);

  return (
    <section className="card">
      <h2>Фотоотчёты</h2>
      {items === null ? (
        error ? null : <Loading />
      ) : items.length === 0 ? (
        <p className="muted">
          Пока нет фото. Прораб снимает их в приложении бригады: ссылку для входа можно отправить в разделе «Кто работает на
          объекте».
        </p>
      ) : (
        <>
          <ul className="photos">
            {items.map((m) => (
              <li key={m.id}>
                <figure>
                  <a href={m.url} target="_blank" rel="noreferrer">
                    <img src={m.url} alt={`${m.stage_title ?? "Фото"}, ${formatDate(m.received_server_at)}`} loading="lazy" />
                  </a>
                  <figcaption>
                    {m.stage_title ?? "Без этапа"} · {formatDate(m.received_server_at)}
                    {m.checklist_item_title && <div>{m.checklist_item_title}</div>}
                    {m.author_name && <div>{m.author_name}</div>}
                    {m.review_status !== "pending" && (
                      <div className={m.review_status === "rejected" ? "error-text" : "ok"}>
                        {REVIEW_LABELS[m.review_status]}
                        {m.review_comment && `: ${m.review_comment}`}
                      </div>
                    )}
                    {m.fraud_flags &&
                      Object.keys(m.fraud_flags).map((f) => (
                        <span className="flag" key={f}>
                          {FRAUD_FLAG_LABELS[f] ?? f}
                        </span>
                      ))}
                  </figcaption>
                </figure>
              </li>
            ))}
          </ul>
          {cursor && (
            <button type="button" className="secondary" onClick={() => void load(cursor)}>
              Показать ещё
            </button>
          )}
        </>
      )}
      <ErrorNote text={error} />
    </section>
  );
}

function Materials({ obj }: { obj: RepairObject }) {
  const [orders, setOrders] = useState<PurchaseOrder[] | null>(null);
  const [requests, setRequests] = useState<MaterialRequest[]>([]);
  const [stages, setStages] = useState<Stage[]>([]);
  const [suppliers, setSuppliers] = useState<Org[]>([]);
  const [editing, setEditing] = useState<PurchaseOrder | "new" | null>(null);
  const [fromRequest, setFromRequest] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const staff = Boolean(obj.access.can_buy);

  const load = useCallback(async () => {
    try {
      const [o, r] = await Promise.all([
        api<PurchaseOrder[]>(`/objects/${obj.id}/purchase-orders`),
        api<MaterialRequest[]>(`/objects/${obj.id}/material-requests`),
      ]);
      setOrders(o);
      setRequests(r);
      if (staff) {
        const [st, sup] = await Promise.all([
          api<Stage[]>(`/objects/${obj.id}/stages`),
          api<Page<Org>>("/organizations?type=supplier&limit=200"),
        ]);
        setStages(st);
        setSuppliers(sup.items);
      }
    } catch (err) {
      setError(errorText(err));
    }
  }, [obj.id, staff]);
  useEffect(() => void load(), [load]);

  if (orders === null) return null;
  if (!staff && orders.length === 0 && requests.length === 0) return null;
  const replace = (o: PurchaseOrder) => setOrders((all) => (all ?? []).map((x) => (x.id === o.id ? o : x)));
  const openRequests = requests.filter((r) => r.status !== "done");

  return (
    <section className="card" id="materials">
      <h2>Материалы</h2>
      {obj.access.is_customer && orders.length > 0 && (
        <p className="muted">Корзину собирает менеджер платформы. Деньги лежат в резерве банка, поставщик получит их после того, как прораб примет доставку.</p>
      )}
      {(staff || obj.access.is_crew) && openRequests.length > 0 && (
        <>
          <h3>Заявки прораба</h3>
          <ul className="list">
            {openRequests.map((r) => (
              <li key={r.id}>
                <div>
                  <b>{r.text ?? "Голосовая заявка"}</b>
                  <div className="muted small">
                    {r.author_name}, {formatDate(r.created_at)}
                    {r.stage_title ? ` · ${r.stage_title}` : ""} · {REQUEST_STATUS_LABELS[r.status]}
                  </div>
                  {r.audio_url && <audio controls preload="none" src={r.audio_url} />}
                </div>
                {staff && (
                  <button
                    className="secondary"
                    onClick={() => {
                      setFromRequest(r.id);
                      setEditing("new");
                    }}
                  >
                    Собрать корзину
                  </button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      {editing ? (
        <BasketForm
          objectId={obj.id}
          stages={stages}
          suppliers={suppliers}
          requestId={editing === "new" ? fromRequest : null}
          order={editing === "new" ? null : editing}
          onCancel={() => {
            setEditing(null);
            setFromRequest(null);
          }}
          onSaved={(o) => {
            setEditing(null);
            setFromRequest(null);
            setOrders((all) => [o, ...(all ?? []).filter((x) => x.id !== o.id)]);
            void load();
          }}
        />
      ) : (
        staff && (
          <button className="secondary" onClick={() => setEditing("new")}>
            Собрать корзину
          </button>
        )
      )}
      {orders.length === 0 && !editing && <p className="muted">Заказов материалов пока нет.</p>}
      {orders.map((o) => (
        <OrderCard key={o.id} order={o} onChanged={replace} onEdit={(x) => setEditing(x)} />
      ))}
      <ErrorNote text={error} />
    </section>
  );
}

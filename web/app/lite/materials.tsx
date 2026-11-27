"use client";

import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { api, errorText } from "@/lib/api";
import { qty, rub } from "@/lib/format";
import type { MaterialRequest, PurchaseOrder, Stage } from "@/lib/types";

/** Заявка на материалы и приёмка доставок на объекте — для прораба. */
export function LiteMaterials({ objectId, stages, online }: { objectId: string; stages: Stage[]; online: boolean }) {
  const [orders, setOrders] = useState<PurchaseOrder[]>([]);
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    if (!online) return;
    api<PurchaseOrder[]>(`/objects/${objectId}/purchase-orders`)
      .then((all) => setOrders(all.filter((o) => o.can_receive)))
      .catch(() => setOrders([]));
  }, [objectId, online]);

  return (
    <div className="lite-materials">
      {orders.map((o) => (
        <Delivery
          key={o.id}
          order={o}
          onDone={(text) => {
            setOrders((xs) => xs.filter((x) => x.id !== o.id));
            setNote(text);
          }}
        />
      ))}
      {note && (
        <p className="ok" role="status">
          {note}
        </p>
      )}
      {asking ? (
        <RequestForm
          objectId={objectId}
          stages={stages}
          onClose={(text) => {
            setAsking(false);
            if (text) setNote(text);
          }}
        />
      ) : (
        online && (
          <button type="button" className="secondary" onClick={() => setAsking(true)}>
            Заказать материалы
          </button>
        )
      )}
    </div>
  );
}

function RequestForm({ objectId, stages, onClose }: { objectId: string; stages: Stage[]; onClose: (note: string | null) => void }) {
  const [text, setText] = useState("");
  const [stageId, setStageId] = useState("");
  const [audio, setAudio] = useState<Blob | null>(null);
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const audioUrl = useMemo(() => (audio ? URL.createObjectURL(audio) : null), [audio]);
  useEffect(() => () => void (audioUrl && URL.revokeObjectURL(audioUrl)), [audioUrl]);
  const canRecord = typeof window !== "undefined" && "MediaRecorder" in window && Boolean(navigator.mediaDevices?.getUserMedia);

  useEffect(() => () => recorder.current?.stream.getTracks().forEach((t) => t.stop()), []);

  async function toggleRecording() {
    setError(null);
    if (recording) {
      recorder.current?.stop();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => chunks.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        setAudio(new Blob(chunks, { type: rec.mimeType || "audio/webm" }));
        setRecording(false);
      };
      recorder.current = rec;
      rec.start();
      setRecording(true);
    } catch {
      setError("Нет доступа к микрофону. Напишите заявку текстом.");
    }
  }

  async function send(e: FormEvent) {
    e.preventDefault();
    if (!text.trim() && !audio) {
      setError("Напишите или наговорите, что нужно.");
      return;
    }
    setBusy(true);
    setError(null);
    const form = new FormData();
    if (text.trim()) form.append("text", text.trim());
    if (stageId) form.append("stage_id", stageId);
    if (audio) form.append("audio", audio, `voice.${audio.type.includes("mp4") ? "m4a" : "webm"}`);
    try {
      await api<MaterialRequest>(`/objects/${objectId}/material-requests`, { form });
      onClose("Заявка отправлена менеджеру. Он соберёт корзину и согласует её с заказчиком.");
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={(e) => void send(e)}>
      <label>
        Что нужно привезти
        <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="Например: цемент М500, 20 мешков, к пятнице" />
      </label>
      {canRecord && (
        <div className="row">
          <button type="button" className={recording ? "primary" : "secondary"} onClick={() => void toggleRecording()}>
            {recording ? "Остановить запись" : audio ? "Записать заново" : "Наговорить голосом"}
          </button>
          {audioUrl && !recording && <audio controls src={audioUrl} />}
        </div>
      )}
      {stages.length > 0 && (
        <label>
          Для какого этапа
          <select value={stageId} onChange={(e) => setStageId(e.target.value)}>
            <option value="">Не важно</option>
            {stages.map((s) => (
              <option key={s.id} value={s.id}>
                {s.seq}. {s.title}
              </option>
            ))}
          </select>
        </label>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="row">
        <button className="primary" disabled={busy || recording}>
          Отправить заявку
        </button>
        <button type="button" className="secondary" onClick={() => onClose(null)}>
          Отмена
        </button>
      </div>
    </form>
  );
}

type Gap = { title: string; expected: string; received: string };

function Delivery({ order, onDone }: { order: PurchaseOrder; onDone: (note: string) => void }) {
  const [open, setOpen] = useState(false);
  const [photo, setPhoto] = useState<File | null>(null);
  const [result, setResult] = useState<"accepted" | "discrepancies" | "rejected">("accepted");
  const [gaps, setGaps] = useState<Gap[]>([]);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function toggleGap(title: string, expected: string) {
    setGaps((gs) => (gs.some((g) => g.title === title) ? gs.filter((g) => g.title !== title) : [...gs, { title, expected, received: "" }]));
  }

  async function send(e: FormEvent) {
    e.preventDefault();
    if (!photo) {
      setError("Сфотографируйте накладную.");
      return;
    }
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("result", result);
    form.append("invoice", photo);
    form.append(
      "discrepancies",
      JSON.stringify(result === "discrepancies" ? gaps.map((g) => ({ title: g.title, expected: g.expected, received: g.received || null })) : []),
    );
    if (comment.trim()) form.append("comment", comment.trim());
    try {
      await api<PurchaseOrder>(`/purchase-orders/${order.id}/deliveries`, { form });
      onDone(
        result === "accepted"
          ? "Доставка принята. Спасибо!"
          : result === "discrepancies"
            ? "Доставка принята с расхождениями, менеджер разберётся."
            : "Доставка не принята, менеджер свяжется с поставщиком.",
      );
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <div className="delivery">
      <div className="row between">
        <div>
          <b>Едут материалы на {rub(order.total_kop)}</b>
          <div className="muted small">{order.items.map((i) => `${i.title} × ${qty(i.qty)}`).join(", ")}</div>
        </div>
        {!open && (
          <button type="button" className="primary" onClick={() => setOpen(true)}>
            Принять доставку
          </button>
        )}
      </div>
      {open && (
        <form className="stack" onSubmit={(e) => void send(e)}>
          <label>
            Фото накладной
            <input type="file" accept="image/*" capture="environment" onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} />
          </label>
          <fieldset>
            <legend>Всё привезли?</legend>
            <div className="radio-row">
              <label>
                <input type="radio" name={`r-${order.id}`} checked={result === "accepted"} onChange={() => setResult("accepted")} />
                Всё на месте
              </label>
              <label>
                <input type="radio" name={`r-${order.id}`} checked={result === "discrepancies"} onChange={() => setResult("discrepancies")} />
                Есть расхождения
              </label>
              <label>
                <input type="radio" name={`r-${order.id}`} checked={result === "rejected"} onChange={() => setResult("rejected")} />
                Не принимаю
              </label>
            </div>
          </fieldset>
          {result === "discrepancies" && (
            <fieldset>
              <legend>Чего не хватает или что не так</legend>
              {order.items.map((i) => {
                const gap = gaps.find((g) => g.title === i.title);
                return (
                  <div key={i.id} className="gap-row">
                    <label className="check">
                      <input type="checkbox" checked={Boolean(gap)} onChange={() => toggleGap(i.title, String(Number(i.qty)))} />
                      {i.title}, заказано {qty(i.qty)}
                    </label>
                    {gap && (
                      <label>
                        Сколько привезли
                        <input
                          inputMode="decimal"
                          value={gap.received}
                          onChange={(e) => setGaps((gs) => gs.map((g) => (g.title === i.title ? { ...g, received: e.target.value.replace(",", ".") } : g)))}
                        />
                      </label>
                    )}
                  </div>
                );
              })}
            </fieldset>
          )}
          {result !== "accepted" && (
            <label>
              {result === "rejected" ? "Почему не принимаете" : "Комментарий"}
              <input value={comment} onChange={(e) => setComment(e.target.value)} required={result === "rejected"} />
            </label>
          )}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <div className="row">
            <button className="primary" disabled={busy}>
              Отправить
            </button>
            <button type="button" className="secondary" onClick={() => setOpen(false)}>
              Отмена
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

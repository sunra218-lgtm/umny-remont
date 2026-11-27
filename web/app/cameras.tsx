"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { api, errorText } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { Camera, CameraState, RepairObject } from "@/lib/types";

import { ErrorNote } from "./components";

export const CAMERA_STATE: Record<CameraState, string> = {
  off: "Выключена",
  paused: "Пауза: объект не в работе",
  sleeping: "Спит: нерабочие часы",
  no_signal: "Нет кадров",
  live: "Снимает",
};

const HOURS = Array.from({ length: 25 }, (_, i) => i);

/** Камеры объекта: заказчик включает и выключает, бригада подключает. */
export function Cameras({ obj }: { obj: RepairObject }) {
  const [items, setItems] = useState<Camera[] | null>(null);
  const [consentText, setConsentText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [link, setLink] = useState<{ title: string; url: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await api<{ items: Camera[]; consent_text: string }>(`/objects/${obj.id}/cameras`);
      setItems(r.items);
      setConsentText(r.consent_text);
    } catch (err) {
      setError(errorText(err));
    }
  }, [obj.id]);
  useEffect(() => void load(), [load]);

  const canAdd = obj.access.is_customer || obj.access.is_admin || Boolean(obj.access.is_crew);
  const working = ["contracting", "active", "dispute"].includes(obj.status);
  if (items === null) return null;
  if (items.length === 0 && !(canAdd && working)) return null;

  function showLink(cam: Camera) {
    if (cam.phone_link) setLink({ title: cam.title, url: cam.phone_link });
  }

  return (
    <section className="card" id="cameras">
      <h2>Камеры</h2>
      <p className="muted small">
        Камера делает кадр раз в несколько минут, только в рабочие часы и только когда её включил заказчик. Из кадров
        собирается таймлапс ремонта.
      </p>
      <ErrorNote text={error} />
      {link && (
        <div className="boxed">
          <p>
            <b>Ссылка для телефона-камеры «{link.title}».</b> Откройте её на старом телефоне, поставьте его на зарядку и
            направьте на комнату. Ссылку видно только сейчас, никому её не пересылайте.
          </p>
          <p className="break">
            <a href={link.url} target="_blank" rel="noreferrer">{link.url}</a>
          </p>
          <div className="row">
            <button type="button" className="secondary" onClick={() => void navigator.clipboard?.writeText(link.url)}>
              Скопировать ссылку
            </button>
            <button type="button" className="link" onClick={() => setLink(null)}>
              Скрыть
            </button>
          </div>
        </div>
      )}
      {items.length === 0 ? (
        <p className="muted">Камер пока нет.</p>
      ) : (
        <ul className="list">
          {items.map((c) => (
            <CameraRow key={c.id} cam={c} consentText={consentText} onChanged={load} onLink={showLink} />
          ))}
        </ul>
      )}
      {canAdd && working && items.length < 4 && (
        adding ? (
          <AddCamera
            objectId={obj.id}
            onDone={async (cam) => {
              setAdding(false);
              showLink(cam);
              await load();
            }}
            onCancel={() => setAdding(false)}
          />
        ) : (
          <button type="button" className="secondary" onClick={() => setAdding(true)}>
            Подключить камеру
          </button>
        )
      )}
    </section>
  );
}

function CameraRow({
  cam,
  consentText,
  onChanged,
  onLink,
}: {
  cam: Camera;
  consentText: string;
  onChanged: () => Promise<void>;
  onLink: (c: Camera) => void;
}) {
  const [consent, setConsent] = useState(false);
  const [asking, setAsking] = useState(false);
  const [editHours, setEditHours] = useState(false);
  const [from, setFrom] = useState(cam.hour_from);
  const [to, setTo] = useState(cam.hour_to);
  const [error, setError] = useState<string | null>(null);

  async function act(path: string, body?: unknown, method = "POST") {
    setError(null);
    try {
      const r = await api<Camera | undefined>(`/cameras/${cam.id}${path}`, { method, body });
      if (r && r.phone_link) onLink(r);
      setAsking(false);
      setEditHours(false);
      await onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <li>
      <div className="grow">
        <div className="row between">
          <b>{cam.title}</b>
          <span className={cam.state === "live" ? "badge" : "badge warn"}>{CAMERA_STATE[cam.state]}</span>
        </div>
        <div className="muted small">
          {cam.kind === "ip" ? `IP-камера ${cam.snapshot_url ?? ""}` : "Телефон-камера"} · кадр раз в {cam.interval_min} мин ·
          снимает с {cam.hour_from}:00 до {cam.hour_to}:00 по Москве
        </div>
        <div className="muted small">
          Сегодня кадров: {cam.frames_today}
          {cam.last_frame_at && ` · последний ${formatDate(cam.last_frame_at)}`}
        </div>
        {cam.last_error && <p className="error small">{cam.last_error}</p>}
        {cam.latest_frame_url && (
          <Link href={`/cameras/${cam.id}`}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img className="example-photo" src={cam.latest_frame_url} alt={`Последний кадр: ${cam.title}`} loading="lazy" />
          </Link>
        )}
        <ErrorNote text={error} />
        <div className="row">
          <Link href={`/cameras/${cam.id}`} className="button secondary">
            Таймлапс
          </Link>
          {cam.can_toggle && cam.enabled && (
            <button type="button" className="secondary" onClick={() => void act("/disable")}>
              Выключить
            </button>
          )}
          {cam.can_toggle && !cam.enabled && !asking && (
            <button type="button" className="primary" onClick={() => setAsking(true)}>
              Включить
            </button>
          )}
          {cam.can_toggle && !editHours && (
            <button type="button" className="link" onClick={() => setEditHours(true)}>
              Часы съёмки
            </button>
          )}
          {cam.can_edit && cam.kind === "phone" && (
            <button type="button" className="link" onClick={() => void act("/phone-link")}>
              Новая ссылка для телефона
            </button>
          )}
          {cam.can_edit && (
            <button type="button" className="link" onClick={() => void act("", undefined, "DELETE")}>
              Удалить
            </button>
          )}
        </div>
        {asking && (
          <div className="boxed">
            <label className="check">
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
              <span>{consentText}</span>
            </label>
            <div className="row">
              <button type="button" className="primary" disabled={!consent} onClick={() => void act("/enable", { consent })}>
                Включить камеру
              </button>
              <button type="button" className="link" onClick={() => setAsking(false)}>
                Отмена
              </button>
            </div>
          </div>
        )}
        {editHours && (
          <div className="boxed">
            <div className="row">
              <label htmlFor={`hf-${cam.id}`}>С</label>
              <select id={`hf-${cam.id}`} value={from} onChange={(e) => setFrom(Number(e.target.value))}>
                {HOURS.slice(0, 24).map((h) => (
                  <option key={h} value={h}>{h}:00</option>
                ))}
              </select>
              <label htmlFor={`ht-${cam.id}`}>до</label>
              <select id={`ht-${cam.id}`} value={to} onChange={(e) => setTo(Number(e.target.value))}>
                {HOURS.slice(1).map((h) => (
                  <option key={h} value={h}>{h}:00</option>
                ))}
              </select>
            </div>
            <div className="row">
              <button
                type="button"
                className="primary"
                disabled={from >= to}
                onClick={() => void act("", { hours: { hour_from: from, hour_to: to } }, "PATCH")}
              >
                Сохранить часы
              </button>
              <button type="button" className="link" onClick={() => setEditHours(false)}>
                Отмена
              </button>
            </div>
          </div>
        )}
      </div>
    </li>
  );
}

function AddCamera({
  objectId,
  onDone,
  onCancel,
}: {
  objectId: string;
  onDone: (c: Camera) => Promise<void>;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<"phone" | "ip">("phone");
  const [url, setUrl] = useState("");
  const [every, setEvery] = useState(15);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const cam = await api<Camera>(`/objects/${objectId}/cameras`, {
        method: "POST",
        body: { title, kind, interval_min: every, snapshot_url: kind === "ip" ? url : null },
      });
      await onDone(cam);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="boxed">
      <label htmlFor="cam-title">Где стоит</label>
      <input id="cam-title" value={title} maxLength={100} onChange={(e) => setTitle(e.target.value)} placeholder="Например, Кухня" />
      <label htmlFor="cam-kind">Что за камера</label>
      <select id="cam-kind" value={kind} onChange={(e) => setKind(e.target.value as "phone" | "ip")}>
        <option value="phone">Старый телефон</option>
        <option value="ip">IP-камера</option>
      </select>
      {kind === "ip" && (
        <>
          <label htmlFor="cam-url">Ссылка на снимок с камеры</label>
          <input
            id="cam-url"
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="http://логин:пароль@адрес:порт/snapshot.jpg"
          />
          <p className="muted small">Камера должна быть доступна из интернета. Ссылку на снимок смотрите в инструкции к камере.</p>
        </>
      )}
      <label htmlFor="cam-interval">Кадр раз в</label>
      <select id="cam-interval" value={every} onChange={(e) => setEvery(Number(e.target.value))}>
        {[5, 10, 15, 30, 60].map((m) => (
          <option key={m} value={m}>{m} мин</option>
        ))}
      </select>
      <p className="muted small">Камера начнёт снимать, когда заказчик включит её в карточке объекта.</p>
      <ErrorNote text={error} />
      <div className="row">
        <button type="submit" className="primary" disabled={busy || title.trim().length < 2 || (kind === "ip" && !url)}>
          Подключить
        </button>
        <button type="button" className="link" onClick={onCancel}>
          Отмена
        </button>
      </div>
    </form>
  );
}

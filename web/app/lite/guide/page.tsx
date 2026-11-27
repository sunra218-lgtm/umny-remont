"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { api } from "@/lib/api";
import { useLite } from "@/lib/lite";
import type { TechCard } from "@/lib/types";

import { AskEngineer, Assistant, Calculator } from "../../guide";
import { TechCardView } from "../../techcard";
import { cacheGet, cacheSet, LiteGate } from "../parts";

export default function GuidePage() {
  return (
    <LiteGate>
      <Suspense>
        <Guide />
      </Suspense>
    </LiteGate>
  );
}

/** Подсказки мастеру по этапу: как делать, допуски, расход материалов, вопрос инженеру. */
function Guide() {
  const params = useSearchParams();
  const stageId = params.get("stage") ?? "";
  const objectId = params.get("object") ?? "";
  const title = params.get("title") ?? "Этап";
  const { online } = useLite();
  const [card, setCard] = useState<TechCard | null>(() => cacheGet(`lite:card:${stageId}`));
  const [loaded, setLoaded] = useState(false);
  const [draft, setDraft] = useState<string | undefined>();

  useEffect(() => {
    if (!stageId || !online) return;
    api<TechCard | null>(`/stages/${stageId}/tech-card`)
      .then((c) => {
        setCard(c);
        cacheSet(`lite:card:${stageId}`, c);
      })
      .catch(() => undefined)
      .finally(() => setLoaded(true));
  }, [stageId, online]);

  return (
    <>
      <p>
        <Link href="/lite">← К объектам</Link>
      </p>
      <h1>Как делать: {title}</h1>
      {card ? (
        <TechCardView card={card} title="Шаги и допуски" />
      ) : (
        loaded && <p className="muted">Для этого этапа техкарты пока нет. Задайте вопрос помощнику или инженеру.</p>
      )}
      {!online && <p className="muted">Нет связи: подсказки показаны из памяти телефона, калькулятор и вопросы заработают при связи.</p>}
      {online && card && <Calculator card={card} />}
      {online && (
        <Assistant
          stageType={card?.stage_type}
          onAskEngineer={(q) => {
            setDraft(q);
            document.getElementById("ask")?.scrollIntoView({ behavior: "smooth" });
          }}
        />
      )}
      {online && objectId && <AskEngineer stageId={stageId} objectId={objectId} card={card} draft={draft} />}
    </>
  );
}

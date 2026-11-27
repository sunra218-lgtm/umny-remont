"use client";

import { formatDate } from "@/lib/format";
import type { TechCard, TechStep } from "@/lib/types";

/** Пометка о том, проверена ли карта инженером. */
export function CardStatus({ card }: { card: TechCard }) {
  return card.verified_at ? (
    <p className="muted small">Проверено инженером технадзора {formatDate(card.verified_at)}.</p>
  ) : (
    <p className="devnote">Ориентир по нормам: карту ещё проверяет инженер технадзора платформы.</p>
  );
}

export function StepDetails({ step }: { step: TechStep }) {
  return (
    <>
      <p>{step.how}</p>
      {step.tolerance && (
        <p>
          <b>Допуск:</b> {step.tolerance}
        </p>
      )}
      {step.check && (
        <p className="muted small">
          <b>Как проверить:</b> {step.check}
        </p>
      )}
      {step.mistakes.length > 0 && (
        <>
          <p className="small">
            <b>Частые ошибки:</b>
          </p>
          <ul className="small">
            {step.mistakes.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        </>
      )}
      {step.photo && <p className="muted small">Фото: {step.photo}</p>}
    </>
  );
}

/** «Как должно быть»: шаги техкарты с допусками и нормами. */
export function TechCardView({ card, title = "Как должно быть" }: { card: TechCard; title?: string }) {
  return (
    <section className="card">
      <h2>{title}</h2>
      {card.summary && <p>{card.summary}</p>}
      <CardStatus card={card} />
      <div className="tc-steps">
        {card.steps.map((s, n) => (
          <details key={s.key}>
            <summary>
              {n + 1}. {s.title}
              {s.hidden && <span className="badge">скрытые работы</span>}
              {s.inspector && <span className="badge">проверяет инженер</span>}
            </summary>
            <StepDetails step={s} />
          </details>
        ))}
      </div>
      {card.norms.length > 0 && <p className="muted small">Нормы: {card.norms.join("; ")}.</p>}
    </section>
  );
}

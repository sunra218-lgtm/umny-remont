"""Распознавание кадров с камер: какой этап идёт и есть ли скрытые работы.

Сейчас работает «по графику»: кадр относится к этапу, который в момент съёмки в работе,
а скрытые работы отмечаются, если в техкарте этапа есть шаги, которые потом закроют
(проводка, трубы, гидроизоляция, стяжка). Инженер технадзора может поправить разметку
вручную — такие кадры потом станут обучающей выборкой для нейросети.

Нейросеть подключается реализацией ``Recognizer`` без изменений в остальном коде.
"""

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Camera, Stage, TechCard
from app.models.enums import StageStatus


class Recognition(BaseModel):
    stage_id: Any = None
    hidden_work: bool = False
    labels: dict[str, Any] = Field(default_factory=dict)


class Recognizer(Protocol):
    async def recognize(
        self, db: AsyncSession, camera: Camera, image: bytes, taken_at: datetime
    ) -> Recognition: ...


class ScheduleRecognizer:
    async def recognize(
        self, db: AsyncSession, camera: Camera, image: bytes, taken_at: datetime
    ) -> Recognition:
        stage = (
            await db.execute(
                select(Stage)
                .where(
                    Stage.object_id == camera.object_id,
                    Stage.deleted_at.is_(None),
                    Stage.status == StageStatus.in_progress,
                )
                .order_by(Stage.seq)
                .limit(1)
            )
        ).scalar_one_or_none()
        if stage is None:
            return Recognition(labels={"source": "schedule"})
        hidden: list[str] = []
        if stage.stage_type:
            card = (
                await db.execute(select(TechCard).where(TechCard.stage_type == stage.stage_type))
            ).scalar_one_or_none()
            if card:
                hidden = [s["title"] for s in card.steps if s.get("hidden")]
        return Recognition(
            stage_id=stage.id,
            hidden_work=bool(hidden),
            labels={"source": "schedule", "stage_type": stage.stage_type, "hidden_steps": hidden},
        )


_recognizer: Recognizer = ScheduleRecognizer()


def get_recognizer() -> Recognizer:
    return _recognizer

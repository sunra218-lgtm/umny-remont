"""Начальный справочник расценок (ориентиры для Москвы, новостройка без отделки).

Запускается при старте backend и ничего не делает, если справочник уже заполнен.
Цены — ориентир для пилота: сметчик платформы уточнит их до запуска.
"""

import asyncio

from sqlalchemy import func, select

from app.core.db import get_engine, get_session
from app.models import ChecklistItem, ChecklistTemplate, PriceItem
from app.models.enums import EstimateItemCategory

W = EstimateItemCategory.work
M = EstimateItemCategory.material

# (тип этапа, категория, наименование, ед., цена в рублях)
CATALOG: list[tuple[str, EstimateItemCategory, str, str, int]] = [
    ("demolition", W, "Демонтаж перегородок из ГКЛ", "м²", 350),
    ("demolition", W, "Вынос и вывоз строительного мусора", "м³", 2200),
    ("walls", W, "Возведение перегородок из пазогребневых плит", "м²", 1400),
    ("walls", W, "Штукатурка стен по маякам", "м²", 750),
    ("walls", M, "Штукатурка гипсовая, мешок 30 кг", "шт", 520),
    ("walls", M, "Плита пазогребневая 667×500×80", "шт", 380),
    ("electric", W, "Штробление и прокладка кабеля", "м.п.", 320),
    ("electric", W, "Установка подрозетника", "шт", 350),
    ("electric", W, "Сборка электрощита (до 24 модулей)", "шт", 9500),
    ("electric", M, "Кабель ВВГнг-LS 3×2,5", "м.п.", 95),
    ("plumbing", W, "Разводка труб водоснабжения", "точка", 3500),
    ("plumbing", W, "Разводка канализации", "точка", 2800),
    ("plumbing", M, "Труба полипропиленовая 20 мм", "м.п.", 90),
    ("screed", W, "Полусухая стяжка пола до 70 мм", "м²", 850),
    ("screed", M, "Пескобетон М300, мешок 40 кг", "шт", 290),
    ("waterproofing", W, "Обмазочная гидроизоляция санузла", "м²", 650),
    ("tiling", W, "Укладка керамогранита на пол", "м²", 1800),
    ("tiling", W, "Облицовка стен плиткой", "м²", 2100),
    ("tiling", M, "Клей плиточный, мешок 25 кг", "шт", 610),
    ("finishing", W, "Шпаклёвка стен под покраску", "м²", 650),
    ("finishing", W, "Покраска стен в два слоя", "м²", 450),
    ("finishing", W, "Укладка ламината", "м²", 550),
    ("finishing", M, "Краска интерьерная моющаяся, 9 л", "шт", 4900),
    ("ceiling", W, "Натяжной потолок матовый", "м²", 850),
    ("doors", W, "Установка межкомнатной двери", "шт", 5500),
]


async def seed_catalog() -> int:
    async for db in get_session():
        count = await db.scalar(select(func.count()).select_from(PriceItem))
        if count:
            return 0
        for stage_type, category, title, unit, price_rub in CATALOG:
            db.add(
                PriceItem(
                    stage_type=stage_type,
                    category=category,
                    title=title,
                    unit=unit,
                    unit_price_kop=price_rub * 100,
                )
            )
        await db.commit()
    return len(CATALOG)


# Чек-листы ракурсов: (тип этапа, название, [(пункт, ракурс, нужно ли фото)]).
# Стартовый набор для пилота; инженер технадзора уточнит его в админке.
CHECKLISTS: list[tuple[str, str, list[tuple[str, str | None, bool]]]] = [
    (
        "demolition",
        "Демонтаж",
        [
            ("Помещение до начала работ", "Общий план от входа в каждую комнату", True),
            (
                "Демонтированные перегородки",
                "Место бывшей перегородки, пол и потолок в кадре",
                True,
            ),
            ("Мусор вывезен", "Общий план, пустое помещение", True),
        ],
    ),
    (
        "walls",
        "Перегородки и штукатурка",
        [
            ("Кладка перегородки", "Перегородка целиком, видны швы и примыкание к потолку", True),
            ("Маяки выставлены", "Стена под углом, видны маяки и правило", True),
            ("Стена после штукатурки", "Правило 2 м приложено к стене, виден просвет", True),
        ],
    ),
    (
        "electric",
        "Электрика — скрытые работы",
        [
            ("Трассы кабеля в штробах", "Каждая стена целиком до заделки штроб, с рулеткой", True),
            ("Подрозетники", "Крупно, с уровнем", True),
            ("Электрощит собран", "Щит открыт, видны автоматы и подписи", True),
            ("Замер сопротивления изоляции", "Экран прибора и подключение", False),
        ],
    ),
    (
        "plumbing",
        "Сантехника — скрытые работы",
        [
            ("Разводка водоснабжения", "Все трубы до закрытия, с рулеткой", True),
            ("Разводка канализации", "Уклон труб, уровень в кадре", True),
            ("Опрессовка", "Манометр с давлением и временем", True),
        ],
    ),
    (
        "waterproofing",
        "Гидроизоляция",
        [
            ("Гидроизоляция пола", "Весь пол санузла, заход на стены", True),
            ("Примыкания и углы", "Крупно: углы, трубы, лента", True),
            ("Пролив водой", "Залитый пол и отметка уровня", False),
        ],
    ),
    (
        "screed",
        "Стяжка пола",
        [
            ("Маяки и демпферная лента", "Общий план до заливки", True),
            ("Стяжка готова", "Правило 2 м на стяжке, виден просвет", True),
        ],
    ),
    (
        "tiling",
        "Плитка",
        [
            ("Раскладка плитки", "Стена или пол целиком", True),
            ("Швы и углы", "Крупно, с крестиками или затиркой", True),
        ],
    ),
    (
        "finishing",
        "Чистовая отделка",
        [
            ("Стены под покраску", "Боковой свет вдоль стены", True),
            ("Покраска готова", "Общий план комнаты", True),
            ("Напольное покрытие", "Общий план и стыки у порогов", True),
        ],
    ),
]


async def seed_checklists() -> int:
    async for db in get_session():
        if await db.scalar(select(func.count()).select_from(ChecklistTemplate)):
            return 0
        for stage_type, title, items in CHECKLISTS:
            template = ChecklistTemplate(stage_type=stage_type, title=title)
            db.add(template)
            await db.flush()
            for seq, (item, angle, required) in enumerate(items, start=1):
                db.add(
                    ChecklistItem(
                        template_id=template.id,
                        seq=seq,
                        title=item,
                        required_angle=angle,
                        photo_required=required,
                    )
                )
        await db.commit()
    return len(CHECKLISTS)


async def main() -> None:
    added = await seed_catalog()
    print(f"Справочник расценок: добавлено {added} позиций")  # noqa: T201
    added = await seed_checklists()
    print(f"Чек-листы: добавлено {added} шаблонов")  # noqa: T201
    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())

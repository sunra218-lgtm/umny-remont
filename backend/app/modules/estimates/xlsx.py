"""Импорт сметы из Excel и шаблон файла для импорта."""

import io
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from app.core.money import rub_to_kop
from app.models.enums import EstimateItemCategory

MAX_ROWS = 3000
COLUMNS = ["Этап", "Помещение", "Тип", "Наименование", "Ед. изм.", "Количество", "Цена, ₽"]

# Ключевые слова в заголовках столбцов → поле.
HEADER_KEYS = {
    "stage": ("этап",),
    "room": ("помещ", "комнат"),
    "category": ("тип", "категор", "вид"),
    "title": ("наимен", "работ", "позиц", "описан"),
    "unit": ("ед",),
    "qty": ("кол",),
    "price": ("цена", "стоим", "расцен"),
}
REQUIRED = ("title", "unit", "qty", "price")

WORK_WORDS = ("работ", "work", "услуг")
MATERIAL_WORDS = ("материал", "material", "товар")


@dataclass
class ParsedRow:
    row: int
    stage: str | None
    room: str | None
    category: EstimateItemCategory
    title: str
    unit: str
    qty: Decimal
    unit_price_kop: int


class ImportError_(Exception):
    def __init__(self, errors: list[dict[str, object]]) -> None:
        self.errors = errors
        super().__init__("import failed")


def _cell_text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _find_header(rows: list[tuple[object, ...]]) -> tuple[int, dict[str, int]] | None:
    for index, row in enumerate(rows[:15]):
        mapping: dict[str, int] = {}
        for col, value in enumerate(row):
            text = _cell_text(value).lower()
            if not text:
                continue
            for field, keys in HEADER_KEYS.items():
                if field in mapping:
                    continue
                # «Цена» не должна попасть в «Наименование работ» и наоборот.
                if field == "title" and any(k in text for k in ("цена", "стоим", "кол")):
                    continue
                if any(text.startswith(k) or f" {k}" in text for k in keys):
                    mapping[field] = col
                    break
        if all(f in mapping for f in REQUIRED):
            return index, mapping
    return None


def _decimal(value: object) -> Decimal:
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value))
    text = re.sub(r"[\s ]", "", _cell_text(value)).replace(",", ".")
    return Decimal(text)


def parse_xlsx(content: bytes) -> list[ParsedRow]:
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 — любой повреждённый файл
        raise ImportError_(
            [{"row": 0, "message": "Не удалось открыть файл. Нужен формат .xlsx"}]
        ) from None
    ws = wb.worksheets[0]
    rows = [tuple(r) for r in ws.iter_rows(values_only=True, max_row=MAX_ROWS + 20)]
    wb.close()
    found = _find_header(rows)
    if found is None:
        raise ImportError_(
            [
                {
                    "row": 0,
                    "message": "Не нашли строку заголовков. Нужны столбцы: "
                    "Наименование, Ед. изм., Количество, Цена. Скачайте шаблон.",
                }
            ]
        )
    header_index, cols = found
    parsed: list[ParsedRow] = []
    errors: list[dict[str, object]] = []

    def get(row: tuple[object, ...], field: str) -> object:
        col = cols.get(field)
        return row[col] if col is not None and col < len(row) else None

    for offset, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        title = _cell_text(get(row, "title"))
        if not title and not _cell_text(get(row, "qty")):
            continue  # пустая строка или подытог
        if len(parsed) >= MAX_ROWS:
            errors.append({"row": offset, "message": f"Больше {MAX_ROWS} строк не загружаем"})
            break
        problems = []
        if len(title) < 2:
            problems.append("нет наименования")
        unit = _cell_text(get(row, "unit"))
        if not unit:
            problems.append("нет единицы измерения")
        try:
            qty = _decimal(get(row, "qty"))
            if qty <= 0:
                raise InvalidOperation
            qty = qty.quantize(Decimal("0.001"))
        except (InvalidOperation, ValueError):
            problems.append("количество должно быть числом больше нуля")
            qty = Decimal(0)
        try:
            price = rub_to_kop(get(row, "price"))
        except (InvalidOperation, ValueError):
            problems.append("цена должна быть числом")
            price = 0
        kind_text = _cell_text(get(row, "category")).lower()
        if not kind_text or any(w in kind_text for w in WORK_WORDS):
            category = EstimateItemCategory.work
        elif any(w in kind_text for w in MATERIAL_WORDS):
            category = EstimateItemCategory.material
        else:
            problems.append("тип должен быть «работа» или «материал»")
            category = EstimateItemCategory.work
        if problems:
            errors.append({"row": offset, "message": "; ".join(problems).capitalize()})
            continue
        parsed.append(
            ParsedRow(
                row=offset,
                stage=_cell_text(get(row, "stage"))[:200] or None,
                room=_cell_text(get(row, "room"))[:100] or None,
                category=category,
                title=title[:500],
                unit=unit[:20],
                qty=qty,
                unit_price_kop=price,
            )
        )
    if errors:
        raise ImportError_(errors[:50])
    if not parsed:
        raise ImportError_([{"row": 0, "message": "В файле нет позиций"}])
    return parsed


def template_xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Смета"
    ws.append(COLUMNS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.append(["Электрика", "Кухня", "работа", "Штробление и прокладка кабеля", "м.п.", 45, 320])
    ws.append(["Электрика", "Кухня", "материал", "Кабель ВВГнг-LS 3×2,5", "м.п.", 50, 95])
    ws.append(["Стяжка", "", "работа", "Полусухая стяжка пола до 70 мм", "м²", 62.5, 850])
    for index, width in enumerate([18, 14, 10, 48, 10, 12, 12], start=1):
        ws.column_dimensions[get_column_letter(index)].width = width
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()

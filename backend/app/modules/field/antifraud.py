"""Проверки фотоотчёта (ТЗ 2.3, раздел 6.2). Найденное записывается во fraud_flags и
показывается проверяющему, но не блокирует загрузку."""

import io
import math
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

FORMATS = {
    "JPEG": ("image/jpeg", "jpg"),
    "WEBP": ("image/webp", "webp"),
    "PNG": ("image/png", "png"),
}


@dataclass
class ImageInfo:
    content_type: str
    ext: str
    width: int
    height: int
    phash: int  # знаковое 64-битное — так оно хранится в BIGINT


class NotAnImage(ValueError):
    pass


def inspect_image(data: bytes) -> ImageInfo:
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format or ""
            if fmt not in FORMATS:
                raise NotAnImage("Нужна фотография в формате JPEG или WebP")
            img.load()
            width, height = img.size
            phash = dhash(img)
    except (UnidentifiedImageError, OSError):
        raise NotAnImage("Файл не похож на фотографию") from None
    content_type, ext = FORMATS[fmt]
    return ImageInfo(content_type, ext, width, height, to_signed64(phash))


def dhash(img: Image.Image) -> int:
    """Перцептивный хэш (difference hash, 64 бита): похожие кадры дают близкие хэши, даже если
    снимок пережат или слегка изменён."""
    small = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(small.getdata())
    value = 0
    for row in range(8):
        for col in range(8):
            left = pixels[row * 9 + col]
            right = pixels[row * 9 + col + 1]
            value = (value << 1) | (1 if left > right else 0)
    return value


def to_signed64(value: int) -> int:
    return value - (1 << 64) if value >= (1 << 63) else value


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние по поверхности Земли (формула гаверсинуса), в метрах."""
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))

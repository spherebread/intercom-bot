"""Работа со временем: в БД храним naive UTC, пользователю показываем в DISPLAY_TZ (UTC+4 по умолчанию)."""
import os
from datetime import UTC, datetime, timedelta, timezone

DISPLAY_TZ = timezone(timedelta(hours=float(os.getenv("TZ_OFFSET_HOURS", "4"))))


def utcnow() -> datetime:
    """Текущее время в UTC без tzinfo — в таком виде оно лежит в БД (колонки DateTime без таймзоны)."""
    return datetime.now(UTC).replace(tzinfo=None)


def to_display(dt: datetime) -> datetime:
    """naive UTC (из БД) или aware datetime → время в DISPLAY_TZ."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(DISPLAY_TZ)


def fmt(dt: datetime, pattern: str = "%d.%m.%Y %H:%M") -> str:
    return to_display(dt).strftime(pattern)


def now_display(pattern: str = "%d.%m.%Y %H:%M:%S") -> str:
    return datetime.now(DISPLAY_TZ).strftime(pattern)

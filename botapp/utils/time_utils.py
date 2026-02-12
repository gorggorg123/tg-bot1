# botapp/utils/time_utils.py
"""
Утилиты для работы со временем и датами.
Вынесены из ozon_client.py для лучшей структурированности.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Tuple

# Смещение для московского времени
MSK_SHIFT = timedelta(hours=3)
MSK_TZ = timezone(MSK_SHIFT)


def iso_z(dt: datetime) -> str:
    """Вернуть ISO-строку в UTC с Z без миллисекунд."""
    dt_utc = ensure_utc(dt)
    return dt_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_utc(dt: datetime) -> datetime:
    """Убедиться, что datetime в UTC."""
    if dt.tzinfo:
        return dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)


def msk_today_range() -> Tuple[str, str, str]:
    """
    Диапазон на сегодня в МСК, но границы в UTC.
    Возвращает (from_iso, to_iso, pretty_text).
    """
    now_utc = datetime.utcnow()
    now_msk = now_utc + MSK_SHIFT
    d = now_msk.date()

    start_utc = datetime(d.year, d.month, d.day) - MSK_SHIFT
    end_utc = start_utc + timedelta(days=1) - timedelta(seconds=1)

    pretty = (
        f"{d.strftime('%d.%m.%Y')} 00:00 — "
        f"{d.strftime('%d.%m.%Y')} 23:59 (МСК)"
    )
    return iso_z(start_utc), iso_z(end_utc), pretty


def msk_current_month_range() -> Tuple[str, str, str]:
    """
    Диапазон с 1-го числа текущего месяца по сегодня (МСК).
    Границы возвращаются в UTC, плюс красивый текст.
    """
    now_utc = datetime.utcnow()
    now_msk = now_utc + MSK_SHIFT
    today = now_msk.date()

    first = date(today.year, today.month, 1)
    if today.month == 12:
        last_calendar = date(today.year, 12, 31)
    else:
        next_first = date(today.year, today.month + 1, 1)
        last_calendar = next_first - timedelta(days=1)

    end_date = today if today <= last_calendar else last_calendar

    start_utc = datetime(first.year, first.month, first.day) - MSK_SHIFT
    end_utc = datetime(
        end_date.year, end_date.month, end_date.day, 23, 59, 59
    ) - MSK_SHIFT

    pretty = (
        f"{first.strftime('%d.%m.%Y')} — "
        f"{end_date.strftime('%d.%m.%Y')} (МСК)"
    )
    return iso_z(start_utc), iso_z(end_utc), pretty


def msk_week_range() -> Tuple[str, str, str]:
    """Возвращает диапазон за последние 7 дней с учётом МСК."""
    now_utc = datetime.utcnow()
    now_msk = now_utc + MSK_SHIFT
    today = now_msk.date()
    week_ago = today - timedelta(days=6)

    start_utc = datetime(week_ago.year, week_ago.month, week_ago.day) - MSK_SHIFT
    end_utc = datetime(today.year, today.month, today.day, 23, 59, 59) - MSK_SHIFT

    pretty = (
        f"{week_ago.strftime('%d.%m.%Y')} — "
        f"{today.strftime('%d.%m.%Y')} (МСК)"
    )
    return iso_z(start_utc), iso_z(end_utc), pretty


def msk_yesterday_range() -> Tuple[str, str, str]:
    """Диапазон за вчера (МСК)."""
    now_utc = datetime.utcnow()
    now_msk = now_utc + MSK_SHIFT
    yesterday = now_msk.date() - timedelta(days=1)

    start_utc = datetime(yesterday.year, yesterday.month, yesterday.day) - MSK_SHIFT
    end_utc = datetime(yesterday.year, yesterday.month, yesterday.day, 23, 59, 59) - MSK_SHIFT

    pretty = (
        f"{yesterday.strftime('%d.%m.%Y')} 00:00 — "
        f"{yesterday.strftime('%d.%m.%Y')} 23:59 (МСК)"
    )
    return iso_z(start_utc), iso_z(end_utc), pretty


def fmt_int(n: float | int) -> str:
    """Форматировать число с пробелами как разделителями тысяч."""
    return f"{int(round(n)):,.0f}".replace(",", " ")


def fmt_rub0(n: float | int) -> str:
    """Форматировать число в рубли без копеек."""
    return f"{int(round(n)):,.0f} ₽".replace(",", " ")


def fmt_rub2(n: float | int) -> str:
    """Форматировать число в рубли с копейками."""
    return f"{n:,.2f} ₽".replace(",", " ")


__all__ = [
    "MSK_SHIFT",
    "MSK_TZ",
    "iso_z",
    "ensure_utc",
    "msk_today_range",
    "msk_current_month_range",
    "msk_week_range",
    "msk_yesterday_range",
    "fmt_int",
    "fmt_rub0",
    "fmt_rub2",
]

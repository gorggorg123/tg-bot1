# botapp/account.py

from __future__ import annotations

import logging
from datetime import datetime
import os

from .ozon_client import OzonClient, get_client


logger = logging.getLogger(__name__)


def _fmt_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    return dt.strftime("%d.%m.%Y")


async def get_account_info_text(client: OzonClient | None = None) -> str:
    client = client or get_client()
    try:
        info = await client.get_seller_info()
    except Exception:
        logger.exception("Failed to fetch account info")
        return "⚠️ Не удалось получить данные аккаунта. Попробуйте позже."

    if not info or not isinstance(info, dict):
        return (
            "⚠️ Не удалось получить данные аккаунта.\n"
            "Проверьте, включен ли необходимый API-метод в личном кабинете Ozon."
        )

    company = info.get("company") if isinstance(info, dict) else None
    company_name = None
    if isinstance(company, dict):
        company_name = company.get("name") or company.get("legal_name")
    if not company_name and isinstance(info, dict):
        company_name = info.get("name") or info.get("company_name")

    inn = (company or {}).get("inn") if isinstance(company, dict) else None
    ogrn = (company or {}).get("ogrn") if isinstance(company, dict) else None
    if isinstance(info, dict):
        inn = inn or info.get("inn")
        ogrn = ogrn or info.get("ogrn")

    status = info.get("status") or info.get("state") if isinstance(info, dict) else None
    registered_at = _fmt_date(
        (company or {}).get("registration_date")
        or (company or {}).get("created_at")
        or (info.get("registration_date") if isinstance(info, dict) else None)
    )
    connected_at = _fmt_date(
        (company or {}).get("connected_at")
        or (info.get("connected_at") if isinstance(info, dict) else None)
    )
    region = None
    if isinstance(company, dict):
        region = company.get("country") or company.get("region")
    if isinstance(info, dict) and not region:
        region = info.get("region")
    warehouse = None
    if isinstance(info, dict):
        warehouse = info.get("warehouse") or info.get("default_store_name")
    email = info.get("email") if isinstance(info, dict) else None
    tax_system = None
    if isinstance(company, dict):
        tax_system = company.get("tax_system")
    subscription = info.get("subscription") if isinstance(info, dict) else None
    rating = None
    if isinstance(info, dict):
        statistics = info.get("statistics") or {}
        rating = statistics.get("rating") or info.get("rating")

    debug = (os.getenv("DEBUG") or "").lower() in {"1", "true", "yes"}

    lines = ["🧾 <b>Аккаунт Ozon</b>"]

    if company_name:
        lines.append(f"Компания: <b>{company_name}</b>")
    if inn:
        lines.append(f"ИНН: <code>{inn}</code>")
    if ogrn:
        lines.append(f"ОГРН: <code>{ogrn}</code>")
    if tax_system:
        lines.append(f"Налогообложение: {tax_system}")
    if region or warehouse:
        region_line = region or warehouse
        if region and warehouse and warehouse not in region_line:
            region_line = f"{region} • {warehouse}"
        lines.append(f"Регион/склад: {region_line}")
    if status:
        lines.append(f"Статус: {status}")
    if registered_at:
        lines.append(f"Регистрация: {registered_at}")
    if connected_at and connected_at != registered_at:
        lines.append(f"Подключение: {connected_at}")
    if subscription:
        sub_type = subscription.get("type") if isinstance(subscription, dict) else None
        level = subscription.get("level") if isinstance(subscription, dict) else None
        is_premium = subscription.get("is_premium") if isinstance(subscription, dict) else None
        parts = []
        if sub_type:
            parts.append(str(sub_type))
        if level:
            parts.append(str(level))
        if is_premium is not None:
            parts.append("Premium" if is_premium else "Standard")
        if parts:
            lines.append(f"Подписка: {' • '.join(parts)}")
    if rating not in (None, ""):
        try:
            rating_val = float(rating)
            lines.append(f"Средний рейтинг: {rating_val:.2f}")
        except Exception:
            pass
    if email:
        lines.append(f"Email: {email}")

    if len(lines) == 1:
        lines.append("⚠️ Не удалось разобрать данные аккаунта.")

    if debug:
        try:
            import json

            lines.append("")
            lines.append("<code>" + json.dumps(info, ensure_ascii=False) + "</code>")
        except Exception:
            pass

    return "\n".join(lines)

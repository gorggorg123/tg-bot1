# botapp/account.py

from __future__ import annotations

import json
import logging
from datetime import datetime

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
        info = await client.get_account_info()
    except Exception as e:
        logger.exception("Failed to fetch account info")
        return "⚠️ Не удалось получить данные аккаунта. Попробуйте позже."

    if not info:
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
    subscription = None
    if isinstance(info, dict):
        subscription = info.get("subscription")

    lines = ["👤 <b>Аккаунт Ozon</b>"]

    if company_name:
        lines.append(f"🏢 Компания: <b>{company_name}</b>")
    if status:
        lines.append(f"⚙️ Статус: <b>{status}</b>")
    if inn:
        lines.append(f"🧾 ИНН: <code>{inn}</code>")
    if ogrn:
        lines.append(f"📄 ОГРН: <code>{ogrn}</code>")
    if registered_at:
        lines.append(f"📅 Регистрация: {registered_at}")
    if connected_at and connected_at != registered_at:
        lines.append(f"🔌 Подключение: {connected_at}")
    if tax_system:
        lines.append(f"💼 Налогообложение: {tax_system}")
    if company_name:
        lines.append("")
    if region:
        lines.append(f"📍 Регион/склад: {region}{(' • ' + warehouse) if warehouse else ''}")
    elif warehouse:
        lines.append(f"📍 Базовый склад: {warehouse}")
    if email:
        lines.append(f"✉️ Email: {email}")
    if subscription:
        sub_type = subscription.get("type") if isinstance(subscription, dict) else None
        is_premium = subscription.get("is_premium") if isinstance(subscription, dict) else None
        status_line = f"Тип: {sub_type}" if sub_type else None
        if is_premium is not None:
            status_line = (status_line + " • " if status_line else "") + (
                "Premium" if is_premium else "Standard"
            )
        if status_line:
            lines.append(f"⭐ Подписка: {status_line}")

    if len(lines) == 1:
        lines.append(
            "⚠️ Не удалось разобрать данные аккаунта."
        )
    else:
        # На всякий случай приложим сырой JSON снизу
        lines.append("")
        lines.append("<code>" + json.dumps(info, ensure_ascii=False) + "</code>")

    return "\n".join(lines)

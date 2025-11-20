# botapp/account.py

from __future__ import annotations

import json
from datetime import datetime

from .ozon_client import OzonClient, get_client


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
        info = await client.get_company_info()
    except Exception as e:
        return (
            "⚠️ Не удалось получить данные аккаунта.\n"
            f"Ошибка: {e}"
        )

    if not info:
        return (
            "⚠️ Не удалось получить данные аккаунта.\n"
            "Проверьте, включен ли необходимый API-метод в личном кабинете Ozon."
        )

    company_name = info.get("name") or info.get("company_name")
    inn = info.get("inn")
    ogrn = info.get("ogrn")
    status = info.get("status") or info.get("state")
    registered_at = _fmt_date(info.get("registration_date") or info.get("created_at"))
    connected_at = _fmt_date(info.get("connected_at") or info.get("connected_date"))
    region = info.get("region")
    warehouse = info.get("warehouse") or info.get("default_store_name")
    email = info.get("email")

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
    if company_name:
        lines.append("")
    if region:
        lines.append(f"📍 Регион/склад: {region}{(' • ' + warehouse) if warehouse else ''}")
    elif warehouse:
        lines.append(f"📍 Базовый склад: {warehouse}")
    if email:
        lines.append(f"✉️ Email: {email}")

    if len(lines) == 1:
        lines.append("⚠️ Не удалось разобрать данные аккаунта.")
    else:
        # На всякий случай приложим сырой JSON снизу
        lines.append("")
        lines.append("<code>" + json.dumps(info, ensure_ascii=False) + "</code>")

    return "\n".join(lines)

# botapp/account.py

from __future__ import annotations

import json

from .ozon_client import OzonClient, get_client


async def get_account_info_text(client: OzonClient | None = None) -> str:
    client = client or get_client()
    try:
        info = await client.get_company_info()
    except Exception as e:
        return (
            "⚠️ Не удалось получить данные аккаунта.\n"
            f"Ошибка: {e}"
        )

    company_name = info.get("name") or info.get("company_name")
    inn = info.get("inn")
    ogrn = info.get("ogrn")
    region = info.get("region")
    email = info.get("email")

    lines = ["👤 <b>Аккаунт Ozon</b>", ""]

    if company_name:
        lines.append(f"🏢 Компания: <b>{company_name}</b>")
    if inn:
        lines.append(f"🧾 ИНН: <code>{inn}</code>")
    if ogrn:
        lines.append(f"📄 ОГРН: <code>{ogrn}</code>")
    if region:
        lines.append(f"📍 Регион: {region}")
    if email:
        lines.append(f"✉️ Email: {email}")

    # На всякий случай приложим сырой JSON снизу
    lines.append("")
    lines.append("<code>" + json.dumps(info, ensure_ascii=False) + "</code>")

    return "\n".join(lines)

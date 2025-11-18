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

    lines = ["👤 *Аккаунт Ozon*:", ""]

    if company_name:
        lines.append(f"🏢 Компания: *{company_name}*")
    if inn:
        lines.append(f"🧾 ИНН: `{inn}`")
    if ogrn:
        lines.append(f"📄 ОГРН: `{ogrn}`")
    if region:
        lines.append(f"📍 Регион: {region}")
    if email:
        lines.append(f"✉️ Email: {email}")

    # На всякий случай приложим сырой JSON снизу
    lines.append("")
    lines.append("`" + json.dumps(info, ensure_ascii=False) + "`")

    return "\n".join(lines)

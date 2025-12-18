# botapp/ai_client.py
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_BASE_URL_ENV = "OPENAI_BASE_URL"
OPENAI_MODEL_ENV = "OPENAI_MODEL"
OPENAI_TIMEOUT_S_ENV = "OPENAI_TIMEOUT_S"

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT_S = 35.0

ULYANOVA_PATH_ENV = "ULYANOVA_PATH"
DEFAULT_ULYANOVA_PATHS = (
    "data/ulyanova.txt",
    "data/ulyanova.md",
    "ulyanova.txt",
    "ulyanova.md",
)


class AIClientError(RuntimeError):
    pass


def _get_env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _load_ulyanova_text() -> str:
    p = _get_env(ULYANOVA_PATH_ENV)
    candidates: list[str] = []
    if p:
        candidates.append(p)
    candidates.extend(DEFAULT_ULYANOVA_PATHS)

    for c in candidates:
        try:
            path = Path(c)
            if path.exists() and path.is_file():
                txt = path.read_text(encoding="utf-8", errors="replace").strip()
                if txt:
                    return txt
        except Exception:
            continue
    return ""


def _normalize(s: str) -> str:
    return (s or "").strip()


def _clamp_text(s: str, n: int) -> str:
    s = _normalize(s)
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)].rstrip() + "…"


@dataclass
class OpenAIConfig:
    api_key: str
    base_url: str
    model: str
    timeout_s: float


def _config() -> OpenAIConfig:
    api_key = _get_env(OPENAI_API_KEY_ENV)
    if not api_key:
        raise AIClientError(f"Не задан {OPENAI_API_KEY_ENV} (ключ OpenAI)")

    base_url = _get_env(OPENAI_BASE_URL_ENV, "https://api.openai.com/v1").rstrip("/")
    model = _get_env(OPENAI_MODEL_ENV, DEFAULT_MODEL)
    try:
        timeout_s = float(_get_env(OPENAI_TIMEOUT_S_ENV, str(DEFAULT_TIMEOUT_S)))
    except Exception:
        timeout_s = DEFAULT_TIMEOUT_S

    return OpenAIConfig(api_key=api_key, base_url=base_url, model=model, timeout_s=timeout_s)


async def _chat_completion(
    *,
    system: str,
    user: str,
    temperature: float = 0.4,
    max_tokens: int = 350,
) -> str:
    cfg = _config()
    url = f"{cfg.base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}
    payload = {
        "model": cfg.model,
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
        "messages": [
            {"role": "system", "content": _normalize(system)},
            {"role": "user", "content": _normalize(user)},
        ],
    }

    retries = 2
    last_err: Exception | None = None

    async with httpx.AsyncClient(timeout=httpx.Timeout(cfg.timeout_s)) as client:
        for attempt in range(retries + 1):
            try:
                r = await client.post(url, headers=headers, json=payload)
                r.raise_for_status()
                data = r.json()
                choices = data.get("choices") if isinstance(data, dict) else None
                if isinstance(choices, list) and choices:
                    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
                    content = (msg or {}).get("content") if isinstance(msg, dict) else None
                    if isinstance(content, str):
                        return _normalize(content)
                raise AIClientError(f"Некорректный ответ OpenAI: {data}")
            except Exception as exc:
                last_err = exc
                if attempt >= retries:
                    break
                await asyncio.sleep(0.6 + attempt * 0.8)

    raise AIClientError(f"OpenAI запрос не удался: {last_err}")


def _base_rules() -> str:
    ulyanova = _load_ulyanova_text()
    if ulyanova:
        return (
            "Ниже — база правил/подходов (Ульянова). Соблюдай их всегда.\n\n"
            f"{ulyanova}\n"
        )
    return ""


async def generate_review_reply(
    *,
    review_text: str,
    product_name: str | None = None,
    rating: int | None = None,
    previous_answer: str | None = None,
    user_prompt: str | None = None,
) -> str:
    sys = (
        "Ты — помощник продавца на маркетплейсе Ozon. "
        "Пиши ответы на отзывы по-русски, вежливо, без воды, без обещаний того, что нельзя гарантировать. "
        "Не упоминай внутренние процессы Ozon. "
        "Если отзыв негативный — сначала сочувствие, затем короткое решение/инструкция, затем приглашение уточнить детали."
        "\n\n"
        + _base_rules()
    )

    user = []
    if product_name:
        user.append(f"Товар: {product_name}")
    if rating is not None:
        user.append(f"Оценка: {rating}/5")
    user.append("Текст отзыва:")
    user.append(review_text or "")

    if previous_answer:
        user.append("\nТекущий черновик (если нужно улучшить/переписать):")
        user.append(previous_answer)

    if user_prompt:
        user.append("\nПожелания к ответу (пересборка):")
        user.append(user_prompt)

    user.append(
        "\nСгенерируй один готовый ответ (без списков кнопок и без служебных комментариев). "
        "Максимум 500 символов, если можно — короче."
    )

    return await _chat_completion(system=sys, user="\n".join(user), temperature=0.5, max_tokens=260)


async def generate_answer_for_question(
    question_text: str,
    *,
    product_name: str | None = None,
    existing_answer: str | None = None,
    user_prompt: str | None = None,
) -> str:
    sys = (
        "Ты — помощник продавца на Ozon. "
        "Отвечай строго по делу, без лишних оправданий. "
        "Не обещай сроки доставки (это зона Ozon), не предлагай перейти в мессенджеры. "
        "Если данных недостаточно — задай 1 уточняющий вопрос в конце."
        "\n\n"
        + _base_rules()
    )

    user = []
    if product_name:
        user.append(f"Товар: {product_name}")
    user.append("Вопрос покупателя:")
    user.append(question_text or "")

    if existing_answer:
        user.append("\nТекущий черновик (если нужно улучшить):")
        user.append(existing_answer)

    if user_prompt:
        user.append("\nПожелания к ответу:")
        user.append(user_prompt)

    user.append(
        "\nСгенерируй один готовый ответ. "
        "Максимум 450 символов, тон — профессиональный и доброжелательный."
    )

    return await _chat_completion(system=sys, user="\n".join(user), temperature=0.4, max_tokens=240)


async def generate_chat_reply(*, messages_text: str, user_prompt: str | None = None) -> str:
    sys = (
        "Ты — помощник продавца в чате Ozon. "
        "Отвечай только как продавец. "
        "Не повторяй историю полностью; дай один короткий ответ по последнему сообщению покупателя. "
        "Если нужно — уточни детали одним вопросом. "
        "Не упоминай, что ты ИИ."
        "\n\n"
        + _base_rules()
    )

    user = []
    if user_prompt:
        user.append("Пожелания/правила ответа:")
        user.append(user_prompt)
        user.append("")

    user.append("Переписка (контекст):")
    user.append(_clamp_text(messages_text or "", 7000))
    user.append(
        "\nСформируй ОДИН ответ продавца на последнее сообщение BUYER. "
        "Не добавляй метки BUYER/SELLER в ответ."
    )

    return await _chat_completion(system=sys, user="\n".join(user), temperature=0.45, max_tokens=220)


__all__ = [
    "AIClientError",
    "generate_review_reply",
    "generate_answer_for_question",
    "generate_chat_reply",
]

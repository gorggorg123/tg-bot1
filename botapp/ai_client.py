# botapp/ai_client.py
"""
AI-клиент для генерации ответов через OpenAI API.

Функции:
- generate_review_reply: генерация ответа на отзыв
- generate_answer_for_question: генерация ответа на вопрос покупателя
- generate_chat_reply: генерация ответа в чате

Особенности:
- Экспоненциальный backoff с jitter для retry
- Метрики (latency, tokens, cost)
- Кэширование style guide
- Интеграция с AI memory для примеров
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from botapp.ai_memory import fetch_examples, format_examples_block

logger = logging.getLogger(__name__)

# ============================================================================
# Environment variables
# ============================================================================
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_BASE_URL_ENV = "OPENAI_BASE_URL"
OPENAI_MODEL_ENV = "OPENAI_MODEL"
OPENAI_TIMEOUT_S_ENV = "OPENAI_TIMEOUT_S"
OPENAI_MAX_RETRIES_ENV = "OPENAI_MAX_RETRIES"
ITOM_QNA_DIGEST_PATH_ENV = "ITOM_QNA_DIGEST_PATH"

# Defaults
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT_S = 35.0
DEFAULT_MAX_RETRIES = 3

# Retry configuration
RETRY_MIN_WAIT = 0.5  # seconds
RETRY_MAX_WAIT = 10.0  # seconds
RETRY_MULTIPLIER = 2.0
RETRY_JITTER = 0.3  # 30% jitter

ULYANOVA_PATH_ENV = "ULYANOVA_PATH"
DEFAULT_ULYANOVA_PATHS = (
    "data/ulyanova.txt",
    "data/ulyanova.md",
    "ulyanova.txt",
    "ulyanova.md",
)

# Approximate token costs (USD per 1K tokens) for gpt-4o-mini
TOKEN_COSTS = {
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4o": {"input": 0.005, "output": 0.015},
    "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
}
DEFAULT_TOKEN_COST = {"input": 0.0005, "output": 0.0015}


# ============================================================================
# Metrics
# ============================================================================
@dataclass
class AIMetrics:
    """Метрики AI-клиента."""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_retries: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_latency_ms: float = 0.0
    estimated_cost_usd: float = 0.0
    last_request_at: str | None = None
    errors_by_type: dict[str, int] = field(default_factory=dict)
    
    @property
    def avg_latency_ms(self) -> float:
        if self.successful_requests == 0:
            return 0.0
        return self.total_latency_ms / self.successful_requests
    
    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.successful_requests / self.total_requests
    
    def as_dict(self) -> dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "success_rate": f"{self.success_rate:.1%}",
            "total_retries": self.total_retries,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "estimated_cost_usd": round(self.estimated_cost_usd, 4),
            "last_request_at": self.last_request_at,
            "errors_by_type": dict(self.errors_by_type),
        }


# Global metrics instance
_ai_metrics = AIMetrics()


def get_ai_metrics() -> AIMetrics:
    """Получить текущие метрики AI-клиента."""
    return _ai_metrics


def reset_ai_metrics() -> None:
    """Сбросить метрики AI-клиента."""
    global _ai_metrics
    _ai_metrics = AIMetrics()


# ============================================================================
# Response cache (simple in-memory)
# ============================================================================
@dataclass
class CacheEntry:
    response: str
    created_at: float
    ttl_seconds: float = 300.0  # 5 minutes default
    
    @property
    def is_expired(self) -> bool:
        return time.time() - self.created_at > self.ttl_seconds


_response_cache: dict[str, CacheEntry] = {}
CACHE_MAX_SIZE = 100
CACHE_TTL_SECONDS = 300.0  # 5 minutes


def _cache_key(system: str, user: str, model: str) -> str:
    """Генерирует ключ кэша для запроса."""
    content = f"{model}|{system[:500]}|{user[:500]}"
    return hashlib.md5(content.encode()).hexdigest()


def _get_cached_response(key: str) -> str | None:
    """Получает закэшированный ответ если он есть и не истёк."""
    entry = _response_cache.get(key)
    if entry and not entry.is_expired:
        logger.debug("AI cache hit: key=%s", key[:16])
        return entry.response
    return None


def _set_cached_response(key: str, response: str) -> None:
    """Сохраняет ответ в кэш."""
    # Очистка старых записей если кэш переполнен
    if len(_response_cache) >= CACHE_MAX_SIZE:
        expired_keys = [k for k, v in _response_cache.items() if v.is_expired]
        for k in expired_keys:
            del _response_cache[k]
        # Если всё ещё много — удаляем самые старые
        if len(_response_cache) >= CACHE_MAX_SIZE:
            oldest_keys = sorted(_response_cache.keys(), 
                                  key=lambda k: _response_cache[k].created_at)[:CACHE_MAX_SIZE // 4]
            for k in oldest_keys:
                del _response_cache[k]
    
    _response_cache[key] = CacheEntry(response=response, created_at=time.time(), ttl_seconds=CACHE_TTL_SECONDS)


# ============================================================================
# Style cache
# ============================================================================
_style_cache: dict[str, str | float | Path | None] = {"path": None, "mtime": None, "text": None}


# ============================================================================
# Exceptions
# ============================================================================
class AIClientError(RuntimeError):
    """Базовая ошибка AI-клиента."""
    pass


class AIRateLimitError(AIClientError):
    """Ошибка rate limit от OpenAI."""
    pass


class AIQuotaExceededError(AIClientError):
    """Превышена квота OpenAI."""
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


def _style_guide_path() -> Path:
    env_path = _get_env(ITOM_QNA_DIGEST_PATH_ENV)
    if env_path:
        try:
            return Path(env_path).expanduser().resolve()
        except Exception:
            pass
    base = Path(__file__).resolve().parents[1]
    return (base / "data" / "itom_qna_digest.txt").resolve()


def _load_style_guide() -> str:
    global _style_cache
    path = _style_guide_path()

    try:
        mtime = path.stat().st_mtime
    except Exception:
        return ""

    cached_path = _style_cache.get("path")
    cached_mtime = _style_cache.get("mtime")
    if cached_path == path and cached_mtime == mtime:
        cached_text = _style_cache.get("text")
        return cached_text or ""

    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""

    _style_cache = {"path": path, "mtime": mtime, "text": text}
    logger.info("ITOM style guide loaded: %d chars from %s", len(text), path)
    return text


def _normalize(s: str) -> str:
    return (s or "").strip()


def _clamp_text(s: str, n: int) -> str:
    s = _normalize(s)
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)].rstrip() + "…"


def _maybe_add_soft_emoji(reply: str, rating: int | None) -> str:
    if not reply:
        return reply
    try:
        r = int(rating) if rating is not None else None
    except Exception:
        r = None
    if r is None or r < 4:
        return reply

    friendly_emojis = ["😊", "🙂", "❤️"]
    if any(e in reply for e in friendly_emojis):
        return reply

    return reply.rstrip() + " " + friendly_emojis[0]


@dataclass
class OpenAIConfig:
    """Конфигурация OpenAI API."""
    api_key: str
    base_url: str
    model: str
    timeout_s: float
    max_retries: int = DEFAULT_MAX_RETRIES


def _config() -> OpenAIConfig:
    """Загружает конфигурацию из переменных окружения."""
    api_key = _get_env(OPENAI_API_KEY_ENV)
    if not api_key:
        raise AIClientError(f"Не задан {OPENAI_API_KEY_ENV} (ключ OpenAI)")

    base_url = _get_env(OPENAI_BASE_URL_ENV, "https://api.openai.com/v1").rstrip("/")
    model = _get_env(OPENAI_MODEL_ENV, DEFAULT_MODEL)
    
    try:
        timeout_s = float(_get_env(OPENAI_TIMEOUT_S_ENV, str(DEFAULT_TIMEOUT_S)))
    except (ValueError, TypeError):
        timeout_s = DEFAULT_TIMEOUT_S
    
    try:
        max_retries = int(_get_env(OPENAI_MAX_RETRIES_ENV, str(DEFAULT_MAX_RETRIES)))
    except (ValueError, TypeError):
        max_retries = DEFAULT_MAX_RETRIES

    return OpenAIConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout_s=timeout_s,
        max_retries=max_retries,
    )


def _calculate_backoff(attempt: int) -> float:
    """Вычисляет задержку с экспоненциальным backoff и jitter."""
    base_delay = RETRY_MIN_WAIT * (RETRY_MULTIPLIER ** attempt)
    delay = min(base_delay, RETRY_MAX_WAIT)
    # Добавляем jitter (±30%)
    jitter = delay * RETRY_JITTER * (2 * random.random() - 1)
    return max(0.1, delay + jitter)


def _estimate_tokens(text: str) -> int:
    """Примерная оценка количества токенов (4 символа ~ 1 токен для русского)."""
    return max(1, len(text) // 3)


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Оценка стоимости запроса в USD."""
    costs = TOKEN_COSTS.get(model, DEFAULT_TOKEN_COST)
    input_cost = (input_tokens / 1000) * costs["input"]
    output_cost = (output_tokens / 1000) * costs["output"]
    return input_cost + output_cost


def _is_retryable_error(exc: Exception) -> bool:
    """Проверяет, можно ли повторить запрос при данной ошибке."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        # Retry on: 429 (rate limit), 500, 502, 503, 504
        return status in (429, 500, 502, 503, 504)
    if isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout)):
        return True
    return False


def _classify_error(exc: Exception) -> str:
    """Классифицирует ошибку для метрик."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return "rate_limit"
        elif status == 401:
            return "auth_error"
        elif status == 403:
            return "forbidden"
        elif status >= 500:
            return "server_error"
        return f"http_{status}"
    if isinstance(exc, httpx.ConnectError):
        return "connect_error"
    if isinstance(exc, (httpx.ReadTimeout, httpx.ConnectTimeout)):
        return "timeout"
    return "unknown"


async def _chat_completion(
    *,
    system: str,
    user: str,
    temperature: float = 0.4,
    max_tokens: int = 350,
    use_cache: bool = True,
) -> str:
    """Выполняет запрос к OpenAI Chat Completion API.
    
    Args:
        system: Системный промпт
        user: Сообщение пользователя
        temperature: Температура (0.0-2.0)
        max_tokens: Максимум токенов в ответе
        use_cache: Использовать кэш ответов
    
    Returns:
        Сгенерированный текст
    
    Raises:
        AIClientError: При ошибке API
        AIRateLimitError: При превышении rate limit
        AIQuotaExceededError: При превышении квоты
    """
    global _ai_metrics
    
    cfg = _config()
    
    # Проверяем кэш
    if use_cache:
        cache_key = _cache_key(system, user, cfg.model)
        cached = _get_cached_response(cache_key)
        if cached:
            return cached
    
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

    last_err: Exception | None = None
    _ai_metrics.total_requests += 1
    _ai_metrics.last_request_at = datetime.now(timezone.utc).isoformat()
    
    input_tokens = _estimate_tokens(system + user)
    start_time = time.perf_counter()

    logger.info("OpenAI request: model=%s max_tokens=%s input_chars=%s", 
                cfg.model, max_tokens, len(system) + len(user))
    
    async with httpx.AsyncClient(timeout=httpx.Timeout(cfg.timeout_s)) as client:
        for attempt in range(cfg.max_retries + 1):
            try:
                r = await client.post(url, headers=headers, json=payload)
                r.raise_for_status()
                data = r.json()
                
                latency_ms = (time.perf_counter() - start_time) * 1000
                
                logger.debug("OpenAI response: status=%s, latency=%.1fms", r.status_code, latency_ms)
                
                choices = data.get("choices") if isinstance(data, dict) else None
                if isinstance(choices, list) and choices:
                    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
                    content = (msg or {}).get("content") if isinstance(msg, dict) else None
                    
                    if isinstance(content, str):
                        result = _normalize(content)
                        
                        # Извлекаем usage из ответа
                        usage = data.get("usage", {})
                        actual_input_tokens = usage.get("prompt_tokens", input_tokens)
                        actual_output_tokens = usage.get("completion_tokens", _estimate_tokens(result))
                        
                        # Обновляем метрики
                        _ai_metrics.successful_requests += 1
                        _ai_metrics.total_latency_ms += latency_ms
                        _ai_metrics.total_input_tokens += actual_input_tokens
                        _ai_metrics.total_output_tokens += actual_output_tokens
                        _ai_metrics.estimated_cost_usd += _estimate_cost(
                            cfg.model, actual_input_tokens, actual_output_tokens
                        )
                        
                        logger.info(
                            "OpenAI ok: chars=%s latency=%.0fms tokens=%s/%s cost=$%.4f", 
                            len(result), latency_ms, actual_input_tokens, actual_output_tokens,
                            _estimate_cost(cfg.model, actual_input_tokens, actual_output_tokens)
                        )
                        
                        # Сохраняем в кэш
                        if use_cache:
                            _set_cached_response(cache_key, result)
                        
                        return result
                    else:
                        logger.warning("OpenAI response: content is not a string, type=%s", type(content))
                else:
                    logger.warning("OpenAI response: no choices found")
                
                raise AIClientError(f"Некорректный ответ OpenAI: {data}")
                
            except httpx.HTTPStatusError as exc:
                last_err = exc
                status = exc.response.status_code
                error_type = _classify_error(exc)
                
                logger.warning(
                    "OpenAI HTTP error (attempt %s/%s): status=%s type=%s response=%s", 
                    attempt + 1, cfg.max_retries + 1, status, error_type,
                    exc.response.text[:300] if exc.response else "N/A"
                )
                
                # Специфичные ошибки
                if status == 429:
                    _ai_metrics.errors_by_type["rate_limit"] = _ai_metrics.errors_by_type.get("rate_limit", 0) + 1
                    if "quota" in (exc.response.text or "").lower():
                        _ai_metrics.failed_requests += 1
                        raise AIQuotaExceededError("Превышена квота OpenAI")
                elif status == 401:
                    _ai_metrics.failed_requests += 1
                    _ai_metrics.errors_by_type["auth_error"] = _ai_metrics.errors_by_type.get("auth_error", 0) + 1
                    raise AIClientError("Неверный API ключ OpenAI")
                
                if not _is_retryable_error(exc) or attempt >= cfg.max_retries:
                    break
                
                _ai_metrics.total_retries += 1
                delay = _calculate_backoff(attempt)
                logger.info("Retrying in %.2fs...", delay)
                await asyncio.sleep(delay)
                
            except Exception as exc:
                last_err = exc
                error_type = _classify_error(exc)
                
                logger.warning(
                    "OpenAI request error (attempt %s/%s): type=%s error=%r", 
                    attempt + 1, cfg.max_retries + 1, error_type, exc
                )
                
                if not _is_retryable_error(exc) or attempt >= cfg.max_retries:
                    break
                
                _ai_metrics.total_retries += 1
                delay = _calculate_backoff(attempt)
                logger.info("Retrying in %.2fs...", delay)
                await asyncio.sleep(delay)

    # Финальная ошибка
    _ai_metrics.failed_requests += 1
    error_type = _classify_error(last_err) if last_err else "unknown"
    _ai_metrics.errors_by_type[error_type] = _ai_metrics.errors_by_type.get(error_type, 0) + 1
    
    logger.error("OpenAI failed after %s attempts: %r", cfg.max_retries + 1, last_err)
    
    if isinstance(last_err, httpx.HTTPStatusError) and last_err.response.status_code == 429:
        raise AIRateLimitError(f"Rate limit OpenAI: {last_err}")
    
    raise AIClientError(f"OpenAI запрос не удался: {last_err}")


def _base_rules() -> str:
    ulyanova = _load_ulyanova_text()
    if ulyanova:
        return (
            "Ниже — база правил/подходов (Ульянова). Соблюдай их всегда.\n\n"
            f"{ulyanova}\n"
        )
    return ""


def _style_guide_block() -> str:
    style = _load_style_guide()
    if not style:
        return ""
    return "Справочник стиля и шаблонов ITOM:\n" + style


def _examples_block(
    kind: str, *, input_text: str, product_id: str | None = None, limit: int = 6
) -> str:
    examples = fetch_examples(
        kind=kind, input_text=input_text, product_id=product_id, limit=limit
    )
    return format_examples_block(examples)


async def generate_review_reply(
    *,
    review_text: str,
    product_name: str | None = None,
    sku: str | None = None,
    rating: int | None = None,
    previous_answer: str | None = None,
    user_prompt: str | None = None,
) -> str:
    is_text_missing = not (review_text or "").strip() or review_text.strip() == "(отзыв без текста)"
    sys = (
        "Ты — помощник продавца на маркетплейсе Ozon. "
        "Пиши ответы на отзывы по-русски, вежливо, без воды, без обещаний того, что нельзя гарантировать. "
        "Не упоминай внутренние процессы Ozon и не выдумывай факты или сроки доставки. "
        "Если отзыв негативный — сначала сочувствие, затем короткое решение/инструкция, затем приглашение уточнить детали. "
        "Если вопрос про доставку — напомни, что доставку организует Ozon, без конкретных дат."
        "\n\n"
        + _base_rules()
    )

    if is_text_missing:
        sys = (
            sys
            + "\n\nЕсли отзыв без текста: поблагодари за оценку, предложи написать детали при проблеме, ответ должен быть лаконичным и с одним уместным эмодзи."
        )

    style_block = _style_guide_block()
    if style_block:
        sys = sys + "\n\n" + style_block

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

    examples_block = _examples_block("review", input_text=review_text, product_id=sku)
    if examples_block:
        sys = sys + "\n\n" + examples_block

    user.append(
        "\nСгенерируй один готовый ответ (без списков кнопок и без служебных комментариев). "
        "Максимум 500 символов, если можно — короче."
    )

    reply = await _chat_completion(system=sys, user="\n".join(user), temperature=0.5, max_tokens=260)
    return _maybe_add_soft_emoji(reply, rating)


async def generate_answer_for_question(
    question_text: str,
    *,
    product_name: str | None = None,
    sku: str | None = None,
    existing_answer: str | None = None,
    user_prompt: str | None = None,
) -> str:
    sys = (
        "Ты — помощник продавца на Ozon. "
        "Отвечай строго по делу, без лишних оправданий. "
        "Не обещай сроки доставки (это зона Ozon), не предлагай перейти в мессенджеры. "
        "Если данных недостаточно — задай 1 уточняющий вопрос в конце. "
        "Не выдумывай детали товара и не обещай невозможное."
        "\n\n"
        + _base_rules()
    )

    style_block = _style_guide_block()
    if style_block:
        sys = sys + "\n\n" + style_block

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

    examples_block = _examples_block(
        "question", input_text=question_text, product_id=str(sku) if sku is not None else None
    )
    if examples_block:
        sys = sys + "\n\n" + examples_block

    user.append(
        "\nСгенерируй один готовый ответ. "
        "Максимум 450 символов, тон — профессиональный и доброжелательный."
    )

    return await _chat_completion(system=sys, user="\n".join(user), temperature=0.4, max_tokens=240)


async def generate_chat_reply(*, messages_text: str, user_prompt: str | None = None) -> str:
    sys = (
        "Ты — помощник продавца на маркетплейсе Ozon. "
        "Сейчас ты работаешь в РАЗДЕЛЕ ЧАТОВ — это переписка с покупателем в чате Ozon. "
        "Это НЕ отзыв и НЕ вопрос под товаром, это именно чат (личная переписка с покупателем). "
        "Отвечай только как продавец в чате. "
        "Не повторяй историю полностью; дай один дружелюбный короткий ответ по последнему сообщению покупателя. "
        "Допускается 1–2 уместных эмодзи (например 😊, ❤️). "
        "Если нужно — уточни детали одним вопросом. "
        "Не упоминай, что ты ИИ. Не обещай сроки доставки и не выдумывай детали заказа."
        "\n\n"
        + _base_rules()
    )

    style_block = _style_guide_block()
    if style_block:
        sys = sys + "\n\n" + style_block

    user = []
    if user_prompt:
        user.append("Пожелания/правила ответа:")
        user.append(user_prompt)
        user.append("")
    
    user.append("=== ЧАТ С ПОКУПАТЕЛЕМ НА OZON ===")
    user.append("Ниже переписка из чата с покупателем на маркетплейсе Ozon.")
    user.append("BUYER — это сообщения покупателя, SELLER — это твои предыдущие ответы (если есть).")
    user.append("")
    user.append("Переписка (контекст):")
    user.append(_clamp_text(messages_text or "", 7000))

    examples_block = _examples_block(
        "chat", input_text=messages_text, product_id=None, limit=4
    )
    if examples_block:
        sys = sys + "\n\n" + examples_block
    user.append(
        "\n=== ЗАДАНИЕ ==="
        "\nСформируй ОДИН готовый ответ продавца в чате на последнее сообщение BUYER. "
        "Это ответ в чате с покупателем на Ozon, не отзыв и не ответ на вопрос под товаром. "
        "Не добавляй метки BUYER/SELLER в ответ. "
        "Ответ должен быть естественным, как будто ты продавец, общающийся с покупателем в чате."
    )

    reply = await _chat_completion(system=sys, user="\n".join(user), temperature=0.45, max_tokens=220)
    return _maybe_add_soft_emoji(reply, None)


async def extract_cdek_shipment_data(conversation_text: str) -> dict[str, Any]:
    """
    Извлечь данные для отправки СДЭК из переписки с клиентом через AI.
    
    Args:
        conversation_text: Текст переписки с клиентом
    
    Returns:
        dict с полями:
        - recipient_fio: ФИО получателя
        - recipient_phone: Телефон получателя
        - recipient_city: Город получателя
        - recipient_address: Адрес получателя для доставки до двери (если найден)
        - delivery_pvz_address: Адрес/ориентир ПВЗ (если найден в переписке)
        - delivery_pvz_code: Код ПВЗ (если найден, иначе None)
        - package: dict с weight_kg, length_cm, width_cm, height_cm, description
        - confidence: float 0..1 (уверенность в извлечении)
        - missing_fields: list[str] (список недостающих полей)
    """
    sys = (
        "Ты — помощник для извлечения данных отправки СДЭК из переписки с клиентом. "
        "Твоя задача — извлечь структурированные данные из текста переписки. "
        "Верни ТОЛЬКО валидный JSON без дополнительного текста, комментариев или объяснений.\n\n"
        "Формат JSON:\n"
        "{\n"
        '  "recipient_fio": "ФИО получателя или null",\n'
        '  "recipient_phone": "телефон в формате +79991234567 или null",\n'
        '  "recipient_city": "название города или null",\n'
        '  "recipient_address": "адрес для доставки до двери или null",\n'
        '  "delivery_pvz_address": "адрес/ориентир ПВЗ (если указан в переписке) или null",\n'
        '  "delivery_pvz_code": "код ПВЗ (например MSK2279) или null",\n'
        '  "package": {\n'
        '    "weight_kg": число в килограммах или null (НЕ обязательное поле),\n'
        '    "length_cm": число в сантиметрах или null,\n'
        '    "width_cm": число в сантиметрах или null,\n'
        '    "height_cm": число в сантиметрах или null,\n'
        '    "description": "описание товара или null",\n'
        '    "amount": целое число (количество мест/товаров) или null,\n'
        '    "cost_rub": число (стоимость 1 товара в рублях) или null,\n'
        '    "payment_rub": число (сумма наложенного платежа в рублях) или null\n'
        '  },\n'
        '  "confidence": число от 0 до 1 (уверенность),\n'
        '  "missing_fields": ["список", "недостающих", "полей"]\n'
        "}\n\n"
        "Правила извлечения:\n"
        "- ФИО получателя: ищи полные имена (Фамилия Имя Отчество) в любом порядке, даже если они в разных строках\n"
        "- Телефон: ищи номера в форматах 8XXXXXXXXXX, +7XXXXXXXXXX, 7XXXXXXXXXX, или просто последовательность из 10-11 цифр\n"
        "- Город: ищи названия городов (если в тексте есть упоминание города, адреса, или ПВЗ с городом)\n"
        "- recipient_address: заполняй, если покупатель дал полный адрес для доставки до двери\n"
        "- delivery_pvz_address: заполняй, если покупатель написал адрес/ориентир ПВЗ, но без кода\n"
        "- delivery_pvz_code: заполняй, если в переписке есть явный код ПВЗ (буквы+цифры)\n"
        "- Если данные не найдены, используй null\n"
        "- Не выдумывай вес и габариты: если нет явного числа в переписке, ставь null\n"
        "- Телефон должен быть в формате +7XXXXXXXXXX (добавь +7 если номер начинается с 8 или 7)\n"
        "- Вес в килограммах (НЕ обязательное поле, можно null)\n"
        "- amount: если в переписке есть количество, верни целое число, иначе null\n"
        "- cost_rub: если в переписке есть цена за единицу, верни число, иначе null\n"
        "- payment_rub: если явно сказано про наложенный платеж, верни сумму; иначе 0 или null\n"
        "- confidence: 1.0 если все ОБЯЗАТЕЛЬНЫЕ данные найдены (ФИО, телефон, город), меньше если что-то отсутствует\n"
        "- missing_fields: список ОБЯЗАТЕЛЬНЫХ полей, которые не удалось извлечь (ФИО, телефон, город). Вес НЕ включай в missing_fields\n"
        "- Верни ТОЛЬКО JSON, без markdown разметки, без ```json```, без текста до/после"
    )
    
    user_text = (
        "Извлеки данные для отправки СДЭК из следующей переписки:\n\n"
        f"{_clamp_text(conversation_text, 4000)}\n\n"
        "Верни ТОЛЬКО валидный JSON без дополнительного текста."
    )
    
    try:
        response = await _chat_completion(
            system=sys,
            user=user_text,
            temperature=0.2,  # Низкая температура для более точного извлечения
            max_tokens=500,
            use_cache=False,  # Не кэшируем, так как переписки разные
        )
        
        # Очищаем ответ от возможных markdown разметки
        response = response.strip()
        if response.startswith("```"):
            # Убираем markdown блоки
            lines = response.split("\n")
            response = "\n".join([l for l in lines if not l.strip().startswith("```")])
        response = response.strip()
        
        # Парсим JSON
        import json
        try:
            data = json.loads(response)
        except json.JSONDecodeError:
            # Пробуем найти JSON в тексте
            import re
            json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
            else:
                raise ValueError(f"Не удалось найти JSON в ответе: {response[:200]}")
        
        # Валидация и нормализация структуры
        # Безопасный доступ к package (может быть None или отсутствовать)
        package_data = data.get("package")
        if not isinstance(package_data, dict):
            package_data = {}
        
        result = {
            "recipient_fio": data.get("recipient_fio") or None,
            "recipient_phone": data.get("recipient_phone") or None,
            "recipient_city": data.get("recipient_city") or None,
            "recipient_address": data.get("recipient_address") or None,
            "delivery_pvz_address": data.get("delivery_pvz_address") or None,
            "delivery_pvz_code": data.get("delivery_pvz_code") or None,
            "package": {
                "weight_kg": package_data.get("weight_kg"),
                "length_cm": package_data.get("length_cm"),
                "width_cm": package_data.get("width_cm"),
                "height_cm": package_data.get("height_cm"),
                "description": package_data.get("description") or None,
                "amount": package_data.get("amount"),
                "cost_rub": package_data.get("cost_rub"),
                "payment_rub": package_data.get("payment_rub"),
            },
            "confidence": float(data.get("confidence", 0.0)) if data.get("confidence") is not None else 0.0,
            "missing_fields": list(data.get("missing_fields", [])) if isinstance(data.get("missing_fields"), list) else [],
        }
        
        # Определяем недостающие ОБЯЗАТЕЛЬНЫЕ поля (вес НЕ обязателен)
        missing = []
        if not result["recipient_fio"]:
            missing.append("recipient_fio")
        if not result["recipient_phone"]:
            missing.append("recipient_phone")
        if not result["recipient_city"]:
            missing.append("recipient_city")
        # package.weight_kg - НЕ обязательное поле, не включаем в missing
        
        result["missing_fields"] = missing
        if not result["confidence"]:
            # Автоматически вычисляем confidence на основе заполненности ОБЯЗАТЕЛЬНЫХ полей
            total_fields = 3  # fio, phone, city (вес не учитываем)
            filled_fields = total_fields - len(missing)
            result["confidence"] = filled_fields / total_fields if total_fields > 0 else 0.0
        
        logger.info(
            "CDEK extraction: confidence=%.2f, missing=%s",
            result["confidence"],
            result["missing_fields"],
        )
        
        return result
        
    except Exception as e:
        logger.error("CDEK extraction error: %s", e, exc_info=True)
        # Возвращаем пустую структуру с ошибкой
        return {
            "recipient_fio": None,
            "recipient_phone": None,
            "recipient_city": None,
            "recipient_address": None,
            "delivery_pvz_address": None,
            "delivery_pvz_code": None,
            "package": {
                "weight_kg": None,
                "length_cm": None,
                "width_cm": None,
                "height_cm": None,
                "description": None,
                "amount": None,
                "cost_rub": None,
                "payment_rub": None,
            },
            "confidence": 0.0,
            "missing_fields": ["all"],
            "error": str(e),
        }


__all__ = [
    # Exceptions
    "AIClientError",
    "AIRateLimitError",
    "AIQuotaExceededError",
    # Generation functions
    "generate_review_reply",
    "generate_answer_for_question",
    "generate_chat_reply",
    "extract_cdek_shipment_data",
    # Metrics
    "AIMetrics",
    "get_ai_metrics",
    "reset_ai_metrics",
]

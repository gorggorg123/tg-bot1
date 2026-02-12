# botapp/sections/cdek/logic.py
"""Бизнес-логика для раздела CDEK."""
from __future__ import annotations

import logging
import re
import time
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from botapp.api.cdek_client import get_cdek_client, CdekAPIError, CdekAuthError
from botapp.config import CdekConfig, load_cdek_config

logger = logging.getLogger(__name__)


# Глобальный конфиг для переиспользования
_cached_config: Optional[CdekConfig] = None


def _get_cached_config() -> CdekConfig:
    """Получить конфигурацию CDEK с кэшированием (избегаем повторной загрузки)."""
    global _cached_config
    if _cached_config is None:
        _cached_config = load_cdek_config()
    return _cached_config


def _normalize_order_type(value: Any) -> int:
    """Нормализовать тип заказа CDEK: только 1 (интернет-магазин) или 2 (доставка)."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 1
    return parsed if parsed in (1, 2) else 1


def _order_type_candidates(preferred: Any) -> List[int]:
    """
    Сформировать порядок попыток для типа заказа.
    Сначала тип из конфига, затем альтернативный.
    """
    first = _normalize_order_type(preferred)
    second = 2 if first == 1 else 1
    return [first, second]


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _build_cdek_items(package: Dict[str, Any], weight_grams: int) -> List[Dict[str, Any]]:
    """
    Сформировать обязательные items для CDEK internet-shop (type=1).

    Даже если цена/количество не извлечены из чата, отправляем валидный
    минимальный item, чтобы API принял заказ.
    """
    description = str(package.get("description") or "Товар").strip()
    if not description:
        description = "Товар"

    amount = max(1, _to_int(package.get("amount") or package.get("quantity"), 1))
    item_weight = max(1, int(weight_grams / amount))
    item_cost = max(0.0, _to_float(package.get("cost_rub") or package.get("price_rub"), 0.0))
    # По умолчанию не используем наложенный платеж.
    payment_value = max(0.0, _to_float(package.get("payment_rub"), 0.0))

    item = {
        "name": description[:255],
        "ware_key": f"ITEM-{abs(hash(description)) % 10_000_000}",
        "cost": round(item_cost, 2),
        "payment": {"value": round(payment_value, 2)},
        "weight": item_weight,
        "amount": amount,
    }
    return [item]


def _normalize_dimension_cm(value: Any) -> int:
    dim = _to_int(value, 0)
    if dim <= 0:
        return 1
    return dim


async def get_city_code(city_name: str) -> Optional[int]:
    """
    Получить код города по названию (с использованием глобального клиента и кэширования).
    
    Args:
        city_name: Название города
    
    Returns:
        Код города или None
    """
    config = _get_cached_config()
    client = get_cdek_client(config)
    
    try:
        cities = await client.get_cities(city=city_name, use_cache=True)
        if cities and len(cities) > 0:
            return cities[0].get("code")
    except Exception as e:
        logger.error("CDEK: Error getting city code for %s: %s", city_name, e)
    return None


async def find_pvz_by_code(pvz_code: str, city_code: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    Найти ПВЗ по коду (с использованием глобального клиента и кэширования).
    
    Args:
        pvz_code: Код ПВЗ
        city_code: Код города (опционально, для ускорения поиска)
    
    Returns:
        Данные ПВЗ или None
    """
    config = _get_cached_config()
    client = get_cdek_client(config)
    
    try:
        pvz_list = await client.get_delivery_points(city_code=city_code, use_cache=True)
        for pvz in pvz_list:
            if pvz.get("code") == pvz_code:
                return pvz
    except Exception as e:
        logger.error("CDEK: Error finding PVZ %s: %s", pvz_code, e)
    return None


def _norm_text(value: str) -> str:
    return re.sub(r"[^a-zA-Zа-яА-Я0-9]+", " ", (value or "").lower()).strip()


def _pvz_text_blob(pvz: Dict[str, Any]) -> str:
    location = pvz.get("location") if isinstance(pvz.get("location"), dict) else {}
    parts = [
        pvz.get("name"),
        pvz.get("code"),
        pvz.get("address"),
        pvz.get("full_address"),
        location.get("address"),
        location.get("address_full"),
        location.get("address_comment"),
        location.get("city"),
    ]
    return " ".join(str(x).strip() for x in parts if x).strip()


def _pvz_match_score(address_hint: str, pvz_blob: str) -> float:
    query = _norm_text(address_hint)
    candidate = _norm_text(pvz_blob)
    if not query or not candidate:
        return 0.0

    ratio = SequenceMatcher(None, query, candidate).ratio()
    query_tokens = {t for t in query.split() if len(t) >= 2}
    cand_tokens = {t for t in candidate.split() if len(t) >= 2}
    overlap = (len(query_tokens & cand_tokens) / len(query_tokens)) if query_tokens else 0.0

    # Если совпали номера дома/корпуса, это сильный сигнал.
    query_numbers = set(re.findall(r"\d+[a-zа-я]?", query))
    cand_numbers = set(re.findall(r"\d+[a-zа-я]?", candidate))
    number_bonus = 0.15 if query_numbers and (query_numbers & cand_numbers) else 0.0

    return min(1.0, ratio * 0.65 + overlap * 0.35 + number_bonus)


async def find_pvz_by_address_hint(
    address_hint: str,
    *,
    city_code: Optional[int] = None,
    city_name: Optional[str] = None,
    min_score: float = 0.48,
) -> Optional[Dict[str, Any]]:
    """
    Подобрать ПВЗ по текстовому адресу/ориентиру (без кода ПВЗ).

    Returns:
        {"pvz": {...}, "score": float, "blob": str} или None
    """
    hint = (address_hint or "").strip()
    if not hint:
        return None

    resolved_city_code = city_code
    if not resolved_city_code and city_name:
        resolved_city_code = await get_city_code(city_name)
    if not resolved_city_code:
        return None

    config = _get_cached_config()
    client = get_cdek_client(config)
    try:
        pvz_list = await client.get_delivery_points(city_code=resolved_city_code, use_cache=True)
    except Exception as exc:
        logger.error("CDEK: Error loading PVZ list for city %s: %s", resolved_city_code, exc)
        return None

    best: Optional[Dict[str, Any]] = None
    for pvz in pvz_list or []:
        code = str(pvz.get("code") or "").strip()
        if not code:
            continue
        blob = _pvz_text_blob(pvz)
        score = _pvz_match_score(hint, blob)
        if best is None or score > best["score"]:
            best = {"pvz": pvz, "score": score, "blob": blob}

    if not best:
        return None
    if best["score"] < min_score:
        logger.info(
            "CDEK: PVZ by address low score=%.2f for hint=%r city_code=%s best=%r",
            best["score"],
            hint,
            resolved_city_code,
            best["blob"][:120],
        )
        return None

    logger.info(
        "CDEK: PVZ by address matched score=%.2f code=%s hint=%r",
        best["score"],
        best["pvz"].get("code"),
        hint,
    )
    return best


async def get_tariff_by_name(
    tariff_name: str,
    from_city_code: int,
    to_city_code: int,
    packages: List[Dict[str, Any]],
    order_type: int = 1,
) -> Optional[Dict[str, Any]]:
    """
    Найти тариф по названию (с использованием глобального клиента).
    
    Args:
        tariff_name: Название тарифа
        from_city_code: Код города отправителя
        to_city_code: Код города получателя
        packages: Список посылок
    
    Returns:
        Данные тарифа или None
    """
    config = _get_cached_config()
    client = get_cdek_client(config)
    
    try:
        normalized_order_type = _normalize_order_type(order_type)
        tariffs = await client.get_available_tariffs(
            from_location={"code": from_city_code},
            to_location={"code": to_city_code},
            packages=packages,
            order_type=normalized_order_type,
        )
        
        if not tariffs:
            logger.warning(
                "CDEK: No tariffs available for route %d -> %d (order_type=%d)",
                from_city_code,
                to_city_code,
                normalized_order_type,
            )
            return None
        
        # Ищем тариф по названию (нечеткое совпадение)
        tariff_name_lower = tariff_name.lower()
        for tariff in tariffs:
            tariff_title = (tariff.get("tariff_name") or tariff.get("name") or "").lower()
            if tariff_name_lower in tariff_title or tariff_title in tariff_name_lower:
                return tariff
        
        # Если не нашли, возвращаем первый доступный
        logger.info(
            "CDEK: Tariff '%s' not found, using first available: %s (order_type=%d)",
            tariff_name,
            tariffs[0].get("tariff_name"),
            normalized_order_type,
        )
        return tariffs[0]
    except Exception as e:
        logger.error("CDEK: Error getting tariff: %s", e)
    return None


async def get_all_available_tariffs(
    from_city_code: int,
    to_city_code: int,
    packages: List[Dict[str, Any]],
    order_type: int = 1,
) -> List[Dict[str, Any]]:
    """
    Получить все доступные тарифы для маршрута (с использованием глобального клиента).
    
    Args:
        from_city_code: Код города отправителя
        to_city_code: Код города получателя
        packages: Список посылок
    
    Returns:
        Список доступных тарифов
    """
    config = _get_cached_config()
    client = get_cdek_client(config)
    
    try:
        normalized_order_type = _normalize_order_type(order_type)
        tariffs = await client.get_available_tariffs(
            from_location={"code": from_city_code},
            to_location={"code": to_city_code},
            packages=packages,
            order_type=normalized_order_type,
        )
        return tariffs or []
    except Exception as e:
        logger.error("CDEK: Error getting all tariffs: %s", e)
        return []


def format_confirmation_card(extracted_data: Dict[str, Any], tariff_info: Optional[Dict[str, Any]] = None) -> str:
    """
    Форматировать карточку подтверждения отправки.
    
    Args:
        extracted_data: Извлеченные данные
        tariff_info: Информация о тарифе (опционально)
    
    Returns:
        Отформатированный текст карточки
    """
    package = extracted_data.get("package", {})

    weight_kg = package.get("weight_kg")
    if weight_kg:
        weight_text = f"{weight_kg} кг"
    else:
        weight_text = "уточним при сдаче в ПВЗ (тех. 1.0 кг)"

    length_cm = package.get("length_cm")
    width_cm = package.get("width_cm")
    height_cm = package.get("height_cm")
    if length_cm and width_cm and height_cm:
        dims_text = f"{length_cm}×{width_cm}×{height_cm} см"
    else:
        dims_text = "уточним при сдаче в ПВЗ (тех. 1×1×1 см)"

    preferred_order_type = _normalize_order_type(getattr(_get_cached_config(), "order_type", 1))
    preferred_order_mode = "Интернет-магазин" if preferred_order_type == 1 else "Доставка"

    text = (
        "📋 <b>Проверьте данные отправки</b>\n\n"
        f"🧭 <b>Режим API (по умолчанию):</b> {preferred_order_mode} (type={preferred_order_type})\n\n"
        "👤 <b>Получатель</b>\n"
        f"• ФИО: {extracted_data.get('recipient_fio') or 'Не указан'}\n"
        f"• Телефон: {extracted_data.get('recipient_phone') or 'Не указан'}\n"
        f"• Город: {extracted_data.get('recipient_city') or 'Не указан'}\n"
        f"• Адрес: {extracted_data.get('recipient_address') or 'Не указан'}\n"
        f"• Адрес ПВЗ: {extracted_data.get('delivery_pvz_address') or 'Не указан'}\n"
        f"• Код ПВЗ: {extracted_data.get('delivery_pvz_code') or 'Не указан'}\n\n"
        "📦 <b>Посылка</b>\n"
        f"• Вес: {weight_text}\n"
        f"• Размеры: {dims_text}\n"
        f"• Описание: {package.get('description') or 'Не указано'}\n"
    )
    
    if tariff_info:
        tariff_name = tariff_info.get("tariff_name") or tariff_info.get("name") or "Не указан"
        tariff_code = tariff_info.get("tariff_code") or tariff_info.get("code")
        delivery_sum = tariff_info.get("delivery_sum")
        text += f"\n🚚 <b>Тариф:</b> {tariff_name}"
        if tariff_code:
            text += f" (код: {tariff_code})"
        if delivery_sum:
            text += f"\n💰 <b>Стоимость:</b> {delivery_sum} ₽"
    
    text += f"\n\n🎯 <b>Уверенность извлечения:</b> {extracted_data.get('confidence', 0)*100:.0f}%"
    
    missing_fields = extracted_data.get("missing_fields", [])
    if missing_fields:
        missing_labels = {
            "recipient_fio": "ФИО получателя",
            "recipient_phone": "Телефон получателя",
            "recipient_city": "Город получателя",
            "recipient_address": "Адрес получателя",
            "delivery_pvz_address": "Адрес ПВЗ",
            "delivery_pvz_code": "Код ПВЗ",
            "weight_kg": "Вес",
            "length_cm": "Длина",
            "width_cm": "Ширина",
            "height_cm": "Высота",
        }
        human_missing = [missing_labels.get(str(field), str(field)) for field in missing_fields]
        text += f"\n\n⚠️ <b>Требует уточнения:</b> {', '.join(human_missing)}"
    
    return text


async def create_cdek_order(extracted_data: Dict[str, Any], config: Optional[CdekConfig] = None) -> Dict[str, Any]:
    """
    Создать заказ в CDEK.
    
    Args:
        extracted_data: Извлеченные данные отправки
        config: Конфигурация CDEK (если не указана, используется кэшированная)
    
    Returns:
        Созданный заказ с UUID и номером
    
    Raises:
        CdekAPIError: При ошибке создания заказа
        ValueError: При недостающих данных
    """
    if config is None:
        config = _get_cached_config()
    
    # Валидация обязательных полей
    if not extracted_data.get("recipient_fio"):
        raise ValueError("Не указано ФИО получателя")
    if not extracted_data.get("recipient_phone"):
        raise ValueError("Не указан телефон получателя")
    if not extracted_data.get("recipient_city"):
        raise ValueError("Не указан город получателя")
    
    package = extracted_data.get("package")
    if not isinstance(package, dict):
        package = {}
    
    # Вес: при отсутствии — 1.0 кг (API CDEK требует вес)
    raw_weight = package.get("weight_kg")
    used_default_weight = False
    if raw_weight is None or raw_weight == "" or (isinstance(raw_weight, (int, float)) and float(raw_weight) <= 0):
        weight_kg = 1.0
        used_default_weight = True
        logger.info("CDEK: weight not provided, using default 1.0 kg")
    else:
        try:
            weight_kg = float(raw_weight)
            if weight_kg <= 0:
                weight_kg = 1.0
                used_default_weight = True
                logger.warning("CDEK: weight <= 0, using default 1.0 kg")
        except (TypeError, ValueError):
            weight_kg = 1.0
            used_default_weight = True
            logger.warning("CDEK: invalid weight %r, using default 1.0 kg", raw_weight)
    weight_grams = int(weight_kg * 1000)
    
    # Получаем коды городов
    sender_city_code = await get_city_code(config.sender_city)
    recipient_city_code = await get_city_code(extracted_data["recipient_city"])
    
    if not sender_city_code:
        raise ValueError(f"Город отправителя '{config.sender_city}' не найден")
    if not recipient_city_code:
        raise ValueError(f"Город получателя '{extracted_data['recipient_city']}' не найден")
    
    # Подготавливаем посылку
    package_data = {
        "number": "PKG-001",
        "weight": weight_grams,
        "comment": str(package.get("description") or "Товар").strip()[:255],
    }
    
    length_cm = package.get("length_cm")
    width_cm = package.get("width_cm")
    height_cm = package.get("height_cm")
    has_all_dims = bool(length_cm and width_cm and height_cm)
    used_placeholder_dims = not has_all_dims
    package_data["length"] = _normalize_dimension_cm(length_cm)
    package_data["width"] = _normalize_dimension_cm(width_cm)
    package_data["height"] = _normalize_dimension_cm(height_cm)
    if not has_all_dims:
        logger.info(
            "CDEK: dimensions not provided, using placeholder %sx%sx%s cm (to be уточнены при приемке)",
            package_data["length"],
            package_data["width"],
            package_data["height"],
        )
    
    preferred_order_type = _normalize_order_type(getattr(config, "order_type", 1))
    selected_order_type: Optional[int] = None
    tariff_info: Optional[Dict[str, Any]] = None

    for attempt_order_type in _order_type_candidates(preferred_order_type):
        tariff_info = await get_tariff_by_name(
            config.default_tariff_name,
            sender_city_code,
            recipient_city_code,
            [package_data],
            order_type=attempt_order_type,
        )

        if not tariff_info:
            # Если тариф по названию не найден, берём первый доступный.
            all_tariffs = await get_all_available_tariffs(
                sender_city_code,
                recipient_city_code,
                [package_data],
                order_type=attempt_order_type,
            )
            if all_tariffs:
                tariff_info = all_tariffs[0]
                logger.warning(
                    "CDEK: Default tariff '%s' not available, using first available '%s' (order_type=%d)",
                    config.default_tariff_name,
                    tariff_info.get("tariff_name") or tariff_info.get("name"),
                    attempt_order_type,
                )

        if tariff_info:
            selected_order_type = attempt_order_type
            if attempt_order_type != preferred_order_type:
                logger.warning(
                    "CDEK: Fallback order_type used: preferred=%d actual=%d",
                    preferred_order_type,
                    selected_order_type,
                )
            break

    if not tariff_info or selected_order_type is None:
        alt_type = 2 if preferred_order_type == 1 else 1
        raise ValueError(
            f"Нет доступных тарифов для маршрута {config.sender_city} -> {extracted_data['recipient_city']} "
            f"(type={preferred_order_type}, fallback={alt_type})"
        )
    
    tariff_code = tariff_info.get("tariff_code") or tariff_info.get("code")
    if not tariff_code:
        raise ValueError("Не удалось определить код тарифа")
    
    recipient_address = (
        str(
            extracted_data.get("recipient_address")
            or extracted_data.get("delivery_address")
            or ""
        ).strip()
        or None
    )
    delivery_pvz_address = (
        str(
            extracted_data.get("delivery_pvz_address")
            or extracted_data.get("pvz_address")
            or ""
        ).strip()
        or None
    )
    if not recipient_address and delivery_pvz_address:
        # Если адрес получателя не найден, используем адрес ПВЗ как адрес назначения.
        recipient_address = delivery_pvz_address

    # Подготавливаем локации.
    # Важно: код ПВЗ не подставляем в to_location.code (там ожидается код города).
    # Для ПВЗ используем отдельные поля shipment_point / delivery_point.
    from_location = {"code": sender_city_code}
    to_location = {"code": recipient_city_code}
    if recipient_address:
        to_location["address"] = recipient_address
    sender_pvz_code = (config.sender_pvz or "").strip() or None
    recipient_pvz_code = (str(extracted_data.get("delivery_pvz_code") or "").strip().upper() or None)
    if recipient_pvz_code:
        pvz_info = await find_pvz_by_code(recipient_pvz_code, recipient_city_code)
        if not pvz_info:
            logger.warning("CDEK: PVZ code %s not found, fallback to city delivery", recipient_pvz_code)
            recipient_pvz_code = None
    if not recipient_pvz_code and delivery_pvz_address:
        best = await find_pvz_by_address_hint(
            delivery_pvz_address,
            city_code=recipient_city_code,
            city_name=extracted_data["recipient_city"],
        )
        if best and best.get("pvz"):
            matched_code = str(best["pvz"].get("code") or "").strip().upper()
            if matched_code:
                recipient_pvz_code = matched_code
                extracted_data["delivery_pvz_code"] = matched_code
                extracted_data["pvz_match_score"] = round(float(best.get("score") or 0.0), 3)
                logger.info(
                    "CDEK: Auto-resolved PVZ code by address: code=%s city=%s score=%.2f",
                    matched_code,
                    extracted_data["recipient_city"],
                    float(best.get("score") or 0.0),
                )
    if not recipient_pvz_code and not recipient_address:
        raise ValueError(
            "Не указан адрес получателя или код ПВЗ. "
            "Укажите адрес доставки или адрес/код ПВЗ."
        )

    order_package_data = dict(package_data)
    if int(selected_order_type) == 1:
        # Для type=1 (интернет-магазин) CDEK требует непустой packages[].items.
        order_package_data["items"] = _build_cdek_items(package, weight_grams)
    
    # Формируем данные заказа
    order_data = {
        "type": int(selected_order_type),  # 1 = интернет-магазин, 2 = доставка
        "number": f"ORDER-{int(time.time())}",  # Уникальный номер
        "tariff_code": int(tariff_code),
        "from_location": from_location,
        "to_location": to_location,
        "packages": [order_package_data],
        "recipient": {
            "name": extracted_data["recipient_fio"],
            "phones": [{"number": extracted_data["recipient_phone"]}],
        },
        "sender": {
            "name": config.sender_name,
            "company": config.sender_name,
            "phones": [{"number": config.sender_phone}],
        },
    }
    # CDEK docs: shipment_point/delivery_point must not be sent together
    # with from_location/to_location respectively.
    if sender_pvz_code:
        order_data["shipment_point"] = sender_pvz_code
        order_data.pop("from_location", None)
    if recipient_pvz_code:
        order_data["delivery_point"] = recipient_pvz_code
        order_data.pop("to_location", None)
    
    # Создаем заказ (используем глобальный клиент)
    client = get_cdek_client(config)
    
    try:
        logger.info(
            "CDEK: creating order with type=%s (1=e-shop, 2=delivery), tariff_code=%s, items=%s",
            order_data["type"],
            order_data["tariff_code"],
            len(order_package_data.get("items") or []),
        )
        order = await client.create_order(order_data)
        if isinstance(order, dict):
            order["_bot_meta"] = {
                "order_type": int(selected_order_type),
                "used_default_weight": used_default_weight,
                "used_placeholder_dims": used_placeholder_dims,
                "tariff_code": int(tariff_code),
                "delivery_pvz_code": recipient_pvz_code,
            }
        logger.info(
            "CDEK: Order created successfully: uuid=%s cdek_number=%s",
            order.get("uuid"),
            order.get("cdek_number"),
        )
        return order
    except CdekAuthError as e:
        logger.error("CDEK: Auth error creating order: %s", e)
        raise
    except CdekAPIError as e:
        logger.error("CDEK: API error creating order: %s", e)
        msg = str(e).lower()
        if "items" in msg:
            raise CdekAPIError(
                f"{e}. Для режима type=1 (интернет-магазин) обязателен packages[].items"
            )
        if "shipment_point" in msg:
            raise CdekAPIError(
                f"{e}. Для выбранного тарифа нужен склад отправителя: задайте CDEK_SENDER_PVZ в .env"
            )
        if "delivery_point" in msg:
            raise CdekAPIError(
                f"{e}. Для выбранного тарифа нужен ПВЗ получателя (delivery_pvz_code)"
            )
        raise
    except Exception as e:
        logger.error("CDEK: Unexpected error creating order: %s", e, exc_info=True)
        raise CdekAPIError(f"Ошибка создания заказа: {e}")


__all__ = [
    "get_city_code",
    "find_pvz_by_code",
    "find_pvz_by_address_hint",
    "get_tariff_by_name",
    "get_all_available_tariffs",
    "format_confirmation_card",
    "create_cdek_order",
]

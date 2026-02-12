# botapp/notifications/checker.py
"""
Фоновая проверка новых данных для уведомлений.

Реализация на основе паттернов a-ulianov/OzonAPI:
- Асинхронный дизайн
- Автоповторы при сбоях
- Логирование операций
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from .config import (
    get_user_settings,
    get_user_state,
    update_user_settings,
    get_all_enabled_users,
    is_in_quiet_hours,
)
from .sender import (
    NotificationType,
    send_notification,
    send_batch_notification,
)

logger = logging.getLogger(__name__)

# Глобальные переменные для управления фоновой задачей
_checker_task: Optional[asyncio.Task] = None
_is_running: bool = False

# Интервал проверки по умолчанию (секунды)
DEFAULT_CHECK_INTERVAL = 300  # 5 минут

# Минимальный интервал между проверками одного типа
MIN_CHECK_INTERVAL = 60  # 1 минута


async def _check_new_reviews(user_id: int) -> list[dict]:
    """Проверяет новые отзывы для пользователя."""
    from botapp.ozon_client import get_client
    
    state = get_user_state(user_id)
    settings = get_user_settings(user_id)
    
    if not settings.reviews_enabled:
        return []
    
    # Проверяем интервал
    if state.last_reviews_check:
        elapsed = (datetime.now() - state.last_reviews_check).total_seconds()
        if elapsed < MIN_CHECK_INTERVAL:
            return []
    
    try:
        client = get_client()
        
        # Получаем отзывы за последние 24 часа
        date_from = datetime.now() - timedelta(days=1)
        date_to = datetime.now()
        
        reviews = await client.get_reviews(date_from, date_to, max_count=50)
        
        state.last_reviews_check = datetime.now()
        
        if not isinstance(reviews, list) or not reviews:
            return []
        
        # Фильтруем только новые (не виденные ранее)
        new_reviews = []
        for review in reviews:
            if not isinstance(review, dict):
                continue
            
            review_id = str(review.get("id") or review.get("review_id") or "")
            if not review_id or review_id in state.known_review_ids:
                continue
            
            state.known_review_ids.add(review_id)
            
            # Безопасное извлечение данных
            product_data = review.get("product") or {}
            if isinstance(product_data, dict):
                product_name = product_data.get("name") or review.get("product_name") or "Товар"
            else:
                product_name = review.get("product_name") or "Товар"
            
            new_reviews.append({
                "id": review_id,
                "product_name": str(product_name),
                "rating": review.get("rating") or review.get("score") or 0,
                "text": str(review.get("text") or review.get("content") or ""),
            })
        
        # Ограничиваем размер кеша
        if len(state.known_review_ids) > 1000:
            state.known_review_ids = set(list(state.known_review_ids)[-500:])
        
        return new_reviews
        
    except Exception as e:
        logger.warning("Failed to check reviews for user %d: %s", user_id, e, exc_info=True)
        return []


async def _check_new_questions(user_id: int) -> list[dict]:
    """Проверяет новые вопросы для пользователя."""
    from botapp.ozon_client import get_questions_list
    
    state = get_user_state(user_id)
    settings = get_user_settings(user_id)
    
    if not settings.questions_enabled:
        return []
    
    # Проверяем интервал
    if state.last_questions_check:
        elapsed = (datetime.now() - state.last_questions_check).total_seconds()
        if elapsed < MIN_CHECK_INTERVAL:
            return []
    
    try:
        # Получаем вопросы без ответа (status="unanswered" маппится внутри функции)
        questions = await get_questions_list(status="unanswered", limit=50)
        
        state.last_questions_check = datetime.now()
        
        if not isinstance(questions, list) or not questions:
            return []
        
        # Фильтруем только новые
        new_questions = []
        for question in questions:
            # question — это объект Question из models
            if not hasattr(question, 'id'):
                continue
            
            qid = str(question.id) if question.id else ""
            if not qid or qid in state.known_question_ids:
                continue
            
            state.known_question_ids.add(qid)
            
            # Безопасное извлечение данных
            product_name = getattr(question, 'product_name', None) or "Товар"
            text = getattr(question, 'text', None) or ""
            
            new_questions.append({
                "id": qid,
                "product_name": str(product_name),
                "text": str(text),
            })
        
        # Ограничиваем размер кеша
        if len(state.known_question_ids) > 1000:
            state.known_question_ids = set(list(state.known_question_ids)[-500:])
        
        return new_questions
        
    except Exception as e:
        logger.warning("Failed to check questions for user %d: %s", user_id, e, exc_info=True)
        return []


async def _check_new_chat_messages(user_id: int) -> list[dict]:
    """Проверяет новые сообщения в чатах."""
    from botapp.ozon_client import chat_list
    
    state = get_user_state(user_id)
    settings = get_user_settings(user_id)
    
    if not settings.chats_enabled:
        return []
    
    # Проверяем интервал
    if state.last_chats_check:
        elapsed = (datetime.now() - state.last_chats_check).total_seconds()
        if elapsed < MIN_CHECK_INTERVAL:
            return []
    
    try:
        # Получаем чаты с непрочитанными сообщениями
        chat_response = await chat_list(limit=50, unread_only=True)
        
        if not chat_response:
            return []
        
        chats = chat_response.chats or list(chat_response.iter_items())
        state.last_chats_check = datetime.now()
        
        if not isinstance(chats, list) or not chats:
            return []
        
        new_messages = []
        for chat in chats:
            chat_id = chat.safe_chat_id or str(getattr(chat, 'chat_id', '') or "")
            if not chat_id:
                continue
            
            unread = int(getattr(chat, 'unread_count', 0) or 0)
            
            if unread <= 0 or chat_id in state.known_chat_ids:
                continue
            
            state.known_chat_ids.add(chat_id)
            
            # Извлекаем текст последнего сообщения из model_extra если есть
            last_text = "Новое сообщение"
            try:
                extras = getattr(chat, "model_extra", {}) or {}
                last_msg = extras.get("last_message") or {}
                if isinstance(last_msg, dict):
                    last_text = last_msg.get("text") or last_msg.get("content") or "Новое сообщение"
            except Exception:
                pass
            
            new_messages.append({
                "chat_id": chat_id,
                "text": str(last_text),
                "unread_count": unread,
            })
        
        # Ограничиваем размер кеша
        if len(state.known_chat_ids) > 500:
            state.known_chat_ids = set(list(state.known_chat_ids)[-250:])
        
        return new_messages
        
    except Exception as e:
        logger.warning("Failed to check chats for user %d: %s", user_id, e, exc_info=True)
        return []


async def _check_new_orders(user_id: int) -> tuple[list[dict], list[dict]]:
    """Проверяет новые заказы FBO и FBS."""
    from botapp.ozon_client import get_client
    
    state = get_user_state(user_id)
    settings = get_user_settings(user_id)
    
    new_fbo = []
    new_fbs = []
    
    # Проверяем интервал
    if state.last_orders_check:
        elapsed = (datetime.now() - state.last_orders_check).total_seconds()
        if elapsed < MIN_CHECK_INTERVAL:
            return [], []
    
    try:
        client = get_client()
        
        # Период: последний час
        since = (datetime.now() - timedelta(hours=1)).isoformat()
        to = datetime.now().isoformat()
        
        # FBO заказы
        if settings.orders_fbo_enabled:
            try:
                fbo_orders = await client.get_fbo_postings(since, to)
                if not isinstance(fbo_orders, list):
                    fbo_orders = []
                for order in fbo_orders[:20]:  # Ограничиваем
                    if not isinstance(order, dict):
                        continue
                    posting_number = order.get("posting_number")
                    if not posting_number or posting_number in state.known_order_numbers:
                        continue
                    
                    state.known_order_numbers.add(str(posting_number))
                    
                    # Безопасное извлечение products
                    products = order.get("products")
                    if not isinstance(products, list):
                        products = []
                    
                    # Безопасное извлечение названия товара
                    product_name = "Заказ"
                    if products and len(products) > 0:
                        first_product = products[0]
                        if isinstance(first_product, dict):
                            product_name = first_product.get("name") or first_product.get("product_name") or "Заказ"
                    
                    # Безопасный расчёт суммы
                    amount = 0.0
                    try:
                        amount = sum(
                            float(p.get("price", 0) or 0) * int(p.get("quantity", 1) or 1)
                            for p in products if isinstance(p, dict)
                        )
                    except (ValueError, TypeError) as e:
                        logger.debug("Failed to calculate amount for order %s: %s", posting_number, e)
                    
                    new_fbo.append({
                        "posting_number": str(posting_number),
                        "product_name": str(product_name),
                        "amount": amount,
                    })
            except Exception as e:
                logger.warning("FBO check failed for user %d: %s", user_id, e, exc_info=True)
        
        # FBS заказы
        if settings.orders_fbs_enabled:
            try:
                fbs_orders = await client.get_fbs_postings(since, to)
                if not isinstance(fbs_orders, list):
                    fbs_orders = []
                for order in fbs_orders[:20]:
                    if not isinstance(order, dict):
                        continue
                    posting_number = order.get("posting_number")
                    if not posting_number or posting_number in state.known_order_numbers:
                        continue
                    
                    state.known_order_numbers.add(str(posting_number))
                    
                    # Безопасное извлечение products
                    products = order.get("products")
                    if not isinstance(products, list):
                        products = []
                    
                    # Безопасное извлечение названия товара
                    product_name = "Заказ"
                    if products and len(products) > 0:
                        first_product = products[0]
                        if isinstance(first_product, dict):
                            product_name = first_product.get("name") or first_product.get("product_name") or "Заказ"
                    
                    # Безопасный расчёт суммы
                    amount = 0.0
                    try:
                        amount = sum(
                            float(p.get("price", 0) or 0) * int(p.get("quantity", 1) or 1)
                            for p in products if isinstance(p, dict)
                        )
                    except (ValueError, TypeError) as e:
                        logger.debug("Failed to calculate amount for order %s: %s", posting_number, e)
                    
                    new_fbs.append({
                        "posting_number": str(posting_number),
                        "product_name": str(product_name),
                        "amount": amount,
                    })
            except Exception as e:
                logger.warning("FBS check failed for user %d: %s", user_id, e, exc_info=True)
        
        state.last_orders_check = datetime.now()
        
        # Ограничиваем размер кеша
        if len(state.known_order_numbers) > 2000:
            state.known_order_numbers = set(list(state.known_order_numbers)[-1000:])
        
        return new_fbo, new_fbs
        
    except Exception as e:
        logger.warning("Failed to check orders for user %d: %s", user_id, e)
        return [], []


async def _process_user_notifications(user_id: int) -> None:
    """Обрабатывает уведомления для одного пользователя."""
    try:
        settings = get_user_settings(user_id)
        
        if not settings.enabled:
            return
        
        if is_in_quiet_hours(user_id):
            return
        
        # Проверяем новые отзывы
        try:
            new_reviews = await _check_new_reviews(user_id)
            if new_reviews and isinstance(new_reviews, list):
                if len(new_reviews) == 1:
                    review = new_reviews[0]
                    if isinstance(review, dict):
                        await send_notification(
                            user_id,
                            NotificationType.NEW_REVIEW,
                            "Новый отзыв",
                            str(review.get("text") or ""),
                            data={"review_id": str(review.get("id") or "")},
                            product_name=str(review.get("product_name") or "Товар"),
                            rating=int(review.get("rating") or 0) if review.get("rating") else None,
                        )
                else:
                    await send_batch_notification(
                        user_id,
                        NotificationType.NEW_REVIEW,
                        new_reviews,
                    )
        except Exception as e:
            logger.warning("Failed to process reviews notifications for user %d: %s", user_id, e, exc_info=True)
        
        # Проверяем новые вопросы
        try:
            new_questions = await _check_new_questions(user_id)
            if new_questions and isinstance(new_questions, list):
                if len(new_questions) == 1:
                    question = new_questions[0]
                    if isinstance(question, dict):
                        await send_notification(
                            user_id,
                            NotificationType.NEW_QUESTION,
                            "Новый вопрос",
                            str(question.get("text") or ""),
                            data={"question_id": str(question.get("id") or "")},
                            product_name=str(question.get("product_name") or "Товар"),
                        )
                else:
                    await send_batch_notification(
                        user_id,
                        NotificationType.NEW_QUESTION,
                        new_questions,
                    )
        except Exception as e:
            logger.warning("Failed to process questions notifications for user %d: %s", user_id, e, exc_info=True)
        
        # Проверяем новые сообщения в чатах
        try:
            new_messages = await _check_new_chat_messages(user_id)
            if new_messages:
                if len(new_messages) == 1:
                    msg = new_messages[0]
                    await send_notification(
                        user_id,
                        NotificationType.NEW_CHAT_MESSAGE,
                        "Новое сообщение",
                        msg.get("text", ""),
                        data={"chat_id": msg.get("chat_id")},
                    )
                else:
                    await send_batch_notification(
                        user_id,
                        NotificationType.NEW_CHAT_MESSAGE,
                        new_messages,
                    )
        except Exception as e:
            logger.warning("Failed to process chat notifications for user %d: %s", user_id, e, exc_info=True)
        
        # Проверяем новые заказы
        try:
            new_fbo, new_fbs = await _check_new_orders(user_id)
            
            if new_fbo:
                await send_batch_notification(
                    user_id,
                    NotificationType.NEW_ORDER_FBO,
                    new_fbo,
                )
            
            if new_fbs:
                await send_batch_notification(
                    user_id,
                    NotificationType.NEW_ORDER_FBS,
                    new_fbs,
                )
        except Exception as e:
            logger.warning("Failed to process orders notifications for user %d: %s", user_id, e, exc_info=True)
            
    except Exception as e:
        logger.error("Error processing notifications for user %d: %s", user_id, e, exc_info=True)


async def _notification_loop() -> None:
    """Основной цикл проверки уведомлений."""
    global _is_running
    
    logger.info("Notification checker started")
    
    consecutive_errors = 0
    max_consecutive_errors = 5
    
    while _is_running:
        try:
            # Получаем всех пользователей с включёнными уведомлениями
            enabled_users = get_all_enabled_users()
            
            if not enabled_users:
                logger.debug("No users with enabled notifications")
                await asyncio.sleep(DEFAULT_CHECK_INTERVAL)
                continue
            
            logger.debug("Processing notifications for %d users", len(enabled_users))
            
            processed_count = 0
            error_count = 0
            
            for user_id in enabled_users:
                if not _is_running:
                    break
                
                try:
                    await _process_user_notifications(user_id)
                    processed_count += 1
                    consecutive_errors = 0  # Сбрасываем счётчик при успехе
                except Exception as e:
                    error_count += 1
                    consecutive_errors += 1
                    logger.error("Error processing notifications for user %d: %s", user_id, e, exc_info=True)
                    
                    # Если слишком много ошибок подряд, делаем паузу
                    if consecutive_errors >= max_consecutive_errors:
                        logger.warning("Too many consecutive errors (%d), pausing for 60 seconds", consecutive_errors)
                        await asyncio.sleep(60)
                        consecutive_errors = 0
                
                # Небольшая задержка между пользователями
                await asyncio.sleep(1)
            
            if processed_count > 0:
                logger.debug("Processed notifications for %d users (errors: %d)", processed_count, error_count)
            
            # Ждём до следующей проверки
            await asyncio.sleep(DEFAULT_CHECK_INTERVAL)
            
        except asyncio.CancelledError:
            logger.info("Notification checker cancelled")
            break
        except Exception as e:
            consecutive_errors += 1
            logger.error("Error in notification loop: %s", e, exc_info=True)
            
            # При критической ошибке ждём дольше
            wait_time = min(60 * consecutive_errors, 300)  # Максимум 5 минут
            await asyncio.sleep(wait_time)
    
    logger.info("Notification checker stopped")


def start_notification_checker() -> None:
    """Запускает фоновую проверку уведомлений."""
    global _checker_task, _is_running
    
    if _is_running:
        logger.warning("Notification checker already running")
        return
    
    _is_running = True
    _checker_task = asyncio.create_task(_notification_loop())
    logger.info("Notification checker task created")


def stop_notification_checker() -> None:
    """Останавливает фоновую проверку уведомлений."""
    global _checker_task, _is_running
    
    _is_running = False
    
    if _checker_task:
        _checker_task.cancel()
        _checker_task = None
    
    logger.info("Notification checker stop requested")


def is_checker_running() -> bool:
    """Проверяет, запущен ли checker."""
    return _is_running


__all__ = [
    "start_notification_checker",
    "stop_notification_checker",
    "is_checker_running",
]

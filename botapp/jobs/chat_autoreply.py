"""Автоответ на входящие сообщения от покупателей в чатах Ozon."""
from __future__ import annotations

import asyncio
import logging
import os

from botapp.ozon_client import chat_history, chat_list, chat_send_message, OzonAPIError
from botapp.utils.storage import get_chat_autoreply_state, save_chat_autoreply_state

logger = logging.getLogger(__name__)

# Интервал проверки чатов (секунды) - увеличено для снижения нагрузки на API
POLL_INTERVAL_SECONDS = max(30, int(os.getenv("CHAT_AUTOREPLY_POLL_SECONDS", "60")))

# Текст автоответа
AUTOREPLY_TEXT = os.getenv(
    "CHAT_AUTOREPLY_TEXT",
    "Спасибо за заказ! Сообщение получено, скоро ответим.",
).strip()

def _is_buyer_message(message: dict) -> bool:
    """Проверяет, является ли сообщение от покупателя."""
    author = str(message.get("author", "") or "").lower()
    # В Ozon API автор может быть "Customer", "customer", "Buyer" и т.д.
    return author in ("customer", "buyer", "покупатель")


def _normalize_message_id(msg_id: int | str | None) -> int | None:
    """Нормализует message_id в int или возвращает None."""
    if msg_id is None:
        return None
    if isinstance(msg_id, int):
        return msg_id if msg_id > 0 else None
    if isinstance(msg_id, str):
        try:
            return int(msg_id) if int(msg_id) > 0 else None
        except (ValueError, TypeError):
            return None
    return None


async def chat_autoreply_loop(stop_event: asyncio.Event) -> None:
    """Основной цикл автоответов на входящие сообщения.
    
    Периодически проверяет чаты и отправляет автоответы на новые сообщения от покупателей.
    """
    logger.info(
        "Chat autoreply loop started: poll_interval=%ss, text=%r",
        POLL_INTERVAL_SECONDS,
        AUTOREPLY_TEXT,
    )
    
    # Загружаем начальное состояние
    state = get_chat_autoreply_state()
    last_processed = state.get("last_processed", {})
    if not isinstance(last_processed, dict):
        last_processed = {}
    
    while not stop_event.is_set():
        try:
            # Ждем интервал или сигнал остановки
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
                if stop_event.is_set():
                    break
            except asyncio.TimeoutError:
                pass
            
            # Получаем список чатов (оптимизация: сначала только с непрочитанными)
            try:
                chat_list_resp = await chat_list(
                    limit=200,
                    offset=0,
                    unread_only=True,  # Оптимизация: сначала проверяем только чаты с непрочитанными
                    chat_type_whitelist=("buyer_seller", "buyer-seller", "buyer_seller_chat"),
                )
            except OzonAPIError as e:
                logger.warning("Failed to fetch chat list: %s", e)
                continue
            except Exception as e:
                logger.exception("Unexpected error fetching chat list: %s", e)
                continue
            
            chats = chat_list_resp.chats or list(chat_list_resp.iter_items())
            if not chats:
                continue
            
            logger.debug("Checking %s chats for autoreply", len(chats))
            
            updated_state = False
            replied_count = 0
            
            for chat_item in chats:
                if stop_event.is_set():
                    break
                
                chat_id = chat_item.safe_chat_id
                if not chat_id:
                    continue
                
                # Получаем last_message_id из модели ChatListItem
                last_message_id = _normalize_message_id(chat_item.last_message_id)
                if not last_message_id:
                    continue
                
                # Проверяем, обрабатывали ли мы уже это сообщение
                last_processed_id = _normalize_message_id(last_processed.get(chat_id, 0)) or 0
                
                if last_message_id <= last_processed_id:
                    continue
                
                # Получаем историю чата (последнее сообщение)
                try:
                    history = await chat_history(chat_id, limit=1)
                except OzonAPIError as e:
                    error_msg = str(e).lower()
                    if "access period has expired" in error_msg or "expired" in error_msg:
                        continue
                    logger.debug("Failed to fetch history for chat %s: %s", chat_id, e)
                    continue
                except Exception as e:
                    logger.debug("Error fetching history for chat %s: %s", chat_id, e)
                    continue
                
                if not history or not isinstance(history[0], dict):
                    continue
                
                # Проверяем, что последнее сообщение от покупателя
                if not _is_buyer_message(history[0]):
                    # Не от покупателя - обновляем состояние, чтобы не проверять снова
                    last_processed[chat_id] = last_message_id
                    updated_state = True
                    continue
                
                # Отправляем автоответ
                try:
                    await chat_send_message(chat_id, AUTOREPLY_TEXT)
                    logger.info("Autoreply sent: chat_id=%s message_id=%s", chat_id, last_message_id)
                    last_processed[chat_id] = last_message_id
                    updated_state = True
                    replied_count += 1
                except OzonAPIError as e:
                    error_msg = str(e).lower()
                    if "access period has expired" in error_msg or "expired" in error_msg:
                        logger.debug("Chat %s expired", chat_id)
                    elif "chat not started" in error_msg:
                        logger.debug("Chat %s not started", chat_id)
                    else:
                        logger.warning("Failed to send autoreply to chat %s: %s", chat_id, e)
                except Exception as e:
                    logger.debug("Error sending autoreply to chat %s: %s", chat_id, e)
            
            # Сохраняем состояние только при изменениях
            if updated_state:
                state["last_processed"] = last_processed
                save_chat_autoreply_state(state)
            
            if replied_count > 0:
                logger.info("Autoreply cycle: sent %s replies", replied_count)
        
        except asyncio.CancelledError:
            logger.info("Chat autoreply loop cancelled")
            break
        except Exception as e:
            logger.exception("Error in chat autoreply loop: %s", e)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
    
    logger.info("Chat autoreply loop stopped")

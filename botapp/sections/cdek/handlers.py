# botapp/sections/cdek/handlers.py
"""Обработчики для раздела CDEK."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from botapp.ai_client import extract_cdek_shipment_data
from botapp.api.cdek_client import CdekAPIError, CdekAuthError
from botapp.keyboards import MenuCallbackData
from botapp.menu_handlers import _close_all_sections
from botapp.sections.cdek.keyboards import (
    CdekCallbackData,
    cdek_confirmation_keyboard,
    cdek_main_keyboard,
)
from botapp.sections.cdek.logic import (
    create_cdek_order,
    format_confirmation_card,
    get_tariff_by_name,
)
from botapp.states import CdekStates
from botapp.utils.message_gc import (
    SECTION_CDEK,
    SECTION_MENU,
    SECTION_CHAT_PROMPT,
    delete_section_message,
    send_section_message,
)

logger = logging.getLogger(__name__)
router = Router()


def _human_cdek_field_name(field: str) -> str:
    mapping = {
        "recipient_fio": "ФИО получателя",
        "recipient_phone": "Телефон получателя",
        "recipient_city": "Город получателя",
    }
    return mapping.get(field, field)


@router.callback_query(CdekCallbackData.filter(F.action == "create"))
async def cdek_create_handler(callback: CallbackQuery, state: FSMContext) -> None:
    """Обработчик кнопки 'Создать отправку'."""
    user_id = callback.from_user.id
    logger.info("CDEK: User %s started creating shipment", user_id)
    
    await state.set_state(CdekStates.waiting_conversation)
    
    text = (
        "📦 <b>Создание отправки СДЭК</b>\n\n"
        "1. Отправьте переписку с клиентом.\n"
        "2. Бот извлечет данные и покажет карточку.\n"
        "3. Подтвердите создание заказа.\n\n"
        "🧭 Режим по умолчанию: <b>Интернет-магазин (type=1)</b>.\n"
        "ℹ️ Если вес/габариты неизвестны, заказ все равно создается "
        "с техническими значениями, фактические параметры можно уточнить в ПВЗ."
    )
    
    await send_section_message(
        SECTION_CDEK,
        user_id=user_id,
        text=text,
        reply_markup=cdek_main_keyboard(),
        callback=callback,
        edit_current_message=True,
    )
    
    await callback.answer()


@router.message(CdekStates.waiting_conversation)
async def cdek_conversation_handler(message: Message, state: FSMContext) -> None:
    """Обработчик переписки с клиентом."""
    user_id = message.from_user.id
    logger.info("CDEK: User %s sent conversation for extraction", user_id)
    
    # Получаем текст переписки
    conversation_text = message.text or ""
    if message.forward_from_chat or message.forward_from:
        # Если переслано, можно добавить логику получения истории
        conversation_text = message.text or ""
    
    if not conversation_text.strip():
        await message.answer("Пожалуйста, отправьте текст переписки.")
        return
    
    # Сохраняем переписку в состояние
    await state.update_data(conversation_text=conversation_text)
    
    # Показываем сообщение об обработке
    processing_msg = await message.answer("🤖 Обработка переписки...")
    
    try:
        # Извлечение данных через AI
        extracted_data = await extract_cdek_shipment_data(conversation_text)
        
        # Проверяем наличие ошибки
        if extracted_data.get("error"):
            await processing_msg.delete()
            await message.answer(
                f"❌ Ошибка при извлечении данных: {extracted_data['error']}\n\n"
                "Попробуйте отправить переписку еще раз или укажите данные вручную."
            )
            return
        
        # Проверяем обязательные поля по фактическим значениям.
        critical_missing: list[str] = []
        if not extracted_data.get("recipient_fio"):
            critical_missing.append("recipient_fio")
        if not extracted_data.get("recipient_phone"):
            critical_missing.append("recipient_phone")
        if not extracted_data.get("recipient_city"):
            critical_missing.append("recipient_city")
        
        if critical_missing:
            await processing_msg.delete()
            missing_human = ", ".join(_human_cdek_field_name(field) for field in critical_missing)
            await message.answer(
                f"⚠️ <b>Не удалось извлечь обязательные данные:</b>\n"
                f"{missing_human}\n\n"
                "Пожалуйста, убедитесь, что в переписке указаны:\n"
                "• ФИО получателя\n"
                "• Телефон получателя\n"
                "• Город получателя\n\n"
                "Попробуйте отправить переписку еще раз."
            )
            return
        
        await state.update_data(extracted_data=extracted_data)
        await state.set_state(CdekStates.confirming_data)
        
        # Получаем информацию о тарифе для отображения
        tariff_info = None
        try:
            from botapp.sections.cdek.logic import get_city_code
            from botapp.config import load_cdek_config
            
            config = load_cdek_config()
            sender_city_code = await get_city_code(config.sender_city)
            recipient_city_code = await get_city_code(extracted_data.get("recipient_city", ""))
            
            if sender_city_code and recipient_city_code and extracted_data.get("package", {}).get("weight_kg"):
                package = extracted_data["package"]
                package_data = {
                    "weight": int(float(package["weight_kg"]) * 1000),
                }
                if package.get("length_cm"):
                    package_data["length"] = int(package["length_cm"])
                if package.get("width_cm"):
                    package_data["width"] = int(package["width_cm"])
                if package.get("height_cm"):
                    package_data["height"] = int(package["height_cm"])
                
                tariff_info = await get_tariff_by_name(
                    config.default_tariff_name,
                    sender_city_code,
                    recipient_city_code,
                    [package_data],
                    order_type=config.order_type,
                )
        except Exception as e:
            logger.debug("CDEK: Could not get tariff info: %s", e)
        
        # Форматируем карточку подтверждения
        confirmation_text = format_confirmation_card(extracted_data, tariff_info)
        
        await processing_msg.delete()
        await send_section_message(
            SECTION_CDEK,
            user_id=user_id,
            text=confirmation_text,
            reply_markup=cdek_confirmation_keyboard(),
            message=message,
        )
        
    except Exception as e:
        logger.error("CDEK: Error extracting data: %s", e, exc_info=True)
        await processing_msg.delete()
        await message.answer(f"❌ Ошибка при обработке переписки: {e}")


@router.callback_query(CdekCallbackData.filter(F.action == "confirm"))
async def cdek_confirm_handler(callback: CallbackQuery, state: FSMContext) -> None:
    """Обработчик подтверждения создания отправки."""
    user_id = callback.from_user.id
    logger.info("CDEK: User %s confirmed shipment creation", user_id)
    if callback.message is None:
        await callback.answer("❌ Сообщение недоступно, откройте карточку заново.", show_alert=True)
        return
    
    data = await state.get_data()
    extracted_data = data.get("extracted_data")
    
    if not extracted_data:
        await callback.answer("Данные не найдены. Начните заново.", show_alert=True)
        return
    
    # Проверяем, вызвано ли из чата
    is_from_chat = False
    chat_token = None
    callback_data = CdekCallbackData.unpack(callback.data)
    if callback_data.extra and callback_data.extra.startswith("chat:"):
        is_from_chat = True
        chat_token = callback_data.extra.replace("chat:", "")
    
    # Показываем сообщение о создании
    creating_msg = await callback.message.answer("⏳ Создание отправки...")
    
    try:
        # Создание отправки через CDEK API
        order = await create_cdek_order(extracted_data)

        # Нормализуем структуру ответа (часто CDEK возвращает данные в entity)
        entity = order.get("entity") if isinstance(order.get("entity"), dict) else None
        source = entity if entity else order
        
        # Форматируем результат
        order_uuid = source.get("uuid") or order.get("uuid") or "N/A"
        cdek_number = source.get("cdek_number") or source.get("number") or order.get("cdek_number") or order.get("number") or "N/A"
        
        # Статус может быть в разных местах
        status = "N/A"
        if isinstance(source.get("status"), dict):
            status = source["status"].get("code") or source["status"].get("name", "N/A")
        elif isinstance(source.get("status"), str):
            status = source["status"]
        elif isinstance(order.get("status"), dict):
            status = order["status"].get("code") or order["status"].get("name", "N/A")
        elif isinstance(order.get("status"), str):
            status = order["status"]
        elif isinstance(source.get("statuses"), list) and source.get("statuses"):
            first_status = source["statuses"][0]
            if isinstance(first_status, dict):
                status = first_status.get("name") or first_status.get("code") or "N/A"
        
        order_type = None
        bot_meta = None
        if isinstance(order, dict):
            bot_meta = order.get("_bot_meta")
            if isinstance(bot_meta, dict):
                order_type = bot_meta.get("order_type")

        order_type_label = None
        if order_type == 1:
            order_type_label = "Интернет-магазин"
        elif order_type == 2:
            order_type_label = "Доставка"

        result_text = (
            "✅ <b>Отправка создана!</b>\n\n"
            f"📦 <b>UUID:</b> {order_uuid}\n"
            f"🔢 <b>Номер CDEK:</b> {cdek_number}\n"
            f"📊 <b>Статус:</b> {status}\n\n"
        )
        if order_type_label:
            result_text += f"🧭 <b>Режим:</b> {order_type_label} (type={order_type})\n\n"

        if isinstance(bot_meta, dict):
            tech_notes = []
            if bot_meta.get("used_default_weight"):
                tech_notes.append("вес 1.0 кг (технический)")
            if bot_meta.get("used_placeholder_dims"):
                tech_notes.append("габариты 1×1×1 см (технические)")
            if tech_notes:
                result_text += (
                    "ℹ️ <b>На создание заказа использованы тех. параметры:</b>\n"
                    f"• {', '.join(tech_notes)}\n"
                    "Фактические параметры можно уточнить при приемке в ПВЗ.\n\n"
                )
        
        # Добавляем информацию о трек-номере, если есть
        tracking_number = source.get("tracking_number") or order.get("tracking_number")
        if tracking_number:
            result_text += f"📮 <b>Трек-номер:</b> {tracking_number}\n\n"
        
        result_text += (
            "Документы будут доступны после обработки.\n\n"
            "Что дальше:\n"
            "• Передайте посылку в ПВЗ СДЭК\n"
            "• При необходимости уточните фактический вес/габариты на приемке"
        )
        
        await creating_msg.delete()
        
        if is_from_chat and chat_token:
            # Возвращаемся к чату - используем секцию чата
            from botapp.sections.chats.keyboards import ChatCallbackData
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            
            back_keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="↩️ Вернуться к чату",
                            callback_data=ChatCallbackData(action="open", chat_id=chat_token).pack(),
                        )
                    ]
                ]
            )
            
            await send_section_message(
                SECTION_CHAT_PROMPT,
                user_id=user_id,
                text=result_text,
                reply_markup=back_keyboard,
                callback=callback,
                edit_current_message=True,
            )
        else:
            await send_section_message(
                SECTION_CDEK,
                user_id=user_id,
                text=result_text,
                reply_markup=cdek_main_keyboard(),
                callback=callback,
                edit_current_message=True,
            )
        
        await state.clear()
        await callback.answer("Отправка успешно создана!")
        
    except ValueError as e:
        logger.error("CDEK: Validation error creating order: %s", e)
        await creating_msg.delete()
        await callback.answer(f"❌ {str(e)}", show_alert=True)
    except CdekAuthError as e:
        logger.error("CDEK: Auth error creating order: %s", e)
        await creating_msg.delete()
        await callback.answer("❌ Ошибка авторизации CDEK API. Проверьте настройки.", show_alert=True)
    except CdekAPIError as e:
        logger.error("CDEK: API error creating order: %s", e)
        await creating_msg.delete()
        await callback.answer(f"❌ Ошибка CDEK API: {str(e)}", show_alert=True)
    except Exception as e:
        logger.error("CDEK: Unexpected error creating order: %s", e, exc_info=True)
        await creating_msg.delete()
        await callback.answer(f"❌ Неожиданная ошибка: {str(e)}", show_alert=True)


@router.callback_query(CdekCallbackData.filter(F.action == "edit"))
async def cdek_edit_handler(callback: CallbackQuery, state: FSMContext) -> None:
    """Обработчик редактирования данных отправки (ручной ввод)."""
    user_id = callback.from_user.id
    callback_data = CdekCallbackData.unpack(callback.data)
    
    # Проверяем, вызвано ли из чата
    is_from_chat = False
    chat_token = None
    if callback_data.extra and callback_data.extra.startswith("chat:"):
        is_from_chat = True
        chat_token = callback_data.extra.replace("chat:", "")
        # Сохраняем информацию о чате в состояние
        await state.update_data(chat_token=chat_token, is_from_chat=True)
    
    # Переходим в режим ручного редактирования
    await state.set_state(CdekStates.editing_data)
    
    # Получаем текущие данные из состояния (если есть)
    state_data = await state.get_data()
    extracted_data = state_data.get("extracted_data", {})
    
    text = (
        "✏️ <b>Ввод данных отправки вручную</b>\n\n"
        "Введите данные в следующем формате (каждое поле с новой строки):\n\n"
        "<b>ФИО:</b> [Фамилия Имя Отчество]\n"
        "<b>Телефон:</b> [+79991234567 или 89991234567]\n"
        "<b>Город:</b> [Название города]\n"
        "<b>Адрес:</b> [Улица, дом, квартира - для доставки до двери]\n"
        "<b>Адрес ПВЗ:</b> [Адрес/ориентир ПВЗ, если кода нет]\n"
        "<b>ПВЗ:</b> [Код ПВЗ, опционально]\n"
        "<b>Вес:</b> [Вес в кг, опционально]\n"
        "<b>Размеры:</b> [Длина×Ширина×Высота в см, опционально]\n"
        "<b>Описание:</b> [Описание товара, опционально]\n\n"
        "Пример:\n"
        "ФИО: Иванов Иван Иванович\n"
        "Телефон: +79991234567\n"
        "Город: Москва\n"
        "Адрес: ул. Тверская, д. 7, кв. 12\n"
        "Адрес ПВЗ: г. Москва, ул. Пятницкая, 1\n"
        "ПВЗ: MSK2279\n"
        "Вес: 2.5\n"
        "Размеры: 30×20×15\n"
        "Описание: Товар для отправки\n\n"
        "ℹ️ Если вес/размеры не указать, бот подставит тех. значения "
        "(1.0 кг и 1×1×1 см), а фактические параметры можно уточнить при приемке."
    )
    
    # Если есть частично извлеченные данные, показываем их
    if extracted_data:
        preview = []
        if extracted_data.get("recipient_fio"):
            preview.append(f"ФИО: {extracted_data['recipient_fio']}")
        if extracted_data.get("recipient_phone"):
            preview.append(f"Телефон: {extracted_data['recipient_phone']}")
        if extracted_data.get("recipient_city"):
            preview.append(f"Город: {extracted_data['recipient_city']}")
        if extracted_data.get("recipient_address"):
            preview.append(f"Адрес: {extracted_data['recipient_address']}")
        if extracted_data.get("delivery_pvz_address"):
            preview.append(f"Адрес ПВЗ: {extracted_data['delivery_pvz_address']}")
        if extracted_data.get("delivery_pvz_code"):
            preview.append(f"ПВЗ: {extracted_data['delivery_pvz_code']}")
        if preview:
            text += f"\n\n<b>Текущие данные (можно исправить):</b>\n" + "\n".join(preview)
    
    await send_section_message(
        SECTION_CDEK if not is_from_chat else SECTION_CHAT_PROMPT,
        user_id=user_id,
        text=text,
        reply_markup=None,
        callback=callback,
        edit_current_message=True,
    )
    
    await callback.answer("Введите данные вручную")


@router.message(CdekStates.editing_data)
async def cdek_manual_input_handler(message: Message, state: FSMContext) -> None:
    """Обработчик ручного ввода данных отправки."""
    user_id = message.from_user.id
    text = message.text or ""
    
    logger.info("CDEK: User %s entered manual data, length=%d", user_id, len(text))
    
    # Парсим данные из текста
    extracted_data = {
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
        },
        "confidence": 1.0,
        "missing_fields": [],
    }
    
    # Парсим строки формата "Поле: значение"
    lines = text.split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Парсим "Поле: значение"
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip().lower()
            value = value.strip()
            
            if "фио" in key:
                extracted_data["recipient_fio"] = value
            elif "телефон" in key:
                # Нормализуем телефон
                phone = value.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
                if phone.startswith("8"):
                    phone = "+7" + phone[1:]
                elif phone.startswith("7"):
                    phone = "+" + phone
                elif not phone.startswith("+"):
                    phone = "+7" + phone
                extracted_data["recipient_phone"] = phone
            elif "город" in key:
                extracted_data["recipient_city"] = value
            elif "код пвз" in key or "пвз код" in key:
                extracted_data["delivery_pvz_code"] = value.upper() if value else None
            elif "адрес пвз" in key or ("пвз" in key and "адрес" in key):
                extracted_data["delivery_pvz_address"] = value
            elif "адрес" in key:
                extracted_data["recipient_address"] = value
            elif "пвз" in key:
                extracted_data["delivery_pvz_code"] = value.upper() if value else None
            elif "вес" in key:
                try:
                    extracted_data["package"]["weight_kg"] = float(value.replace(",", "."))
                except ValueError:
                    pass
            elif "размеры" in key or "размер" in key:
                # Парсим "Длина×Ширина×Высота" или "Длина x Ширина x Высота"
                import re
                dims = re.findall(r'\d+', value.replace("×", "x").replace("X", "x"))
                if len(dims) >= 1:
                    extracted_data["package"]["length_cm"] = int(dims[0])
                if len(dims) >= 2:
                    extracted_data["package"]["width_cm"] = int(dims[1])
                if len(dims) >= 3:
                    extracted_data["package"]["height_cm"] = int(dims[2])
            elif "описание" in key:
                extracted_data["package"]["description"] = value
    
    # Проверяем обязательные поля
    missing = []
    if not extracted_data["recipient_fio"]:
        missing.append("recipient_fio")
    if not extracted_data["recipient_phone"]:
        missing.append("recipient_phone")
    if not extracted_data["recipient_city"]:
        missing.append("recipient_city")
    
    extracted_data["missing_fields"] = missing

    if not extracted_data["recipient_address"] and not extracted_data["delivery_pvz_code"] and not extracted_data["delivery_pvz_address"]:
        await message.answer(
            "⚠️ Нужен адрес получателя или данные ПВЗ (код/адрес ПВЗ). "
            "Добавьте одно из этих полей и отправьте ещё раз."
        )
        return
    
    if missing:
        await message.answer(
            f"⚠️ Не заполнены обязательные поля: {', '.join(missing)}\n\n"
            "Пожалуйста, введите данные еще раз с обязательными полями."
        )
        return
    
    # Сохраняем данные в состояние
    state_data = await state.get_data()
    state_data["extracted_data"] = extracted_data
    await state.set_data(state_data)
    await state.set_state(CdekStates.confirming_data)
    
    # Получаем информацию о тарифе
    from botapp.config import load_cdek_config
    from botapp.sections.cdek.logic import get_tariff_by_name, get_city_code
    
    tariff_info = None
    try:
        config = load_cdek_config()
        sender_city_code = await get_city_code(config.sender_city)
        recipient_city_code = await get_city_code(extracted_data.get("recipient_city", ""))
        
        if sender_city_code and recipient_city_code and extracted_data.get("package", {}).get("weight_kg"):
            package = extracted_data["package"]
            package_data = {"weight": int(float(package["weight_kg"]) * 1000)}
            if package.get("length_cm"):
                package_data["length"] = int(package["length_cm"])
            if package.get("width_cm"):
                package_data["width"] = int(package["width_cm"])
            if package.get("height_cm"):
                package_data["height"] = int(package["height_cm"])
            
            tariff_info = await get_tariff_by_name(
                config.default_tariff_name,
                sender_city_code,
                recipient_city_code,
                [package_data],
                order_type=config.order_type,
            )
    except Exception as e:
        logger.debug("CDEK: Could not get tariff info: %s", e)
    
    # Форматируем карточку подтверждения
    confirmation_text = format_confirmation_card(extracted_data, tariff_info)
    
    # Определяем, откуда был вызов
    is_from_chat = state_data.get("is_from_chat", False)
    chat_token = state_data.get("chat_token")
    
    # Создаем клавиатуру
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    
    keyboard_buttons = [
        [
            InlineKeyboardButton(
                text="✅ Создать",
                callback_data=CdekCallbackData(action="confirm", extra=f"chat:{chat_token}" if chat_token else None).pack(),
            ),
            InlineKeyboardButton(
                text="✏️ Исправить",
                callback_data=CdekCallbackData(action="edit", extra=f"chat:{chat_token}" if chat_token else None).pack(),
            ),
        ],
    ]
    
    if is_from_chat and chat_token:
        from botapp.sections.chats.keyboards import ChatsCallbackData
        keyboard_buttons.append([
            InlineKeyboardButton(
                text="⬅️ Назад к чату",
                callback_data=ChatsCallbackData(action="open", chat_id=chat_token).pack(),
            )
        ])
    else:
        keyboard_buttons.append([
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=CdekCallbackData(action="back").pack(),
            )
        ])
    
    confirmation_keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    # Отправляем карточку подтверждения
    section = SECTION_CHAT_PROMPT if is_from_chat else SECTION_CDEK
    await send_section_message(
        section,
        user_id=user_id,
        text=confirmation_text,
        reply_markup=confirmation_keyboard,
        message=message,
        edit_current_message=True,
    )


@router.callback_query(CdekCallbackData.filter(F.action == "back"))
async def cdek_back_handler(callback: CallbackQuery, state: FSMContext) -> None:
    """Обработчик возврата назад."""
    user_id = callback.from_user.id
    await state.clear()
    
    text = (
        "🚚 <b>СДЭК</b>\n\n"
        "Выберите действие:\n"
        "• Создать отправку из переписки\n"
        "• Ввести данные вручную"
    )
    
    await send_section_message(
        SECTION_CDEK,
        user_id=user_id,
        text=text,
        reply_markup=cdek_main_keyboard(),
        callback=callback,
        edit_current_message=True,
    )
    
    await callback.answer()

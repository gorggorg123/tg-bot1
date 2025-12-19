"""Warehouse bot flows: receiving, picking, inventory."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from botapp.keyboards import (
    MenuCallbackData,
    WarehouseCallbackData,
    pick_plan_keyboard,
    warehouse_ai_confirmation_keyboard,
    warehouse_catalog_keyboard,
    warehouse_labels_keyboard,
    warehouse_menu_keyboard,
    warehouse_receive_keyboard,
    warehouse_results_keyboard,
)
from botapp.utils.message_gc import (
    SECTION_WAREHOUSE_MENU,
    SECTION_WAREHOUSE_PLAN,
    SECTION_WAREHOUSE_PROMPT,
    delete_section_message,
    send_section_message,
)
from botapp.ozon_client import OzonClient, get_client, get_posting_details
from botapp.products_service import (
    CatalogProduct,
    find_by_sku,
    get_catalog,
    refresh_catalog_from_ozon,
    search_by_name,
    update_barcode_in_cache,
)
from botapp.states import WarehouseStates
from botapp.utils import send_ephemeral_from_callback, send_ephemeral_message
from botapp.warehouse_models import (
    Box,
    Location,
    Movement,
    Product,
    default_shop_location,
    generate_box_id,
    generate_movement_id,
)
from botapp.warehouse_ai import parse_production_text_to_items
from botapp.warehouse_labels import build_labels_pdf

logger = logging.getLogger(__name__)

router = Router()


class InMemoryWarehouseStore:
    """Minimal in-memory storage to support bot flows until DB is wired."""

    def __init__(self) -> None:
        self.products: Dict[str, Product] = {}
        self.boxes: Dict[str, Box] = {}
        self.locations: Dict[str, Location] = {}
        self.movements: Dict[str, Movement] = {}

    def find_product(self, query: str) -> Product | None:
        text = (query or "").strip()
        if not text:
            return None

        if text in self.products:
            return self.products[text]

        for product in self.products.values():
            if product.barcode and product.barcode == text:
                return product

        lowered = text.lower()
        for product in self.products.values():
            if lowered in product.name.lower():
                return product

        return None

    def find_product_by_ozon(self, *, offer_id: str | None, sku: int | None) -> Product | None:
        for product in self.products.values():
            if offer_id and product.ozon_offer_id == offer_id:
                return product
            if sku and product.ozon_sku == sku:
                return product
        return None

    def save_product(self, product: Product) -> Product:
        self.products[product.sku] = product
        return product

    def upsert_product(self, product: Product) -> Product:
        existing = self.products.get(product.sku)
        if existing:
            merged = existing.model_copy(update=product.model_dump(exclude_none=True))
            self.products[product.sku] = merged
            return merged
        self.products[product.sku] = product
        return product

    def get_or_create_location(self, location_id: str, name: str | None = None) -> Location:
        if location_id in self.locations:
            return self.locations[location_id]
        location = Location(id=location_id, name=name)
        self.locations[location_id] = location
        return location

    def default_location(self) -> Location:
        return self.get_or_create_location(
            default_shop_location().id, name=default_shop_location().name
        )

    def list_boxes_for_product(self, sku: str) -> list[Box]:
        return [box for box in self.boxes.values() if box.product.sku == sku]

    def save_box(self, box: Box) -> Box:
        self.boxes[box.id] = box
        return box

    def get_box(self, box_id: str) -> Box | None:
        return self.boxes.get(box_id)

    def save_movement(self, movement: Movement) -> Movement:
        self.movements[movement.id] = movement
        return movement

    def delete_box(self, box_id: str) -> None:
        self.boxes.pop(box_id, None)

    def total_quantity(self, sku: str) -> int:
        return sum(box.quantity for box in self.list_boxes_for_product(sku))


STORE = InMemoryWarehouseStore()


def _deserialize_product(data: Dict[str, Any]) -> Product:
    return Product.model_validate(data)


@router.callback_query(MenuCallbackData.filter(F.section == "warehouse"))
async def open_warehouse(callback: CallbackQuery, callback_data: MenuCallbackData, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await send_section_message(
        SECTION_WAREHOUSE_MENU,
        text="🏬 Раздел склада",
        reply_markup=warehouse_menu_keyboard(),
        callback=callback,
        user_id=callback.from_user.id,
    )


@router.callback_query(WarehouseCallbackData.filter(F.action == "risk"))
async def warehouse_risk_stub(callback: CallbackQuery) -> None:
    await callback.answer()
    await send_ephemeral_from_callback(callback, "Скоро покажем риск остатков.")


@router.callback_query(WarehouseCallbackData.filter(F.action == "ask_ai"))
async def warehouse_ai_stub(callback: CallbackQuery) -> None:
    await callback.answer()
    await send_ephemeral_from_callback(callback, "ИИ-помощник для склада появится позже.")


@router.callback_query(WarehouseCallbackData.filter(F.action == "receive"))
async def start_receive(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await state.set_state(WarehouseStates.receive_product_manual)
    await send_section_message(
        SECTION_WAREHOUSE_PROMPT,
        text="📥 Приёмка: выберите режим.",
        callback=callback,
        reply_markup=warehouse_receive_keyboard(),
        user_id=callback.from_user.id,
    )


@router.callback_query(WarehouseCallbackData.filter(F.action == "receive_back"))
async def receive_back(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await send_section_message(
        SECTION_WAREHOUSE_MENU,
        text="🏬 Раздел склада",
        callback=callback,
        reply_markup=warehouse_menu_keyboard(),
        user_id=callback.from_user.id,
    )


async def _paginate_catalog(page: int, force: bool = False) -> tuple[list[tuple[str, str]], int]:
    catalog = await (refresh_catalog_from_ozon(force=force) if force else get_catalog())
    per_page = 8
    total_pages = max((len(catalog) + per_page - 1) // per_page, 1)
    safe_page = max(0, min(page, total_pages - 1))
    start = safe_page * per_page
    end = start + per_page
    options: list[tuple[str, str]] = []
    for item in catalog[start:end]:
        text = f"{item.name[:40]} (SKU: {item.sku})"
        if item.ozon_product_id is None:
            continue
        data = WarehouseCallbackData(
            action="receive_choose", product_id=item.ozon_product_id
        ).pack()
        options.append((text, data))
    return options, total_pages


@router.callback_query(WarehouseCallbackData.filter(F.action.in_(
    {"receive_list", "receive_list_refresh"}
)))
async def receive_list(callback: CallbackQuery, callback_data: WarehouseCallbackData, state: FSMContext) -> None:
    await callback.answer()
    page = callback_data.page or 0
    force = callback_data.action == "receive_list_refresh"
    options, total_pages = await _paginate_catalog(page, force=force)
    await state.set_state(WarehouseStates.receive_product_manual)
    await send_section_message(
        SECTION_WAREHOUSE_PROMPT,
        text="Выберите товар из каталога Ozon:",
        callback=callback,
        reply_markup=warehouse_catalog_keyboard(options, page, total_pages),
        user_id=callback.from_user.id,
    )


@router.callback_query(WarehouseCallbackData.filter(F.action == "receive_search_name"))
async def receive_search_name_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(WarehouseStates.receive_search_name)
    await send_section_message(
        SECTION_WAREHOUSE_PROMPT,
        text="Отправьте часть названия товара",
        callback=callback,
        user_id=callback.from_user.id,
    )


@router.message(WarehouseStates.receive_search_name, F.text)
async def receive_search_name(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()
    results = await search_by_name(query)
    if not results:
        await message.answer("Товар не найден. Попробуйте ещё раз или вернитесь назад.")
        return

    options = []
    for item in results[:10]:
        options.append(
            (
                f"{item.name[:40]} (SKU: {item.sku})",
                WarehouseCallbackData(
                    action="receive_choose", product_id=item.ozon_product_id
                ).pack(),
            )
        )
    await state.set_state(WarehouseStates.receive_product_manual)
    await send_section_message(
        SECTION_WAREHOUSE_PROMPT,
        text="Нашёл варианты:",
        message=message,
        reply_markup=warehouse_results_keyboard(options),
        user_id=message.from_user.id,
    )


@router.callback_query(WarehouseCallbackData.filter(F.action == "receive_search_sku"))
async def receive_search_sku_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(WarehouseStates.receive_search_sku)
    await send_section_message(
        SECTION_WAREHOUSE_PROMPT,
        text="Отправьте артикул товара (SKU / offer_id)",
        callback=callback,
        user_id=callback.from_user.id,
    )


@router.message(WarehouseStates.receive_search_sku, F.text)
async def receive_search_sku(message: Message, state: FSMContext) -> None:
    sku = (message.text or "").strip()
    item = await find_by_sku(sku)
    if not item:
        await message.answer("Товар не найден, попробуйте ещё раз.")
        return
    await state.update_data(selected_product=item.model_dump())
    await state.set_state(WarehouseStates.receive_quantity_manual)
    await send_section_message(
        SECTION_WAREHOUSE_PROMPT,
        text=f"Выбрали {item.name}. Сколько штук этого товара вы собрали?",
        message=message,
        user_id=message.from_user.id,
    )


@router.callback_query(WarehouseCallbackData.filter(F.action == "receive_choose"))
async def receive_choose(callback: CallbackQuery, callback_data: WarehouseCallbackData, state: FSMContext) -> None:
    await callback.answer()
    product_id = callback_data.product_id
    if product_id is None:
        await send_ephemeral_from_callback(callback, "Не удалось определить товар.")
        return
    catalog = await get_catalog()
    item = next((p for p in catalog if p.ozon_product_id == product_id), None)
    if item is None:
        await send_ephemeral_from_callback(callback, "Товар не найден в каталоге, обновите список.")
        return
    await state.update_data(selected_product=item.model_dump())
    await state.set_state(WarehouseStates.receive_quantity_manual)
    await send_section_message(
        SECTION_WAREHOUSE_PROMPT,
        text=f"Сколько штук товара {item.name} вы собрали?",
        callback=callback,
        user_id=callback.from_user.id,
    )


@router.message(WarehouseStates.receive_quantity_manual, F.text)
async def handle_receive_quantity(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    try:
        qty = int(text)
        if qty <= 0:
            raise ValueError
    except Exception:
        await message.answer("Введите целое положительное число.")
        return

    data = await state.get_data()
    product_raw = data.get("selected_product")
    if not product_raw:
        await message.answer("Не выбрали товар. Начните приёмку заново.")
        await state.clear()
        return

    await state.update_data(quantity=qty)
    await send_section_message(
        SECTION_WAREHOUSE_PROMPT,
        text=(
            f"Сделать файл с {qty} этикетками со штрихкодами для этого товара?"
        ),
        message=message,
        reply_markup=warehouse_labels_keyboard(),
        user_id=message.from_user.id,
    )


def _catalog_to_product(item: CatalogProduct) -> Product:
    return Product(
        sku=item.sku,
        name=item.name,
        ozon_offer_id=item.sku,
        ozon_sku=item.ozon_sku,
        ozon_product_id=item.ozon_product_id,
        barcode=item.barcode,
    )


async def _record_receipt(product: CatalogProduct, quantity: int) -> tuple[Product, Box]:
    prod_model = _catalog_to_product(product)
    stored = STORE.upsert_product(prod_model)
    location = STORE.default_location()
    box_id = generate_box_id(set(STORE.boxes.keys()))
    box = Box(
        id=box_id,
        product=stored,
        quantity=quantity,
        location=location,
        created_at=datetime.utcnow(),
    )
    STORE.save_box(box)
    movement = Movement(
        id=generate_movement_id(set(STORE.movements.keys())),
        type="RECEIPT",
        product=stored,
        quantity=quantity,
        to_box=box,
        timestamp=datetime.utcnow(),
    )
    STORE.save_movement(movement)
    return stored, box


async def ensure_ozon_barcode(client: OzonClient, product: CatalogProduct) -> str | None:
    """
    Попробовать получить штрихкод для товара ИСКЛЮЧИТЕЛЬНО из Ozon.

    1. Если в product.barcode уже есть значение — вернуть его.
    2. Если есть ozon_product_id:
       2.1) запросить /v3/product/info/list и попытаться взять barcode/barcodes;
       2.2) если пусто — вызвать /v1/barcode/generate;
       2.3) затем снова запросить /v3/product/info/list.
    3. Ошибки логируются, но не приводят к синтетическим штрихкодам.
    4. При успешном получении обновляем product.barcode и локальный кэш.
    """

    if product.barcode:
        return product.barcode

    if product.ozon_product_id is None:
        logger.info(
            "ensure_ozon_barcode: product %s has no ozon_product_id, skipping fetch",
            product.sku,
        )
        return None

    def _pick_barcode(items: list[Any]) -> str | None:
        for info in items:
            candidate = getattr(info, "barcode", None) or next(
                (b for b in getattr(info, "barcodes", []) if b), None
            )
            if candidate:
                return str(candidate)
        return None

    barcode_value: str | None = None

    try:
        try:
            info_list = await client.get_product_info_list(
                product_ids=[product.ozon_product_id]
            )
            barcode_value = _pick_barcode(info_list)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch product info for barcode: %s", exc)

        if not barcode_value:
            try:
                barcodes = await client.generate_barcodes(
                    product_ids=[product.ozon_product_id]
                )
                generated = barcodes[0] if barcodes else None
                if generated:
                    barcode_value = str(generated)
                    try:
                        await client.add_barcode(product.sku, barcode_value)
                    except Exception as exc:  # noqa: BLE001
                        logger.info(
                            "Could not attach barcode %s to offer %s: %s",
                            barcode_value,
                            product.sku,
                            exc,
                        )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Barcode generation failed: %s", exc)

        if not barcode_value:
            try:
                info_list = await client.get_product_info_list(
                    product_ids=[product.ozon_product_id]
                )
                barcode_value = _pick_barcode(info_list)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to re-fetch product info after barcode generation: %s",
                    exc,
                )
    except Exception as exc:  # noqa: BLE001
        logger.exception("ensure_ozon_barcode failed for product %s: %s", product.sku, exc)
        return None

    if barcode_value:
        product.barcode = barcode_value
        try:
            await update_barcode_in_cache(product.sku, barcode_value)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to update barcode cache for %s: %s", product.sku, exc)
        return barcode_value

    return None


@router.callback_query(
    WarehouseCallbackData.filter(F.action.in_({"labels_yes", "labels_no"}))
)
async def handle_labels(callback: CallbackQuery, callback_data: WarehouseCallbackData, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    product_raw = data.get("selected_product")
    qty = data.get("quantity")
    logger.info(
        "Warehouse labels: action=%s, product_raw=%r, qty=%r",
        callback_data.action,
        product_raw,
        qty,
    )
    if not product_raw or qty is None:
        await send_ephemeral_from_callback(callback, "Не выбрали товар или количество.")
        await state.clear()
        return
    product = CatalogProduct.model_validate(product_raw)

    stored_product, box = await _record_receipt(product, int(qty))
    total = STORE.total_quantity(stored_product.sku)

    if callback_data.action == "labels_yes":
        client = get_client()
        barcode_value = await ensure_ozon_barcode(client, product)

        logger.info(
            "Warehouse labels: final Ozon barcode for product %s is %r (qty=%s)",
            product.name,
            barcode_value,
            qty,
        )

        if not barcode_value:
            await send_ephemeral_from_callback(
                callback,
                "Не удалось получить штрихкод от Ozon для этого товара. Этикетки не сформированы.",
            )
            await state.clear()
            await send_section_message(
                SECTION_WAREHOUSE_PROMPT,
                text=(
                    f"✅ Записал: {stored_product.name} — {qty} шт. "
                    f"Текущий остаток в цеху: {total} шт."
                ),
                callback=callback,
                user_id=callback.from_user.id,
            )
            return

        product.barcode = barcode_value
        try:
            pdf_bytes = await build_labels_pdf(product, int(qty))
            file = BufferedInputFile(
                pdf_bytes,
                filename=f"labels_{product.sku}_{qty}.pdf",
            )
            await callback.message.answer_document(
                file,
                caption=f"Этикетки для {product.name} × {qty}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Failed to build/send labels file for sku=%s, barcode=%r, qty=%s: %s",
                product.sku,
                product.barcode,
                qty,
                exc,
            )
            await send_ephemeral_from_callback(
                callback,
                "Записал количество, но не смог сформировать файл этикеток.",
            )

    await state.clear()
    await send_section_message(
        SECTION_WAREHOUSE_PROMPT,
        text=f"✅ Записал: {stored_product.name} — {qty} шт. Текущий остаток в цеху: {total} шт.",
        callback=callback,
        user_id=callback.from_user.id,
    )


@router.callback_query(WarehouseCallbackData.filter(F.action == "receive_ai"))
async def receive_ai_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(WarehouseStates.receive_ai_text)
    await send_section_message(
        SECTION_WAREHOUSE_PROMPT,
        text=(
            "Опишите текстом, что вы произвели (например: \"3 стеллажа Loft 3-ярусных, "
            "2 обувницы с сиденьем и 1 кофейный столик\")."
        ),
        callback=callback,
        user_id=callback.from_user.id,
    )


@router.message(WarehouseStates.receive_ai_text, F.text)
async def receive_ai_text(message: Message, state: FSMContext) -> None:
    text = message.text or ""
    catalog = await get_catalog()
    items = await parse_production_text_to_items(text, catalog)
    if not items:
        await message.answer(
            "Не удалось распознать товары. Попробуйте ещё раз или вернитесь к ручному режиму."
        )
        return

    await state.update_data(ai_items=[{"sku": p.sku, "quantity": q} for p, q in items])
    await state.set_state(WarehouseStates.receive_ai_confirm)

    lines = ["Понял так:"]
    for product, qty in items:
        lines.append(f"• {product.name} — {qty} шт")
    lines.append("Всё верно?")

    await send_section_message(
        SECTION_WAREHOUSE_PROMPT,
        text="\n".join(lines),
        message=message,
        reply_markup=warehouse_ai_confirmation_keyboard(),
        user_id=message.from_user.id,
    )


@router.callback_query(
    WarehouseCallbackData.filter(
        F.action.in_({"receive_ai_confirm_yes", "receive_ai_confirm_no"})
    )
)
async def receive_ai_confirm(callback: CallbackQuery, callback_data: WarehouseCallbackData, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    items_raw = data.get("ai_items") or []
    if callback_data.action == "receive_ai_confirm_no":
        await state.set_state(WarehouseStates.receive_ai_text)
        await send_section_message(
            SECTION_WAREHOUSE_PROMPT,
            text="Введите текст заново или вернитесь к ручному режиму.",
            callback=callback,
            user_id=callback.from_user.id,
        )
        return

    catalog = await get_catalog()
    catalog_map = {item.sku: item for item in catalog}
    summary_lines = []
    for entry in items_raw:
        sku = entry.get("sku")
        qty = int(entry.get("quantity") or 0)
        if not sku or qty <= 0:
            continue
        product = catalog_map.get(sku)
        if not product:
            continue
        stored_product, _ = await _record_receipt(product, qty)
        total = STORE.total_quantity(stored_product.sku)
        summary_lines.append(f"{stored_product.name} — {qty} шт (остаток: {total})")

    await state.clear()
    if not summary_lines:
        await send_ephemeral_from_callback(callback, "Не удалось записать приёмку.")
        return

    await send_section_message(
        SECTION_WAREHOUSE_PROMPT,
        text="✅ Записал:\n" + "\n".join(summary_lines),
        callback=callback,
        user_id=callback.from_user.id,
    )


def _extract_posting_items(payload: dict | None) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    products = payload.get("products") or payload.get("items")
    if isinstance(products, list):
        return [p for p in products if isinstance(p, dict)]
    return []


def _build_pick_plan(items: list[dict]) -> list[dict]:
    plan: list[dict] = []
    for item in items:
        offer_id = item.get("offer_id")
        sku_val = item.get("sku")
        qty = int(item.get("quantity") or item.get("quantity_fbs") or 0)
        if qty <= 0:
            continue

        product = STORE.find_product_by_ozon(offer_id=offer_id, sku=sku_val)
        if not product and offer_id:
            product = STORE.find_product(offer_id)
        if not product:
            # Create placeholder to move forward with planning
            fallback_sku = offer_id or str(sku_val or "unknown")
            product = Product(sku=fallback_sku, name=item.get("name") or fallback_sku)
            STORE.save_product(product)

        boxes = sorted(
            STORE.list_boxes_for_product(product.sku), key=lambda b: b.quantity, reverse=True
        )
        remaining = qty
        allocations: list[dict[str, Any]] = []
        for box in boxes:
            if remaining <= 0:
                break
            take = min(box.quantity, remaining)
            if take <= 0:
                continue
            allocations.append(
                {
                    "box_id": box.id,
                    "take": take,
                    "location": box.location.id,
                    "available": box.quantity,
                }
            )
            remaining -= take

        plan.append(
            {
                "product_sku": product.sku,
                "product_name": product.name,
                "offer_id": offer_id,
                "sku": sku_val,
                "requested": qty,
                "allocations": allocations,
                "missing": max(0, remaining),
            }
        )
    return plan


def _format_pick_plan(posting_number: str, plan: list[dict]) -> str:
    if not plan:
        return "План отбора пуст — подходящих товаров нет на складе."

    lines = [f"План отбора для заказа {posting_number}:"]
    for entry in plan:
        lines.append(
            f"- {entry['product_name']} — взять {entry['requested']} шт:"
        )
        if entry.get("allocations"):
            for alloc in entry["allocations"]:
                lines.append(
                    f"  • из Box {alloc['box_id']} (место: {alloc['location']}) — {alloc['take']} шт"
                )
        if entry.get("missing"):
            lines.append(
                f"  ⚠️ Недоступно {entry['missing']} шт на локальном складе"
            )
    return "\n".join(lines)


@router.callback_query(WarehouseCallbackData.filter(F.action == "pick"))
async def start_pick(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await state.set_state(WarehouseStates.pick_posting_number)
    await send_section_message(
        SECTION_WAREHOUSE_PROMPT,
        text="Введите номер заказа/posting_number",
        callback=callback,
        user_id=callback.from_user.id,
    )


@router.message(WarehouseStates.pick_posting_number, F.text)
async def handle_pick_posting(message: Message, state: FSMContext) -> None:
    posting = (message.text or "").strip()
    if not posting:
        await message.answer("Введите корректный номер заказа/posting_number")
        return

    payload, schema = await get_posting_details(posting)
    if not payload:
        await message.answer("Не удалось найти заказ, проверьте номер и попробуйте снова.")
        await state.clear()
        return

    if schema == "fbo":
        await message.answer(
            "Этот заказ выполняется со склада Ozon (FBO), отбор на вашем складе не требуется."
        )
        await state.clear()
        return

    items = _extract_posting_items(payload)
    if not items:
        await message.answer("В заказе нет позиций для отбора.")
        await state.clear()
        return

    plan = _build_pick_plan(items)
    await state.update_data(pick_plan=plan, posting_number=posting)
    await send_section_message(
        SECTION_WAREHOUSE_PLAN,
        text=_format_pick_plan(posting, plan),
        reply_markup=pick_plan_keyboard(posting),
        message=message,
        user_id=message.from_user.id,
    )


def _apply_pick_plan(posting_number: str, plan: list[dict]) -> None:
    for entry in plan:
        product = STORE.products.get(entry.get("product_sku") or "")
        if not product:
            continue
        total_taken = 0
        for alloc in entry.get("allocations", []):
            box = STORE.get_box(alloc.get("box_id", ""))
            if not box:
                continue
            take = int(alloc.get("take") or 0)
            if take <= 0:
                continue
            box.quantity = max(0, box.quantity - take)
            total_taken += take
        if total_taken:
            movement_id = generate_movement_id(set(STORE.movements.keys()))
            movement = Movement(
                id=movement_id,
                type="DISPATCH",
                product=product,
                quantity=-total_taken,
                timestamp=datetime.utcnow(),
                reference=posting_number,
            )
            STORE.save_movement(movement)
        # Boxes with zero quantity are kept to preserve history; clean-up can happen later.


@router.callback_query(WarehouseCallbackData.filter(F.action == "pick_confirm"))
async def confirm_pick(callback: CallbackQuery, callback_data: WarehouseCallbackData, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    plan = data.get("pick_plan") or []
    posting_number = data.get("posting_number") or ""
    _apply_pick_plan(posting_number, plan)
    await state.clear()
    await delete_section_message(callback.from_user.id, SECTION_WAREHOUSE_PLAN, callback.message.bot)
    await send_ephemeral_message(
        callback,
        f"✅ Отбор по заказу {posting_number} завершён. Остатки на складе обновлены.",
    )


@router.callback_query(WarehouseCallbackData.filter(F.action == "pick_cancel"))
async def cancel_pick(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await delete_section_message(callback.from_user.id, SECTION_WAREHOUSE_PLAN, callback.message.bot)
    await send_ephemeral_from_callback(callback, "Отбор отменён.")


@router.callback_query(WarehouseCallbackData.filter(F.action == "inventory"))
async def start_inventory(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await state.set_state(WarehouseStates.inventory_wait_box)
    await state.update_data(inventory_diffs=[])
    await send_section_message(
        SECTION_WAREHOUSE_PROMPT,
        text=(
            "Инвентаризация: по очереди вводите или сканируйте id коробки (BoxID). "
            "Отправьте \"стоп\" для завершения."
        ),
        callback=callback,
        user_id=callback.from_user.id,
    )


@router.message(WarehouseStates.inventory_wait_box, F.text)
async def inventory_wait_box(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text.lower() == "стоп":
        data = await state.get_data()
        diffs: list[dict] = data.get("inventory_diffs") or []
        if not diffs:
            summary = "Инвентаризация завершена без расхождений."
        else:
            summary_lines = ["Итог инвентаризации:"]
            for diff in diffs:
                summary_lines.append(
                    f"- Box {diff['box_id']}: было {diff['was']}, фактически {diff['actual']} (Δ = {diff['delta']})"
                )
            summary = "\n".join(summary_lines)
        await send_section_message(
            SECTION_WAREHOUSE_PROMPT,
            text=summary,
            message=message,
            user_id=message.from_user.id,
        )
        await state.clear()
        # TODO: sync inventory deltas to Ozon for FBS via product_import_stocks
        return

    box = STORE.get_box(text)
    if not box:
        await message.answer("Коробка не найдена, попробуйте ещё раз или отправьте \"стоп\".")
        return

    await state.update_data(current_box=box.id)
    await state.set_state(WarehouseStates.inventory_wait_count)
    await message.answer(
        f"Коробка {box.id}, товар: {box.product.name}, учетное количество: {box.quantity}. "
        "Введите фактическое количество."
    )


@router.message(WarehouseStates.inventory_wait_count, F.text)
async def inventory_wait_count(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    try:
        fact = int(text)
        if fact < 0:
            raise ValueError
    except Exception:
        await message.answer("Введите неотрицательное целое число или \"стоп\" для завершения.")
        return

    data = await state.get_data()
    box_id = data.get("current_box")
    box = STORE.get_box(box_id or "") if box_id else None
    if not box:
        await message.answer("Не удалось найти коробку, начните инвентаризацию заново.")
        await state.clear()
        return

    was = box.quantity
    if fact == was:
        await message.answer(f"✅ ОК ({fact} шт)")
    else:
        delta = fact - was
        box.quantity = fact
        movement_id = generate_movement_id(set(STORE.movements.keys()))
        movement = Movement(
            id=movement_id,
            type="INVENTORY",
            product=box.product,
            quantity=delta,
            timestamp=datetime.utcnow(),
            reference=None,
            to_box=box,
        )
        STORE.save_movement(movement)
        diffs: list[dict] = data.get("inventory_diffs") or []
        diffs.append({"box_id": box.id, "was": was, "actual": fact, "delta": delta})
        await state.update_data(inventory_diffs=diffs)
        await message.answer(
            f"⚠️ Расхождение по Box {box.id}: было {was}, фактически {fact} (Δ = {delta})."
        )

    await state.set_state(WarehouseStates.inventory_wait_box)
    await state.update_data(current_box=None)
    await message.answer(
        "Инвентаризация: введите следующий BoxID или отправьте \"стоп\" для завершения."
    )


"""Warehouse bot flows: receiving, picking, inventory."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from botapp.keyboards import (
    MenuCallbackData,
    WarehouseCallbackData,
    pick_plan_keyboard,
    warehouse_menu_keyboard,
)
from botapp.message_gc import (
    SECTION_WAREHOUSE_MENU,
    SECTION_WAREHOUSE_PLAN,
    SECTION_WAREHOUSE_PROMPT,
    delete_section_message,
    send_section_message,
)
from botapp.ozon_client import get_posting_details
from botapp.states import WarehouseStates
from botapp.utils import send_ephemeral_message
from botapp.warehouse_models import (
    Box,
    Location,
    Movement,
    Product,
    generate_box_id,
    generate_movement_id,
)

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

    def get_or_create_location(self, location_id: str, name: str | None = None) -> Location:
        if location_id in self.locations:
            return self.locations[location_id]
        location = Location(id=location_id, name=name)
        self.locations[location_id] = location
        return location

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
    await send_ephemeral_message(callback, "Скоро покажем риск остатков.")


@router.callback_query(WarehouseCallbackData.filter(F.action == "ask_ai"))
async def warehouse_ai_stub(callback: CallbackQuery) -> None:
    await callback.answer()
    await send_ephemeral_message(callback, "ИИ-помощник для склада появится позже.")


@router.callback_query(WarehouseCallbackData.filter(F.action == "receive"))
async def start_receive(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await state.set_state(WarehouseStates.receive_product)
    await send_section_message(
        SECTION_WAREHOUSE_PROMPT,
        text="Приёмка: отправьте SKU, название или штрих-код товара.",
        callback=callback,
        user_id=callback.from_user.id,
    )


@router.message(WarehouseStates.receive_product, F.text)
async def handle_receive_product(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        await message.answer("Приёмка отменена.")
        return
    if not text:
        await message.answer("Введите текст с SKU, штрих-кодом или названием.")
        return

    data = await state.get_data()
    awaiting_new = data.get("awaiting_new_name")

    if awaiting_new:
        sku_raw = data.get("new_product_sku") or text
        product = Product(sku=sku_raw, name=text)
        STORE.save_product(product)
        await state.update_data(product=product.model_dump(), awaiting_new_name=False)
    else:
        found = STORE.find_product(text)
        if found:
            await state.update_data(product=found.model_dump())
        else:
            await state.update_data(awaiting_new_name=True, new_product_sku=text)
            await message.answer(
                "Товар не найден. Введите название для нового товара или отправьте /cancel для отмены"
            )
            return

    await state.set_state(WarehouseStates.receive_quantity)
    await send_section_message(
        SECTION_WAREHOUSE_PROMPT,
        text="Сколько единиц принять на склад?",
        message=message,
        user_id=message.from_user.id,
    )


@router.message(WarehouseStates.receive_quantity, F.text)
async def handle_receive_quantity(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        await message.answer("Приёмка отменена.")
        return
    try:
        qty = int(text)
        if qty <= 0:
            raise ValueError
    except Exception:
        await message.answer("Введите целое положительное число.")
        return

    await state.update_data(quantity=qty)
    await state.set_state(WarehouseStates.receive_location)
    await send_section_message(
        SECTION_WAREHOUSE_PROMPT,
        text="Укажите местоположение (например, A1-05-02) или отправьте \"-\" для пропуска.",
        message=message,
        user_id=message.from_user.id,
    )


@router.message(WarehouseStates.receive_location, F.text)
async def handle_receive_location(message: Message, state: FSMContext) -> None:
    location_raw = (message.text or "").strip()
    if location_raw.lower() == "/cancel":
        await state.clear()
        await message.answer("Приёмка отменена.")
        return
    location_id = "UNASSIGNED" if location_raw == "-" else (location_raw or "UNASSIGNED")
    location_name = None if location_id != "UNASSIGNED" else "Без ячейки"
    location = STORE.get_or_create_location(location_id, name=location_name)

    data = await state.get_data()
    product_data = data.get("product")
    qty = data.get("quantity")
    if not product_data or qty is None:
        await message.answer("Не удалось определить товар, попробуйте начать приёмку заново.")
        await state.clear()
        return

    product = _deserialize_product(product_data)
    box_id = generate_box_id(set(STORE.boxes.keys()))
    box = Box(
        id=box_id,
        product=product,
        quantity=int(qty),
        location=location,
        created_at=datetime.utcnow(),
    )
    STORE.save_box(box)

    movement_id = generate_movement_id(set(STORE.movements.keys()))
    movement = Movement(
        id=movement_id,
        type="RECEIPT",
        product=product,
        quantity=int(qty),
        to_box=box,
        timestamp=datetime.utcnow(),
    )
    STORE.save_movement(movement)

    await state.clear()
    await send_section_message(
        SECTION_WAREHOUSE_PROMPT,
        text=(
            f"✅ Принято {qty} шт товара {product.name} в коробку {box.id} "
            f"(место: {location.id})."
        ),
        message=message,
        user_id=message.from_user.id,
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
    posting_number = callback_data.posting_number or data.get("posting_number") or ""
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
    await send_ephemeral_message(callback, "Отбор отменён.")


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


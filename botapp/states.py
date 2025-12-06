from aiogram.fsm.state import State, StatesGroup


class QuestionAnswerStates(StatesGroup):
    manual = State()
    reprompt = State()


class ChatStates(StatesGroup):
    waiting_manual = State()
    waiting_ai_confirm = State()


class WarehouseStates(StatesGroup):
    receive_product_manual = State()
    receive_search_name = State()
    receive_search_sku = State()
    receive_quantity_manual = State()
    receive_ai_text = State()
    receive_ai_confirm = State()
    pick_posting_number = State()
    inventory_wait_box = State()
    inventory_wait_count = State()
    ask_ai_question = State()


__all__ = ["QuestionAnswerStates", "ChatStates", "WarehouseStates"]

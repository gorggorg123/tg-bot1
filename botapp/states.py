from aiogram.fsm.state import State, StatesGroup


class QuestionAnswerStates(StatesGroup):
    manual = State()
    reprompt = State()


class ChatStates(StatesGroup):
    waiting_manual = State()
    waiting_ai_confirm = State()


__all__ = ["QuestionAnswerStates", "ChatStates"]

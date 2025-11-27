from aiogram.fsm.state import State, StatesGroup


class QuestionAnswerStates(StatesGroup):
    manual = State()
    reprompt = State()


__all__ = ["QuestionAnswerStates"]

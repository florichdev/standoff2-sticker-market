from aiogram.fsm.state import State, StatesGroup


class ChatStates(StatesGroup):
    """FSM states for active anonymous chat session."""

    in_chat = State()

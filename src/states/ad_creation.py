from aiogram.fsm.state import State, StatesGroup


class AdCreationStates(StatesGroup):
    """FSM states for listing creation wizard."""

    waiting_for_photo = State()
    waiting_for_category = State()
    waiting_for_weapon = State()
    waiting_for_skin = State()
    waiting_for_sticker_count = State()
    waiting_for_sticker_rarity = State()
    waiting_for_price = State()

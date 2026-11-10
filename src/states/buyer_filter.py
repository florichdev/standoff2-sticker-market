from aiogram.fsm.state import State, StatesGroup


class BuyerFilterStates(StatesGroup):
    """FSM states for buyer's search filter configurator."""

    waiting_for_category = State()
    waiting_for_weapon = State()
    waiting_for_skin = State()
    waiting_for_sticker_count = State()
    waiting_for_sticker_rarity = State()
    waiting_for_price_sort = State()
    viewing_filtered_feed = State()

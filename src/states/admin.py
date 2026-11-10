from aiogram.fsm.state import State, StatesGroup


class ReportStates(StatesGroup):
    """FSM states for filing a report against an ad or chat participant."""

    waiting_for_reason = State()


class BroadcastStates(StatesGroup):
    """FSM states for admin mass messaging."""

    waiting_for_content = State()
    waiting_for_confirmation = State()


class AdminUserSearchStates(StatesGroup):
    """FSM states for user lookup by Telegram ID in admin panel."""

    waiting_for_user_id = State()

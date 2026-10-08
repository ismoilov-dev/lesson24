"""Admin group: approve / reject orders with inline buttons.

Only users with the admin role (who have logged in to the bot once, so their `telegram_id`
is known) can press the buttons. Rejecting asks for a reason as a reply (ForceReply), which
reaches the bot even with group privacy mode on.
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ForceReply, Message
from asgiref.sync import sync_to_async

from apps.errors import ServiceError
from apps.payments import services
from apps.payments.models import Order
from apps.users.models import User
from apps.users.services import get_bot_user

from .notify import APPROVE, REJECT

router = Router(name="orders")


class RejectOrder(StatesGroup):
    reason = State()


@sync_to_async
def _admin(telegram_id: int) -> User | None:
    user = get_bot_user(telegram_id)
    return user if user and user.is_active and user.is_admin_role else None


@sync_to_async
def _get_order(order_id: int) -> Order | None:
    return Order.objects.filter(pk=order_id).first()


approve_order = sync_to_async(services.approve_order)
reject_order = sync_to_async(services.reject_order)


def _done_caption(original: str | None, line: str) -> str:
    return f"{original or ''}\n\n{line}"


@router.callback_query(F.data.startswith("ord:"))
async def order_button(callback: CallbackQuery, state: FSMContext) -> None:
    admin = await _admin(callback.from_user.id)
    if admin is None:
        await callback.answer("Faqat adminlar uchun. Avval botga kiring.", show_alert=True)
        return
    _, action, raw_id = callback.data.split(":")
    order = await _get_order(int(raw_id))
    if order is None:
        await callback.answer("Buyurtma topilmadi.", show_alert=True)
        return

    if action == APPROVE:
        try:
            await approve_order(order, admin)
        except ServiceError as exc:
            await callback.answer(exc.message, show_alert=True)
            return
        await callback.message.edit_caption(
            caption=_done_caption(callback.message.caption, f"✅ Tasdiqladi: {admin}"),
            reply_markup=None,
        )
        await callback.answer("Tasdiqlandi")
    elif action == REJECT:
        await state.set_state(RejectOrder.reason)
        await state.update_data(order_id=order.pk, message_id=callback.message.message_id)
        # ForceReply: guruhda privacy mode yoqilgan bo'lsa ham bot javob xabarini oladi
        await callback.message.reply(
            f"#{order.pk} buyurtmani rad etish sababini shu xabarga javob qilib yozing "
            "(bekor qilish: /cancel).",
            reply_markup=ForceReply(selective=True),
        )
        await callback.answer()


@router.message(RejectOrder.reason, Command("cancel"))
async def cancel_reject(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.reply("Bekor qilindi.")


@router.message(RejectOrder.reason, F.text)
async def reject_reason(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    admin = await _admin(message.from_user.id)
    order = await _get_order(data["order_id"])
    if admin is None or order is None:
        return
    try:
        await reject_order(order, admin, message.text)
    except ServiceError as exc:
        await message.reply(f"❗ {exc.message}")
        return
    await message.bot.edit_message_reply_markup(
        chat_id=message.chat.id, message_id=data["message_id"], reply_markup=None
    )
    await message.reply(f"❌ #{order.pk} rad etildi: {admin}")

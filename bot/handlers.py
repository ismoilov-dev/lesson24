"""Bot flow: /start → share contact → 10-minute login code → enter it on the site.

Only a contact the user shares from their own account is accepted (`contact.user_id`
must match the sender), so nobody can request a code for someone else's number.
"""

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from asgiref.sync import sync_to_async

from apps.errors import ServiceError
from apps.users import services
from apps.users.models import OneTimeCode, User

router = Router(name="auth")
# Login oqimi faqat shaxsiy chatda; admin guruhidagi xabarlarga javob bermaymiz
router.message.filter(F.chat.type == "private")

NEW_CODE = "new_code"
CODE_TTL_MIN = int(OneTimeCode.TTL[OneTimeCode.Purpose.LOGIN].total_seconds() // 60)

CONTACT_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True,
)
NEW_CODE_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="🔄 Yangi kod", callback_data=NEW_CODE)]]
)

get_bot_user = sync_to_async(services.get_bot_user)
issue_bot_login = sync_to_async(services.issue_bot_login)
register_from_contact = sync_to_async(services.register_from_contact)


def code_text(otc: OneTimeCode, student: User | None = None) -> str:
    text = (
        f"🔐 Kirish kodi: <code>{otc.code}</code>\n\n"
        f"Kodni saytdagi kirish sahifasiga kiriting. U {CODE_TTL_MIN} daqiqa amal qiladi "
        "va faqat bir marta ishlatiladi.\n"
        "Kodni hech kimga bermang."
    )
    if student:
        text = f"✅ Farzandingiz bog'landi: <b>{student}</b>\n\n" + text
    return text


@router.message(CommandStart())
async def start(message: Message, command: CommandObject, state: FSMContext) -> None:
    invite = command.args[2:] if command.args and command.args.startswith("p_") else None
    user = await get_bot_user(message.from_user.id)
    if user is None:
        # Yangi foydalanuvchi: avval telefon raqam
        await state.update_data(invite=invite)
        await message.answer(
            "Assalomu alaykum! Lesson24 ga kirish uchun telefon raqamingizni yuboring 👇",
            reply_markup=CONTACT_KEYBOARD,
        )
        return
    # Raqami allaqachon tasdiqlangan — kodni darhol beramiz
    try:
        otc, student = await issue_bot_login(user, invite)
    except ServiceError as exc:
        await message.answer(f"❗ {exc.message}")
        return
    await message.answer(code_text(otc, student), reply_markup=NEW_CODE_KEYBOARD)


@router.message(F.contact)
async def contact(message: Message, state: FSMContext) -> None:
    if message.contact.user_id != message.from_user.id:
        await message.answer(
            "Iltimos, pastdagi tugma orqali <b>o'z</b> raqamingizni yuboring.",
            reply_markup=CONTACT_KEYBOARD,
        )
        return
    invite = (await state.get_data()).get("invite")
    try:
        _, otc, student = await register_from_contact(
            telegram_id=message.from_user.id,
            phone=message.contact.phone_number,
            first_name=message.contact.first_name or message.from_user.first_name or "",
            last_name=message.contact.last_name or message.from_user.last_name or "",
            invite_code=invite,
        )
    except ServiceError as exc:
        await message.answer(f"❗ {exc.message}", reply_markup=ReplyKeyboardRemove())
        return
    finally:
        await state.clear()
    await message.answer("Raqamingiz qabul qilindi ✅", reply_markup=ReplyKeyboardRemove())
    await message.answer(code_text(otc, student), reply_markup=NEW_CODE_KEYBOARD)


@router.callback_query(F.data == NEW_CODE)
async def new_code(callback: CallbackQuery) -> None:
    user = await get_bot_user(callback.from_user.id)
    if user is None:
        await callback.answer("Avval /start bosing", show_alert=True)
        return
    try:
        otc, _ = await issue_bot_login(user)
    except ServiceError as exc:
        await callback.answer(exc.message, show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(code_text(otc), reply_markup=NEW_CODE_KEYBOARD)


@router.message()
async def fallback(message: Message) -> None:
    user = await get_bot_user(message.from_user.id)
    if user is None:
        await message.answer(
            "Raqamni yozib emas, pastdagi tugma orqali yuboring 👇", reply_markup=CONTACT_KEYBOARD
        )
    else:
        await message.answer(
            "Yangi kirish kodi uchun tugmani bosing.", reply_markup=NEW_CODE_KEYBOARD
        )

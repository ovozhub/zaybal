import logging
import asyncio
import os
from aiohttp import web

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ConversationHandler, MessageHandler, filters, ContextTypes
from telethon import TelegramClient, errors
from telethon.tl.functions.channels import CreateChannelRequest, InviteToChannelRequest

# 📂 sessions papkasini yaratamiz
os.makedirs("sessions", exist_ok=True)

# ——— TELEGRAM API ma’lumotlari ———
api_id = 25351311
api_hash = "7b854af9996797aa9ca67b42f1cd5cbe"
bot_token = "7352312639:AAEwQHVq5Uwhmnkc3ITk5vPLhVrRxCOWTcs"

# 🔑 Kirish paroli
ACCESS_PASSWORD = "dnx"

# 🎯 Har bir guruhga avtomatik qo'shiladigan bot
TARGET_BOT = "@oxang_bot"

# ——— LOGGER ———
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ——— Holatlar ———
ASK_PASSWORD, SELECT_MODE, PHONE, CODE, PASSWORD, GROUP_RANGE = range(6)

# ——— Session va avtorizatsiya boshqaruvlari ———
sessions = {}  # {user_id: TelegramClient}
authorized_users = set()  # Parol kiritgan userlar ID si


# ——— START ———
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in authorized_users:
        return await show_menu(update)
    else:
        await update.message.reply_text("🔒 Kirish parolini kiriting:")
        return ASK_PASSWORD


# ——— Parol tekshirish ———
async def ask_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if update.message.text.strip() != ACCESS_PASSWORD:
        await update.message.reply_text("❌ Noto‘g‘ri parol.")
        return ConversationHandler.END
    authorized_users.add(user_id)
    return await show_menu(update)


# ——— Menyu chiqarish ———
async def show_menu(update: Update):
    keyboard = [
        [InlineKeyboardButton("Guruh ochish", callback_data='create_group'),
         InlineKeyboardButton("Guruhni topshirish", callback_data='transfer_group')]
    ]
    if update.message:
        await update.message.reply_text("Rejimni tanlang⚙️", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.callback_query.message.reply_text("Rejimni tanlang⚙️", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_MODE


# ——— Rejim tanlash ———
async def mode_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['mode'] = query.data
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Telefon raqamni yuborish", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )
    await query.message.reply_text("📞 Telefon raqamingizni yuboring:", reply_markup=keyboard)
    return PHONE


# ——— Telefon qabul qilish ———
async def phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.contact.phone_number if update.message.contact else update.message.text
    if not phone.startswith('+'):
        await update.message.reply_text("Telefon raqam + bilan boshlanishi kerak.")
        return PHONE

    context.user_data['phone'] = phone
    client = TelegramClient(f"sessions/{phone}", api_id, api_hash)
    sessions[update.effective_user.id] = client

    await client.connect()
    if not await client.is_user_authorized():
        try:
            await client.send_code_request(phone)
            await update.message.reply_text("📩 Kod yuborildi, kiriting:")
            return CODE
        except Exception as e:
            await update.message.reply_text(f"❌ Xato: {e}")
            return ConversationHandler.END
    else:
        await update.message.reply_text("✅ Akkount allaqachon ulangan.")
        return await after_login(update, context)


# ——— Kod qabul qilish ———
async def code_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    client = sessions.get(user_id)
    phone = context.user_data.get('phone')

    try:
        await client.sign_in(phone, update.message.text.strip())
    except errors.SessionPasswordNeededError:
        await update.message.reply_text("🔑 2 bosqichli parolni kiriting:")
        return PASSWORD
    except Exception as e:
        await update.message.reply_text(f"❌ Xato: {e}")
        return ConversationHandler.END

    return await after_login(update, context)


# ——— 2FA parol ———
async def password_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    client = sessions.get(user_id)
    try:
        await client.sign_in(password=update.message.text.strip())
    except Exception as e:
        await update.message.reply_text(f"❌ Xato: {e}")
        return ConversationHandler.END
    return await after_login(update, context)


# ——— Login tugagach ———
async def after_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Nechta guruh yaratilsin? (masalan 1-5)")
    return GROUP_RANGE


# ——— Guruh yaratish jarayoni ———
async def background_group_creator(user_id, client, start, end, mode, context):
    created_channels = []
    for i in range(start, end + 1):
        try:
            result = await client(CreateChannelRequest(
                title=f"Guruh #{i}", about="Guruh sotiladi", megagroup=True
            ))
            channel = result.chats[0]
            created_channels.append(channel)
            try:
                await client(InviteToChannelRequest(channel, [TARGET_BOT]))
                await context.bot.send_message(user_id, f"✅ Guruh #{i} yaratildi va {TARGET_BOT} qo‘shildi.")
            except Exception as e:
                await context.bot.send_message(user_id, f"⚠ Guruh #{i} yaratildi, lekin bot qo‘shilmadi: {e}")
        except Exception as e:
            await context.bot.send_message(user_id, f"❌ Guruh #{i} yaratishda xato: {e}")
        await asyncio.sleep(3)

    if mode == 'create_group':
        await context.bot.send_message(user_id, f"🏁 {len(created_channels)} ta guruh yaratildi.")
    await client.disconnect()
    sessions.pop(user_id, None)


# ——— Guruhlar soni qabul qilish ———
async def group_range_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        start, end = map(int, update.message.text.strip().split('-'))
    except:
        await update.message.reply_text("❌ Noto‘g‘ri format.")
        return GROUP_RANGE

    client = sessions.get(update.effective_user.id)
    mode = context.user_data.get('mode')

    await update.message.reply_text("⏳ Guruh yaratish jarayoni boshlandi...")
    asyncio.create_task(background_group_creator(update.effective_user.id, client, start, end, mode, context))
    return ConversationHandler.END


# ——— Bekor qilish ———
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Bekor qilindi.")
    client = sessions.pop(update.effective_user.id, None)
    if client:
        await client.disconnect()
    return ConversationHandler.END


# 🌐 WEB SERVER (Render uchun)
async def handle(request):
    return web.Response(text="Bot alive!")


async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"🌐 Web-server {port} portda ishga tushdi.")


# 🤖 BOTNI ISHGA TUSHIRISH
async def run_bot():
    application = Application.builder().token(bot_token).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_password)],
            SELECT_MODE: [CallbackQueryHandler(mode_chosen)],
            PHONE: [MessageHandler(filters.TEXT | filters.CONTACT, phone_received)],
            CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, code_received)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, password_received)],
            GROUP_RANGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, group_range_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv_handler)
    logger.info("🤖 Bot ishga tushdi.")

    await application.run_polling()


# ASOSIY ISHGA TUSHIRISH
async def main():
    await asyncio.gather(
        start_webserver(),
        run_bot()
    )


if __name__ == "__main__":
    asyncio.run(main())

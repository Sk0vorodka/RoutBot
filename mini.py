# mini_bot.py
import os
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# Включаем простое логирование прямо в консоль (увидишь в логах Infrlo)
logging.basicConfig(level=logging.INFO)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
API_KEY = os.environ.get("CRAX_API_KEY")

if not TOKEN:
    raise ValueError("Переменная TELEGRAM_BOT_TOKEN не задана")
if not API_KEY:
    raise ValueError("Переменная CRAX_API_KEY не задана")


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Работаю. Пинг успешен.")


def main():
    logging.info("Бот запускается...")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT, echo))
    app.run_polling()


if __name__ == "__main__":
    main()

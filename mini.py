# mini_bot.py
import os
import logging
import aiohttp
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
API_KEY = os.environ.get("CRAX_API_KEY")

if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не найден")
if not API_KEY:
    raise ValueError("CRAX_API_KEY не найден")


async def test_crax_api():
    """Тестовый запрос к Crax"""
    url = "https://gpt.crax.lol/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-5-6-luna",
        "messages": [{"role": "user", "content": "Ответь одно слово: привет"}],
        "temperature": 0.7
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                data = await resp.json()
                if resp.status == 200:
                    reply = data["choices"][0]["message"]["content"]
                    return f"✅ Успешно: {reply}"
                else:
                    return f"❌ Ошибка API ({resp.status}): {data}"
    except Exception as e:
        return f"💥 Исключение: {e}"


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Проверяю подключение к Crax...")
    result = await test_crax_api()
    await msg.edit_text(result)


def main():
    logging.info("Запуск тестового бота...")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT, handle_message))
    app.run_polling()


if __name__ == "__main__":
    main()

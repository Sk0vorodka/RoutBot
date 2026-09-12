# bot.py
import os
import re
import json
import logging
import subprocess
import aiohttp
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from dotenv import load_dotenv

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()

API_BASE = "https://gpt.crax.lol/v1"
API_KEY = os.getenv("CRAX_API_KEY")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

TEXT_MODEL = "gpt-5-6-luna"
IMAGE_MODEL = "qwen-image-2.0-pro"

if not BOT_TOKEN or not API_KEY:
    logger.error("Нужны TELEGRAM_BOT_TOKEN и CRAX_API_KEY")
    exit(1)

SYSTEM_PROMPT = """Ты Роут. Дружелюбный ИИ в Telegram.

Правила:
- Отвечай по-русски.
- Если просят фото, начни с: IMAGE: <английский промпт>
- Иначе отвечай просто текстом."""

# ---------- API клиента ----------

async def chat_completion(messages: list[dict]) -> str:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": TEXT_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        "temperature": 0.7,
        "stream": False,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE}/chat/completions", headers=headers, json=payload
            ) as resp:
                content_type = resp.headers.get("Content-Type", "")
                if "text/event-stream" in content_type:
                    full_response = ""
                    async for line in resp.content:
                        line = line.decode("utf-8").strip()
                        if line.startswith("data:"):
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                delta = chunk.get("choices", [{}])[0].get("delta", {})
                                content = delta.get("content", "")
                                full_response += content
                            except:
                                continue
                    return full_response
                else:
                    data = await resp.json()
                    if resp.status != 200:
                        raise RuntimeError(data.get("error", data))
                    return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"chat_completion ошибка: {e}")
        raise


async def generate_image(prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": IMAGE_MODEL,
        "prompt": prompt,
        "n": 1,
        "aspect_ratio": "1:1",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE}/images/generations", headers=headers, json=payload
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    raise RuntimeError(data.get("error", data))
                return data["data"][0]["url"]
    except Exception as e:
        logger.error(f"Ошибка генерации изображения: {e}")
        raise


# ---------- Вспомогательные функции ----------

def parse_cmd(text: str):
    text = text.strip()
    if text.upper().startswith("IMAGE:"):
        return "image", text[6:].strip()
    return None, text


def mentioned_in(text: str, bot_username: str) -> bool:
    if not text:
        return False
    t = text.lower()
    return f"@{bot_username.lower()}" in t or re.search(r"\bроут\b", t)


def quoted_content(msg) -> str:
    txt = msg.text or ""
    if msg.reply_to_message:
        repl = msg.reply_to_message.text or "[медиа]"
        return f"[Цитата: {repl}]\n{txt}"
    return txt


# ---------- Основная функция ----------

async def do_request(messages, msg, reply=False):
    async def send(text=None, image_url=None, cap=None):
        kwargs = {"reply_to_message_id": msg.message_id} if reply else {}

        try:
            if image_url:
                await msg.reply_photo(photo=image_url, caption=cap or "", **kwargs)
            else:
                await msg.reply_text(text or "...", **kwargs)
        except Exception:
            fallback_text = f"Готово: {image_url}" + (("\n\n" + cap) if cap else "")
            await msg.reply_text(fallback_text[:1000], **kwargs)

    bot = msg.get_bot()
    await bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)

    try:
        answer = await chat_completion(messages)
        typ, prm = parse_cmd(answer)

        if not typ:
            await send(text=answer)
            return

        st = await msg.reply_text("Генерирую фото...", reply_to_message_id=msg.message_id if reply else None)
        await bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.UPLOAD_PHOTO)
        url = await generate_image(prm)

        # Подпись
        cprm = "Напиши короткий коммент на русском"
        crsp = await chat_completion(messages + [
            {"role": "assistant", "content": answer},
            {"role": "user", "content": cprm},
        ])
        c = crsp.replace("IMAGE:", "").strip()

        await send(image_url=url, cap=c)
        await st.delete()

    except Exception as e:
        logger.error(f"do_request ошибка: {e}", exc_info=True)
        kwargs = {"reply_to_message_id": msg.message_id} if reply else {}
        await msg.reply_text("Ошибка. Попробуй позже.", **kwargs)


# ---------- Хендлеры ----------

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Напиши что-то.")


async def logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_document(document=open("bot.log", "rb"))
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def handle_private(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    await do_request([{"role": "user", "content": quoted_content(msg)}], msg, False)


async def handle_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    un = ctx.bot.username
    if not mentioned_in(msg.text, un):
        return
    await do_request([{"role": "user", "content": quoted_content(msg)}], msg, True)


async def handle_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        return
    if msg.reply_to_message.from_user.is_bot:
        await do_request([{"role": "user", "content": quoted_content(msg)}], msg, True)


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("logs", logs))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, handle_private))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT, handle_group))
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT, handle_reply))
    app.run_polling()


if __name__ == "__main__":
    main()

# bot.py
import os
import re
import json
import logging
import traceback
import asyncio
import aiohttp
import subprocess
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
VIDEO_MODEL = "seedance-2.0"

if not BOT_TOKEN:
    logger.error("Отсутствует TELEGRAM_BOT_TOKEN в .env")
    exit(1)
if not API_KEY:
    logger.error("Отсутствует CRAX_API_KEY в .env")
    exit(1)

SYSTEM_PROMPT = """Тебя зовут Роут. Ты — дружелюбный ИИ-ассистент в Telegram.

Очень важно:
- Отвечай на русском, если не просят иначе.
- Если пользователь просит нарисовать, сгенерировать изображение, картинку, фото, арт — начни свой ответ с:
  IMAGE: <подробный англоязычный промпт для генерации>
- Если пользователь просит сделать видео, ролик, клип, анимацию — начни свой ответ с:
  VIDEO: <подробный англоязычный промпт для генерации>
- Если не просят генерировать медиа — просто отвечай текстом, без префиксов."""

# ---------- API клиенты ----------

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
                url = data["data"][0]["url"]
                return url
    except Exception as e:
        logger.error(f"Ошибка генерации изображения: {e}")
        raise


# ---------- Вспомогательные функции ----------

def is_route_mentioned(text: str, bot_username: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    if f"@{bot_username.lower()}" in lower:
        return True
    if re.search(r"(?<!\w)роут(?!\w)", lower):
        return True
    return False


def build_user_content(message) -> str:
    text = message.text or ""
    if message.reply_to_message and message.reply_to_message.text:
        return (
            f"[Цитата]: {message.reply_to_message.text}\n"
            f"{text}"
        )
    return text


def parse_media_cmd(text: str):
    text = text.strip()
    if text.upper().startswith("IMAGE:"):
        return "image", text[6:].strip()
    if text.upper().startswith("VIDEO:"):
        return "video", text[6:].strip()
    return None, text


# ---------- Основная логика ----------

async def process_request(messages, message, reply=False):
    async def send(text=None, image_url=None, caption=None):
        kwargs = {"reply_to_message_id": message.message_id} if reply else {}

        try:
            if image_url:
                await message.reply_photo(photo=image_url, caption=caption or "", **kwargs)
            else:
                await message.reply_text(text or "...", **kwargs)
        except Exception:
            fallback = f"Готово: {image_url}" + (("\n\n" + caption) if caption else "")
            await message.reply_text(fallback[:1000], **kwargs)

    bot = message.get_bot()
    await bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.TYPING)

    try:
        response = await chat_completion(messages)
        media_type, prompt = parse_media_cmd(response)

        if not media_type:
            await send(text=response)
            return

        status = await message.reply_text(
            "Генерирую фото..." if media_type == "image" else "Генерирую видео...",
            **({"reply_to_message_id": message.message_id} if reply else {})
        )

        await bot.send_chat_action(
            chat_id=message.chat_id,
            action=ChatAction.UPLOAD_PHOTO if media_type == "image" else ChatAction.UPLOAD_VIDEO
        )

        if media_type == "image":
            media_url = await generate_image(prompt)
        else:
            await send(text=f"Видео: {prompt}")
            await status.delete()
            return

        cap_prompt = "Напиши короткую подпись на русском"
        caption_resp = await chat_completion(messages + [
            {"role": "assistant", "content": response},
            {"role": "user", "content": cap_prompt},
        ])
        caption = caption_resp.replace("IMAGE:", "").replace("VIDEO:", "").strip()

        await send(image_url=media_url, caption=caption)
        await status.delete()

    except Exception as e:
        logger.error(f"Ошибка при обработке запроса: {e}", exc_info=True)
        await message.reply_text("Произошла ошибка. Попробуй позже.", reply_to_message_id=message.message_id if reply else None)


# ---------- Команды Telegram ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Напиши что-то.")


async def send_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        res = subprocess.run(["tail", "-n", "20", "bot.log"], capture_output=True, text=True)
        text = res.stdout or "пусто"
        await update.message.reply_text(f"```\n{text[-3000:]}\n```", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка чтения логов: {e}")


async def private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    await process_request(
        [{"role": "user", "content": build_user_content(msg)}],
        msg,
        reply=False
    )


async def group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    bot_uname = context.bot.username
    if not is_route_mentioned(msg.text, bot_uname):
        return
    await process_request(
        [{"role": "user", "content": build_user_content(msg)}],
        msg,
        reply=True
    )


async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        return
    if msg.reply_to_message.from_user.is_bot:
        await process_request(
            [{"role": "user", "content": build_user_content(msg)}],
            msg,
            reply=True
        )


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("logs", send_logs))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, private))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT, group))
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT, reply_handler))
    app.run_polling()


if __name__ == "__main__":
    main()

import os
import re
import json
import logging
import traceback
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

load_dotenv()

API_BASE = "https://gpt.crax.lol/v1"
API_KEY = os.getenv("CRAX_API_KEY")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

TEXT_MODEL = "gpt-5-6-luna"
IMAGE_MODEL = "qwen-image-2.0-pro"
VIDEO_MODEL = "seedance-2.0"


SYSTEM_PROMPT = """Тебя зовут Роут. Ты — дружелюбный ИИ-ассистент в Telegram.

Очень важно:
- Отвечай на русском, если не просят иначе.
- Если пользователь просит нарисовать, сгенерировать изображение, картинку, фото, арт — начни свой ответ с:
  IMAGE: <подробный англоязычный промпт для генерации>
- Если пользователь просит сделать видео, ролик, клип, анимацию — начни свой ответ с:
  VIDEO: <подробный англоязычный промпт для генерации>
- Если не просят генерировать медиа — просто отвечай текстом, без префиксов.

Примеры:
Пользователь: нарисуй слона
Ты: IMAGE: A majestic African elephant standing in savanna at golden hour, highly detailed, photorealistic, 8k

Пользователь: сделай видео про космос
Ты: VIDEO: A cinematic flight through a colorful nebula with distant stars and planets, realistic space visuals, 4k

Пользователь: привет
Ты: Привет! Чем могу помочь?"""


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
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{API_BASE}/chat/completions", headers=headers, json=payload
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(data.get("error", data))
            return data["choices"][0]["message"]["content"]


async def generate_image(prompt: str, aspect_ratio: str = "1:1") -> str:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": IMAGE_MODEL,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "n": 1,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{API_BASE}/images/generations", headers=headers, json=payload
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(data.get("error", data))
            return data["data"][0]["url"]


async def generate_video(prompt: str, aspect_ratio: str = "16:9", duration: int = 5) -> str:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": VIDEO_MODEL,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "mode": "t2v",
        "resolution": "720p",
        "duration": duration,
        "generate_audio": True,
        "stream": True,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{API_BASE}/videos/generations", headers=headers, json=payload
        ) as resp:
            if resp.status != 200:
                data = await resp.json()
                raise RuntimeError(data.get("error", data))

            async for line in resp.content:
                line = line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue

                if event.get("type") == "video":
                    return event["url"]

            raise RuntimeError("Video endpoint did not return a video URL")


# ---------- Упоминания в чатах ----------

def is_route_mentioned(text: str, bot_username: str) -> bool:
    if not text:
        return False
    lower_text = text.lower()
    if f"@{bot_username.lower()}" in lower_text:
        return True
    if re.search(r"(?<!\w)роут(?!\w)", lower_text):
        return True
    return False


def build_user_content(message) -> str:
    text = message.text or ""
    if message.reply_to_message and message.reply_to_message.text:
        return (
            f"Сообщение, на которое ответили:\n{message.reply_to_message.text}\n\n"
            f"Текущее сообщение:\n{text}"
        )
    return text


def parse_media_command(text: str):
    """Возвращает (type, prompt) или (None, text)"""
    text = text.strip()
    if text.upper().startswith("IMAGE:"):
        return "image", text[6:].strip()
    if text.upper().startswith("VIDEO:"):
        return "video", text[6:].strip()
    return None, text


# ---------- Обработка запроса ----------

async def process_request(messages: list, message, reply: bool):
    async def send(text=None, image_url=None, video_url=None, caption=None):
        caption = (caption or "")[:1000]
        kwargs = {}
        if reply:
            kwargs["reply_to_message_id"] = message.message_id

        try:
            if image_url:
                await message.reply_photo(photo=image_url, caption=caption, **kwargs)
            elif video_url:
                await message.reply_video(video=video_url, caption=caption, **kwargs)
            else:
                await message.reply_text(text or "...", **kwargs)
        except Exception as media_error:
            if image_url or video_url:
                url = image_url or video_url
                await message.reply_text(
                    f"Готово: {url}\n\n{caption or ''}".strip(), **kwargs
                )
            else:
                raise media_error

    bot = message.get_bot()

    # --- Этап 1: ИИ думает и отвечает ---
    await bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.TYPING)
    answer = await chat_completion(messages)

    media_type, content = parse_media_command(answer)

    # --- Обычный текст ---
    if not media_type:
        await send(text=answer)
        return

    # --- Генерация медиа ---
    if media_type == "image":
        action = ChatAction.UPLOAD_PHOTO
        status_text = "Генерирую фото..."
    else:
        action = ChatAction.UPLOAD_VIDEO
        status_text = "Генерирую видео..."

    status_msg = await message.reply_text(
        status_text,
        reply_to_message_id=message.message_id if reply else None
    )

    await bot.send_chat_action(chat_id=message.chat_id, action=action)

    try:
        if media_type == "image":
            media_url = await generate_image(content)
        else:
            media_url = await generate_video(content)
    except Exception as e:
        await status_msg.edit_text(f"Ошибка генерации: {e}")
        return

    # --- Этап 2: ИИ пишет подпись ---
    await bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.TYPING)

    caption_messages = messages + [
        {"role": "assistant", "content": answer},
        {
            "role": "user",
            "content": (
                f"Медиа сгенерировано по запросу: {content}\n"
                f"URL: {media_url}\n"
                f"Напиши короткую подпись к этому медиа на русском языке. "
                f"Не повторяй технические детали, просто естественный комментарий."
            ),
        },
    ]
    caption = await chat_completion(caption_messages)
    caption = caption.replace("IMAGE:", "").replace("VIDEO:", "").strip()

    # --- Отправляем результат ---
    if media_type == "image":
        await send(image_url=media_url, caption=caption)
    else:
        await send(video_url=media_url, caption=caption)

    # Удаляем статусное сообщение
    try:
        await status_msg.delete()
    except Exception:
        pass


# ---------- Хендлеры ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я Роут. Напиши мне что-нибудь.")


async def handle_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    await process_request(
        [{"role": "user", "content": build_user_content(message)}],
        message,
        reply=False,
    )


async def handle_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    bot_username = context.bot.username
    if not is_route_mentioned(message.text, bot_username):
        return

    await process_request(
        [{"role": "user", "content": build_user_content(message)}],
        message,
        reply=True,
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    traceback.print_exc()
    logging.error(f"Update {update} caused error: {context.error}", exc_info=context.error)
    if isinstance(update, Update) and update.message:
        try:
            await update.message.reply_text("Произошла ошибка. Попробуй позже.")
        except Exception:
            pass


# ---------- Запуск ----------

def main():
    if not BOT_TOKEN or not API_KEY:
        raise ValueError("Задай TELEGRAM_BOT_TOKEN и CRAX_API_KEY в .env")

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, handle_private)
    )
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.TEXT, handle_group)
    )
    application.add_error_handler(error_handler)

    application.run_polling()


if __name__ == "__main__":
    main()

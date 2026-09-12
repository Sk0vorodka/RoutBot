import os
import re
import json
import logging
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

Правила:
- Отвечай на русском, если не просят иначе.
- Если пользователь просит нарисовать изображение, картинку, фото, арт — вызови функцию generate_image.
- Если пользователь просит сделать видео, ролик, клип, анимацию — вызови функцию generate_video.
- Для генерации медиа составляй максимально подробный англоязычный промпт.
- Если не просят генерировать медиа — отвечай обычным текстом."""


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "Generate an image from a detailed text prompt. Use when the user asks for a picture, image, photo, drawing, or art.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Detailed English prompt for image generation",
                    },
                    "aspect_ratio": {
                        "type": "string",
                        "enum": ["1:1", "3:4", "4:3", "16:9", "9:16"],
                        "default": "1:1",
                        "description": "Aspect ratio of the generated image",
                    },
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_video",
            "description": "Generate a video from a detailed text prompt. Use when the user asks for a video, clip, animation, or movie.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Detailed English prompt for video generation",
                    },
                    "aspect_ratio": {
                        "type": "string",
                        "enum": ["1:1", "3:4", "4:3", "16:9", "9:16"],
                        "default": "16:9",
                        "description": "Aspect ratio of the generated video",
                    },
                    "duration": {
                        "type": "integer",
                        "enum": [5, 10],
                        "default": 5,
                        "description": "Duration of the video in seconds",
                    },
                },
                "required": ["prompt"],
            },
        },
    },
]


# ---------- API клиенты ----------

async def chat_completion(messages, tools=None, tool_choice="none"):
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": TEXT_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        "temperature": 0.7,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{API_BASE}/chat/completions", headers=headers, json=payload
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(data.get("error", data))
            return data


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

    response = await chat_completion(messages, tools=TOOLS, tool_choice="auto")
    assistant_message = response["choices"][0]["message"]

    if not assistant_message.get("tool_calls"):
        await send(text=assistant_message.get("content", "..."))
        return

    tool_call = assistant_message["tool_calls"][0]
    function_name = tool_call["function"]["name"]
    arguments = json.loads(tool_call["function"]["arguments"])

    if function_name == "generate_image":
        await message.reply_text(
            "Рисую...",
            reply_to_message_id=message.message_id if reply else None
        )
        media_url = await generate_image(**arguments)
        media_type = "image"
    elif function_name == "generate_video":
        await message.reply_text(
            "Делаю видео, это может занять время...",
            reply_to_message_id=message.message_id if reply else None
        )
        media_url = await generate_video(**arguments)
        media_type = "video"
    else:
        await send(text="Неизвестная функция")
        return

    messages.append(assistant_message)
    messages.append(
        {
            "role": "tool",
            "tool_call_id": tool_call["id"],
            "name": function_name,
            "content": json.dumps(
                {"url": media_url, "prompt": arguments.get("prompt", "")}
            ),
        }
    )
    final_response = await chat_completion(messages)
    caption = final_response["choices"][0]["message"].get("content", "")

    if media_type == "image":
        await send(image_url=media_url, caption=caption)
    else:
        await send(video_url=media_url, caption=caption)


# ---------- Хендлеры ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я Роут. Напиши мне что-нибудь.")


async def handle_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    async with context.bot.send_chat_action(
        chat_id=message.chat_id, action=ChatAction.TYPING
    ):
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

    async with context.bot.send_chat_action(
        chat_id=message.chat_id, action=ChatAction.TYPING
    ):
        await process_request(
            [{"role": "user", "content": build_user_content(message)}],
            message,
            reply=True,
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
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

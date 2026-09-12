# bot.py
import os
import re
import json
import logging
import traceback
import tempfile
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

# Настройка логирования в файл и в консоль
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

# Проверка наличия необходимых переменных окружения
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
    logger.info(f"Отправка запроса к {API_BASE}/chat/completions с моделью {TEXT_MODEL}")
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": TEXT_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        "temperature": 0.7,
        "stream": False,  # !! Явно отключаем стрим
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE}/chat/completions", headers=headers, json=payload
            ) as resp:
                # ВАЖНО: Проверяем Content-Type перед чтением
                content_type = resp.headers.get("Content-Type", "")
                if "text/event-stream" in content_type:
                    # Если стрим — соберём текст из потока
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
                    logger.info(f"Собранный ответ из потока: {full_response[:100]}...")
                    return full_response
                else:
                    # Если обычный JSON
                    data = await resp.json()
                    logger.info(f"Статус ответа: {resp.status}")
                    if resp.status != 200:
                        logger.error(f"Ошибка API: {data}")
                        raise RuntimeError(data.get("error", data))
                    result = data["choices"][0]["message"]["content"]
                    logger.info(f"Получен ответ от модели: {result[:100]}...")
                    return result
    except Exception as e:
        logger.error(f"Ошибка при выполнении запроса к API: {e}")
        raise


async def generate_image(prompt: str, aspect_ratio: str = "1:1") -> str:
    logger.info(f"Генерация изображения с промптом: {prompt}")
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
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE}/images/generations", headers=headers, json=payload
            ) as resp:
                data = await resp.json()
                logger.info(f"Статус генерации изображения: {resp.status}")
                if resp.status != 200:
                    logger.error(f"Ошибка генерации изображения: {data}")
                    raise RuntimeError(data.get("error", data))
                url = data["data"][0]["url"]
                logger.info(f"Изображение сгенерировано: {url}")
                return url
    except Exception as e:
        logger.error(f"Ошибка при генерации изображения: {e}")
        raise


async def generate_video(prompt: str, aspect_ratio: str = "16:9", duration: int = 5) -> str:
    logger.info(f"Генерация видео с промптом: {prompt}")
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
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE}/videos/generations", headers=headers, json=payload
            ) as resp:
                logger.info(f"Статус начала генерации видео: {resp.status}")
                if resp.status != 200:
                    data = await resp.json()
                    logger.error(f"Ошибка начала генерации видео: {data}")
                    raise RuntimeError(data.get("error", data))

                async for line in resp.content:
                    line = line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                        logger.info(f"Получено событие видео: {event}")
                    except json.JSONDecodeError:
                        continue

                    if event.get("type") == "video":
                        url = event["url"]
                        logger.info(f"Видео сгенерировано: {url}")
                        return url

                raise RuntimeError("Видео endpoint не вернул URL видео")
    except Exception as e:
        logger.error(f"Ошибка при генерации видео: {e}")
        raise


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
    logger.info("Начало обработки запроса")
    
    async def send(text=None, image_url=None, video_url=None, caption=None):
    caption = (caption or "")[:1000]
    kwargs = {}
    if reply:
        kwargs["reply_to_message_id"] = message.message_id

    try:
        if image_url:
            logger.info("Скачивание изображения для отправки")
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as resp:
                    if resp.status == 200:
                        # Сохраняем во временный файл
                        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                        temp_file.write(await resp.read())
                        temp_file.close()
                        
                        # Отправляем файл
                        await message.reply_photo(photo=open(temp_file.name, 'rb'), caption=caption, **kwargs)

                        # Удаляем временный файл (опционально)
                        os.unlink(temp_file.name)
                    else:
                        raise Exception(f"Ошибка загрузки изображения: {resp.status}")

        elif video_url:
            # Для видео аналогично, если нужно
            ...

        else:
            await message.reply_text(text or "...", **kwargs)

    except Exception as media_error:
        logger.error(f"Ошибка отправки в Telegram: {media_error}")
        if image_url or video_url:
            url = image_url or video_url
            await message.reply_text(
                f"Готово: {url}\n\n{caption or ''}".strip(), **kwargs
            )
        else:
            raise media_error

    bot = message.get_bot()

    # --- Этап 1: ИИ думает и отвечает ---
    logger.info("Отправка действия typing в Telegram")
    await bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.TYPING)
    
    try:
        answer = await chat_completion(messages)
        logger.info(f"Ответ от модели: {answer[:100]}...")
    except Exception as e:
        logger.error(f"Ошибка при получении ответа от модели: {e}")
        await message.reply_text("Произошла ошибка при обращении к ИИ. Попробуй позже.")
        return

    media_type, content = parse_media_command(answer)
    logger.info(f"Разбор ответа: тип={media_type}, контент={content[:50]}...")

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

    logger.info(f"Отправка статусного сообщения: {status_text}")
    status_msg = await message.reply_text(
        status_text,
        reply_to_message_id=message.message_id if reply else None
    )

    logger.info(f"Отправка действия upload в Telegram: {action}")
    await bot.send_chat_action(chat_id=message.chat_id, action=action)

    try:
        if media_type == "image":
            media_url = await generate_image(content)
        else:
            media_url = await generate_video(content)
    except Exception as e:
        logger.error(f"Ошибка генерации медиа: {e}")
        await status_msg.edit_text(f"Ошибка генерации: {str(e)[:200]}")
        return

    # --- Этап 2: ИИ пишет подпись ---
    logger.info("Генерация подписи к медиа")
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
    
    try:
        caption = await chat_completion(caption_messages)
        caption = caption.replace("IMAGE:", "").replace("VIDEO:", "").strip()
        logger.info(f"Подпись сгенерирована: {caption[:50]}...")
    except Exception as e:
        logger.error(f"Ошибка генерации подписи: {e}")
        caption = ""

    # --- Отправляем результат ---
    logger.info("Отправка финального результата")
    if media_type == "image":
        await send(image_url=media_url, caption=caption)
    else:
        await send(video_url=media_url, caption=caption)

    # Удаляем статусное сообщение
    try:
        await status_msg.delete()
        logger.info("Статусное сообщение удалено")
    except Exception as e:
        logger.warning(f"Не удалось удалить статусное сообщение: {e}")


# ---------- Хендлеры ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Команда /start получена")
    await update.message.reply_text("Привет! Я Роут. Напиши мне что-нибудь.")


async def handle_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Получено сообщение в приватном чате")
    message = update.message
    if not message or not message.text:
        logger.info("Сообщение не содержит текста")
        return

    await process_request(
        [{"role": "user", "content": build_user_content(message)}],
        message,
        reply=False,
    )


async def handle_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Получено сообщение в группе")
    message = update.message
    if not message or not message.text:
        logger.info("Сообщение не содержит текста")
        return

    bot_username = context.bot.username
    if not is_route_mentioned(message.text, bot_username):
        logger.info("Бот не упомянут в сообщении")
        return

    await process_request(
        [{"role": "user", "content": build_user_content(message)}],
        message,
        reply=True,
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Произошла ошибка при обработке update", exc_info=context.error)
    # Печатаем traceback для дополнительной информации
    traceback.print_exc()
    
    if isinstance(update, Update) and update.message:
        try:
            await update.message.reply_text("Произошла ошибка. Попробуй позже.")
        except Exception:
            pass


# ---------- Запуск ----------

def main():
    logger.info("Запуск бота")
    
    if not BOT_TOKEN or not API_KEY:
        logger.error("Не заданы необходимые переменные окружения")
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

    logger.info("Бот готов принимать сообщения")
    application.run_polling()


if __name__ == "__main__":
    main()

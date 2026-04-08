import Pillow
import os
import asyncio
import logging
import base64
import time
from datetime import datetime
from typing import Any, Dict

from PIL import Image
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

import aiohttp

# --- НАСТРОЙКИ ЛОГИРОВАНИЯ ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[
        logging.FileHandler("bot_log.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- КОНФИГУРАЦИЯ ---
TOKEN = os.getenv("BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")
MODEL_NAME = "llama-3.2-11b-vision-preview"

if not TOKEN or not GROQ_KEY:
    exit("Ошибка: Переменные окружения BOT_TOKEN или GROQ_API_KEY не заданы!")

# --- СОСТОЯНИЯ FSM ---
class AnalyzerStates(StatesGroup):
    waiting_for_photo = State()
    waiting_for_pair = State()
    waiting_for_tf = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
async def encode_image_to_base64(path: str) -> str:
    """Кодирует локальный файл изображения в base64."""
    with open(path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

async def ask_groq_vision(image_base64: str, pair: str, tf: str) -> str:
    """Отправляет запрос в Groq API (Llama 3 Vision)."""
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_KEY}",
        "Content-Type": "application/json"
    }
    
    prompt = (
        f"Ты — эксперт технического анализа криптовалют. Пара: {pair}, Таймфрейм: {tf}. "
        "Проанализируй скриншот. Определи уровни поддержки и сопротивления, текущий паттерн "
        "и дай рекомендацию: ПОКУПАТЬ, ПРОДАВАТЬ или ЖДАТЬ. "
        "Укажи Take Profit и Stop Loss. Отвечай только на русском языке."
    )

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
                    }
                ]
            }
        ],
        "temperature": 0.2,
        "max_tokens": 1024
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status != 200:
                err_text = await resp.text()
                logger.error(f"Groq API Error: {err_text}")
                return f"Ошибка API: {resp.status}"
            
            result = await resp.json()
            return result['choices'][0]['message']['content']

# --- ОБРАБОТЧИКИ КОМАНД ---
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Приветствие и сброс состояния."""
    await state.clear()
    user_name = message.from_user.first_name
    await message.answer(
        f"👋 Привет, {user_name}!\n\n"
        "Я ИИ-аналитик для Bybit. Чтобы начать анализ:\n"
        "1️⃣ Пришли скриншот графика\n"
        "2️⃣ Выбери торговую пару\n"
        "3️⃣ Выбери таймфрейм\n\n"
        "Жду твой скриншот! 👇"
    )
    await state.set_state(AnalyzerStates.waiting_for_photo)

@dp.message(StateFilter(AnalyzerStates.waiting_for_photo), F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    """Получение фото и переход к выбору пары."""
    photo_id = message.photo[-1].file_id
    await state.update_data(photo_id=photo_id)

    # Динамическая клавиатура
    builder = InlineKeyboardBuilder()
    pairs = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "TON/USDT", "XRP/USDT", "DOGE/USDT"]
    for p in pairs:
        builder.button(text=p, callback_data=f"pair:{p}")
    builder.adjust(2)

    await message.answer("📊 Отлично! Теперь выбери торговую пару:", reply_markup=builder.as_markup())
    await state.set_state(AnalyzerStates.waiting_for_pair)

@dp.callback_query(F.data.startswith("pair:"), StateFilter(AnalyzerStates.waiting_for_pair))
async def process_pair(callback: types.CallbackQuery, state: FSMContext):
    """Сохранение пары и переход к таймфрейму."""
    pair = callback.data.split(":")[1]
    await state.update_data(chosen_pair=pair)

    builder = InlineKeyboardBuilder()
    timeframes = ["1m", "5m", "15m", "1h", "4h", "1D"]
    for tf in timeframes:
        builder.button(text=tf, callback_data=f"tf:{tf}")
    builder.adjust(3)

    await callback.message.edit_text(f"Пара: {pair}\n⏱ Выбери таймфрейм со скриншота:", reply_markup=builder.as_markup())
    await state.set_state(AnalyzerStates.waiting_for_tf)

@dp.callback_query(F.data.startswith("tf:"), StateFilter(AnalyzerStates.waiting_for_tf))
async def process_analysis(callback: types.CallbackQuery, state: FSMContext):
    """Финальный этап: скачивание фото и запрос к ИИ."""
    tf = callback.data.split(":")[1]
    data = await state.get_data()
    pair = data['chosen_pair']
    photo_id = data['photo_id']

    await callback.answer()
    status_msg = await callback.message.answer("⚙️ *Запускаю нейросеть Llama 3 Vision...*", parse_mode="Markdown")

    # Путь для временного файла
    temp_path = f"tmp_{callback.from_user.id}_{int(time.time())}.jpg"

    try:
        # Скачивание
        file = await bot.get_file(photo_id)
        await bot.download_file(file.file_path, temp_path)
        
        # Подготовка данных
        b64_image = await encode_image_to_base64(temp_path)
        
        # Запрос к ИИ
        analysis_result = await ask_groq_vision(b64_image, pair, tf)

        # Отправка результата
        await callback.message.answer(
            f"📈 **Результат анализа {pair} ({tf})**\n\n{analysis_result}",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.exception("Ошибка при обработке анализа")
        await callback.message.answer("❌ Произошла ошибка при обращении к ИИ. Проверь лимиты API Groq.")
    
    finally:
        # Чистка
        if os.path.exists(temp_path):
            os.remove(temp_path)
        await status_msg.delete()
        await state.clear()
        await callback.message.answer("Бот готов к новому скриншоту. Нажми /start или просто пришли фото.")
        await state.set_state(AnalyzerStates.waiting_photo)

@dp.message()
async def global_echo(message: types.Message):
    """Заглушка для любых других сообщений."""
    if not message.photo:
        await message.answer("Чтобы начать, просто пришли мне **скриншот графика** 🖼")

# --- ЗАПУСК ---
async def main():
    logger.info("Бот запускается...")
    # Удаляем вебхук и все накопившиеся сообщения
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен!")

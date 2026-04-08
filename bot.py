import os
import asyncio
import logging
import base64
import time
import io
from datetime import datetime
from typing import Any, Dict

from PIL import Image
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

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
    """Оптимизирует, сжимает и кодирует изображение в base64 для Groq."""
    try:
        with Image.open(path) as img:
            # Конвертация в RGB (убираем прозрачность PNG)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            
            # Ограничиваем размер (Llama Vision не любит огромные файлы)
            img.thumbnail((1024, 1024))
            
            buffer = io.BytesIO()
            # Сохраняем с качеством 70% для экономии лимитов API
            img.save(buffer, format="JPEG", quality=70, optimize=True)
            return base64.b64encode(buffer.getvalue()).decode('utf-8')
    except Exception as e:
        logger.error(f"Ошибка при обработке изображения: {e}")
        return ""

async def ask_groq_vision(image_base64: str, pair: str, tf: str) -> str:
    """Отправляет запрос в Groq API (Llama 3 Vision)."""
    if not image_base64:
        return "Ошибка: Не удалось подготовить изображение."

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_KEY}",
        "Content-Type": "application/json"
    }
    
    prompt = (
        f"Ты — эксперт-трейдер Bybit. Проанализируй этот график {pair} ({tf}). "
        "1. Какая фаза рынка? 2. Дай сигнал: BUY, SELL или WAIT. "
        "3. Укажи уровни Take Profit и Stop Loss. Отвечай кратко на русском."
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
        "temperature": 0.1, # Низкая температура для точности
        "max_tokens": 800
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, headers=headers, json=payload, timeout=30) as resp:
                if resp.status != 200:
                    err_data = await resp.json()
                    logger.error(f"Groq Error {resp.status}: {err_data}")
                    return f"Ошибка API ({resp.status}): {err_data.get('error', {}).get('message', 'Unknown error')}"
                
                result = await resp.json()
                return result['choices'][0]['message']['content']
        except Exception as e:
            return f"Ошибка сети: {str(e)}"

# --- ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- ОБРАБОТЧИКИ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n"
        "Я ИИ-аналитик. Пришли **скриншот графика** для анализа."
    )
    await state.set_state(AnalyzerStates.waiting_for_photo)

@dp.message(StateFilter(AnalyzerStates.waiting_for_photo), F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(photo_id=photo_id)

    builder = InlineKeyboardBuilder()
    pairs = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "TON/USDT", "XRP/USDT"]
    for p in pairs:
        builder.button(text=p, callback_data=f"pair:{p}")
    builder.adjust(2)

    await message.answer("✅ Фото получено. Выбери пару:", reply_markup=builder.as_markup())
    await state.set_state(AnalyzerStates.waiting_for_pair)

@dp.callback_query(F.data.startswith("pair:"), StateFilter(AnalyzerStates.waiting_for_pair))
async def process_pair(callback: types.CallbackQuery, state: FSMContext):
    pair = callback.data.split(":")[1]
    await state.update_data(chosen_pair=pair)

    builder = InlineKeyboardBuilder()
    for tf in ["5m", "15m", "1h", "4h", "1D"]:
        builder.button(text=tf, callback_data=f"tf:{tf}")
    builder.adjust(3)

    await callback.message.edit_text(f"Пара: {pair}\nВыбери таймфрейм:", reply_markup=builder.as_markup())
    await state.set_state(AnalyzerStates.waiting_for_tf)

@dp.callback_query(F.data.startswith("tf:"), StateFilter(AnalyzerStates.waiting_for_tf))
async def process_analysis(callback: types.CallbackQuery, state: FSMContext):
    tf = callback.data.split(":")[1]
    data = await state.get_data()
    
    await callback.answer()
    status_msg = await callback.message.answer("📡 *Анализирую данные нейросетью...*", parse_mode="Markdown")

    temp_path = f"img_{callback.from_user.id}_{int(time.time())}.jpg"

    try:
        file = await bot.get_file(data['photo_id'])
        await bot.download_file(file.file_path, temp_path)
        
        # Сжатие и кодирование (фиксит ошибку 400)
        b64_image = await encode_image_to_base64(temp_path)
        
        analysis = await ask_groq_vision(b64_image, data['chosen_pair'], tf)

        await callback.message.answer(
            f"📈 **Результат ({data['chosen_pair']} | {tf}):**\n\n{analysis}",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.exception("Final processing error")
        await callback.message.answer(f"❌ Произошла ошибка: {str(e)}")
    
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        await status_msg.delete()
        await state.clear()
        await state.set_state(AnalyzerStates.waiting_for_photo)

@dp.message()
async def global_echo(message: types.Message):
    if not message.photo:
        await message.answer("Пришли скриншот графика (фото), чтобы я начал работу.")

async def main():
    logger.info("Starting bot...")
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Stopped")

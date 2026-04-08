import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command  # Импортируем фильтр команд явно
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from google import genai
from google.genai import types as genai_types

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация Gemini
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Инициализация бота
bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()

class TradeStates(StatesGroup):
    waiting_photo = State()
    waiting_pair = State()
    waiting_tf = State()

# 1. Хендлер на команду /start (изменен фильтр)
@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    logging.info(f"Command /start from {message.from_user.id}")
    await state.clear()
    await message.answer("✅ Бот запущен!\nОтправь мне **скриншот графика** как обычное фото.")
    await state.set_state(TradeStates.waiting_photo)

# 2. Обработка фото (проверяем, что состояние совпадает)
@dp.message(TradeStates.waiting_photo, F.photo)
async def handle_photo(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(photo_id=photo_id)
    
    kb = InlineKeyboardBuilder()
    pairs = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "TON/USDT", "XRP/USDT"]
    for pair in pairs:
        kb.button(text=pair, callback_data=f"p_{pair}")
    kb.adjust(2)
    
    await message.answer("📊 Какая торговая пара?", reply_markup=kb.as_markup())
    await state.set_state(TradeStates.waiting_pair)

# 3. Выбор пары
@dp.callback_query(F.data.startswith("p_"))
async def handle_pair(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(pair=call.data[2:])
    kb = InlineKeyboardBuilder()
    for tf in ["1m", "5m", "15m", "1h", "4h", "1D"]:
        kb.button(text=tf, callback_data=f"tf_{tf}")
    kb.adjust(3)
    await call.message.edit_text("⏱ Какой таймфрейм на скрине?", reply_markup=kb.as_markup())
    await state.set_state(TradeStates.waiting_tf)

# 4. Анализ ИИ
@dp.callback_query(F.data.startswith("tf_"))
async def handle_analysis(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tf = call.data[3:]
    await call.answer()
    
    msg = await call.message.answer("🔍 ИИ анализирует... Подожди.")
    file_path = f"chart_{call.from_user.id}.jpg"
    
    try:
        file = await bot.get_file(data['photo_id'])
        await bot.download_file(file.file_path, file_path)

        with open(file_path, "rb") as f:
            image_bytes = f.read()

        prompt = (
            f"Ты профи трейдер Bybit. Проанализируй {data['pair']} на таймфрейме {tf}. "
            "Дай сигнал (BUY/SELL/WAIT) и уровни TP/SL на русском языке."
        )

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[prompt, genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")]
        )
        
        await call.message.answer(f"📊 **Анализ:**\n\n{response.text}", parse_mode="Markdown")
        await msg.delete()
    except Exception as e:
        logging.error(e)
        await call.message.answer("❌ Ошибка при анализе.")
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        await state.clear()

# 5. ЭХО-ОТВЕТ (Если бот не понял команду - он ответит этим)
@dp.message()
async def echo_all(message: types.Message):
    await message.answer("🤖 Я тебя вижу! Но чтобы начать анализ, напиши /start")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

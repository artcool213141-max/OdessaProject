import os
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
import google.generativeai as genai

# Инициализация ИИ (ключ возьмется из секретов GitHub)
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-1.5-flash')

# Инициализация бота
bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()

class TradeStates(StatesGroup):
    waiting_photo = State()
    waiting_pair = State()
    waiting_tf = State()

@dp.message(F.command("start"))
async def start(message: types.Message, state: FSMContext):
    await message.answer("👋 Привет! Я твой ИИ-помощник для Bybit.\nПришли мне скриншот графика.")
    await state.set_state(TradeStates.waiting_photo)

@dp.message(TradeStates.waiting_photo, F.photo)
async def handle_photo(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(photo_id=photo_id)
    
    kb = InlineKeyboardBuilder()
    pairs = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "TON/USDT", "XRP/USDT"]
    for pair in pairs:
        kb.button(text=pair, callback_data=f"p_{pair}")
    kb.adjust(2)
    await message.answer("Какую пару анализируем?", reply_markup=kb.as_markup())
    await state.set_state(TradeStates.waiting_pair)

@dp.callback_query(F.data.startswith("p_"))
async def handle_pair(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(pair=call.data[2:])
    kb = InlineKeyboardBuilder()
    tfs = ["1m", "5m", "15m", "1h", "4h", "1D"]
    for tf in tfs:
        kb.button(text=tf, callback_data=f"tf_{tf}")
    kb.adjust(3)
    await call.message.edit_text("Выбери таймфрейм со скриншота:", reply_markup=kb.as_markup())
    await state.set_state(TradeStates.waiting_tf)

@dp.callback_query(F.data.startswith("tf_"))
async def handle_analysis(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tf = call.data[3:]
    await call.message.edit_text("🔍 ИИ изучает график... Это займет пару секунд.")

    # Скачиваем фото во временный файл
    file = await bot.get_file(data['photo_id'])
    file_path = f"chart_{call.from_user.id}.jpg"
    await bot.download_file(file.file_path, file_path)

    try:
        # Загружаем в Gemini
        uploaded_file = genai.upload_file(path=file_path)
        
        prompt = (
            f"Ты эксперт-трейдер Bybit. Проанализируй этот график пары {data['pair']} на таймфрейме {tf}. "
            "1. Какая сейчас фаза рынка? "
            "2. Дай четкий сигнал: КУПИТЬ, ПРОДАТЬ или ЖДАТЬ. "
            "3. Укажи ориентировочные уровни Take Profit и Stop Loss. "
            "Отвечай кратко и по делу на русском языке."
        )

        response = model.generate_content([prompt, uploaded_file])
        
        await call.message.answer(f"📊 **Анализ {data['pair']} ({tf}):**\n\n{response.text}", parse_mode="Markdown")
        
        # Удаляем файл после анализа
        os.remove(file_path)
    except Exception as e:
        await call.message.answer(f"❌ Ошибка анализа: {str(e)}")
    
    await state.clear()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

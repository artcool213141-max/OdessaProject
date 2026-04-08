import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F, types
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from google import genai
from google.genai import types as genai_types

# Логирование для отслеживания ошибок в GitHub Actions
logging.basicConfig(level=logging.INFO)

# Инициализация ИИ (используем новую библиотеку)
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Инициализация бота
bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()

class TradeStates(StatesGroup):
    waiting_photo = State()
    waiting_pair = State()
    waiting_tf = State()

@dp.message(F.command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("👋 Привет! Я твой ИИ-аналитик для Bybit.\n\n"
                         "Пришли мне **скриншот графика**, и я подскажу, что делать.")
    await state.set_state(TradeStates.waiting_photo)

@dp.message(TradeStates.waiting_photo, F.photo)
async def handle_photo(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(photo_id=photo_id)
    
    kb = InlineKeyboardBuilder()
    pairs = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "TON/USDT", "XRP/USDT", "DOGE/USDT"]
    for pair in pairs:
        kb.button(text=pair, callback_data=f"p_{pair}")
    kb.adjust(2)
    
    await message.answer("📊 Какая это торговая пара?", reply_markup=kb.as_markup())
    await state.set_state(TradeStates.waiting_pair)

@dp.callback_query(F.data.startswith("p_"))
async def handle_pair(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(pair=call.data[2:])
    
    kb = InlineKeyboardBuilder()
    tfs = ["1m", "5m", "15m", "1h", "4h", "1D"]
    for tf in tfs:
        kb.button(text=tf, callback_data=f"tf_{tf}")
    kb.adjust(3)
    
    await call.message.edit_text("⏱ Какой таймфрейм на скрине?", reply_markup=kb.as_markup())
    await state.set_state(TradeStates.waiting_tf)

@dp.callback_query(F.data.startswith("tf_"))
async def handle_analysis(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tf = call.data[3:]
    
    msg = await call.message.edit_text("🔍 *ИИ анализирует график...*", parse_mode="Markdown")

    # Путь для временного сохранения фото
    file_path = f"chart_{call.from_user.id}.jpg"
    
    try:
        # Загрузка фото из телеграм
        file = await bot.get_file(data['photo_id'])
        await bot.download_file(file.file_path, file_path)

        # Чтение файла для отправки в Gemini
        with open(file_path, "rb") as f:
            image_bytes = f.read()

        prompt = (
            f"Ты профессиональный крипто-трейдер. Проанализируй график {data['pair']} на таймфрейме {tf}. "
            "Дай конкретный ответ на русском языке:\n"
            "1. Текущий тренд и фаза.\n"
            "2. Сигнал (BUY, SELL или WAIT).\n"
            "3. Уровни входа, Take Profit и Stop Loss.\n"
            "Пиши кратко, по делу, используя Markdown."
        )

        # Запрос к Gemini 2.0 (самая быстрая и умная на сегодня)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                prompt,
                genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
            ]
        )
        
        await call.message.answer(
            f"✅ **Анализ завершен!**\n\n{response.text}", 
            parse_mode="Markdown"
        )
        await msg.delete()

    except Exception as e:
        logging.error(f"Error during analysis: {e}")
        await call.message.answer(f"❌ Произошла ошибка при анализе. Попробуй позже.")
    
    finally:
        # Всегда удаляем файл и очищаем состояние
        if os.path.exists(file_path):
            os.remove(file_path)
        await state.clear()

async def main():
    # Удаляем старые сообщения, которые пришли пока бот был офлайн (чтобы не спамил)
    await bot.delete_webhook(drop_pending_updates=True)
    # Запуск
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped")

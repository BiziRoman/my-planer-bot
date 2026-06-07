import os
import asyncio
from contextlib import asynccontextmanager
from dotenv import load_dotenv

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram import F # Магический фильтр для ловли текста кнопок
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Импорты из наших файлов
from database import async_session, User, Task, Base, engine
from sqlalchemy import select, func
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
templates = Jinja2Templates(directory="templates")


# ==========================================
# 1. ЛОГИКА БОТА (из main.py)
# ==========================================

async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# Фоновая задача: проверка напоминаний
async def check_reminders():
    now_utc = datetime.now(ZoneInfo("UTC"))
    async with async_session() as session:
        query = select(Task).where(Task.due_time <= now_utc, Task.is_completed == False)
        result = await session.execute(query)
        tasks_to_send = result.scalars().all()

        for task in tasks_to_send:
            await bot.send_message(task.user_id, f"⏰ **НАПОМИНАНИЕ!**\n\nПора: {task.text}")
            task.is_completed = True

        if tasks_to_send:
            await session.commit()

# Создаем кнопки
btn_add = KeyboardButton(text="➕ Добавить задачу")
btn_tasks = KeyboardButton(text="📋 Мои задачи")
btn_settings = KeyboardButton(text="⚙️ Настройки")

# Собираем клавиатуру
main_menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [btn_add, btn_tasks], # Первый ряд кнопок
        [btn_settings]        # Второй ряд кнопок
    ],
    resize_keyboard=True # Важно! Чтобы кнопки не занимали пол-экрана телефона
)

# Функция для создания Inline-клавиатуры с часовыми поясами
def get_timezone_kb():
    kb = [
        [InlineKeyboardButton(text="🇷🇺 Москва (UTC+3)", callback_data="tz_Europe/Moscow")],
        [InlineKeyboardButton(text="🇷🇺 Екатеринбург (UTC+5)", callback_data="tz_Asia/Yekaterinburg")],
        [InlineKeyboardButton(text="🇷🇺 Новосибирск (UTC+7)", callback_data="tz_Asia/Novosibirsk")],
        [InlineKeyboardButton(text="🇷🇺 Владивосток (UTC+10)", callback_data="tz_Asia/Vladivostok")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# Класс состояний (FSM) для добавления задачи
class AddTaskForm(StatesGroup):
    waiting_for_task_text = State()

# --- Обработчики бота ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    async with async_session() as session:
        query = select(User).where(User.id == message.from_user.id)
        result = await session.execute(query)
        user = result.scalar_one_or_none()

        if user is None:
            new_user = User(id=message.from_user.id, username=message.from_user.username)
            session.add(new_user)
            await session.commit()
            await message.answer("Привет! Ты новый пользователь. Я записал тебя в свою базу!",
                                 reply_markup=main_menu_kb)
        else:
            await message.answer("С возвращением! Ты уже есть в моей базе.", reply_markup=main_menu_kb)


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "🤖 Я твой планировщик! Вот что я умею:\n\n"
        "/start - Запустить бота\n"
        "/help - Показать справку\n"
        "/add <текст> - Добавить задачу\n"
        "/tasks - Показать список задач\n"
        "/done <id> - Отметить задачу выполненной\n"
        "/settimezone <пояс> - Установить пояс (пример: /settimezone Europe/Moscow)\n"
        "/remind HH:MM <текст> - Поставить напоминание"
    )


@dp.message(Command("add"))
async def cmd_add(message: types.Message):
    task_text = message.text.replace("/add", "").strip()
    if not task_text:
        await message.answer("Пожалуйста, напиши текст задачи после команды.\nПример: /add Купить молоко")
        return
    async with async_session() as session:
        new_task = Task(user_id=message.from_user.id, text=task_text)
        session.add(new_task)
        await session.commit()
        await message.answer(f"✅ Задача '{task_text}' успешно сохранена!")


# Обработчик нажатия кнопки "Мои задачи"
@dp.message(F.text == "📋 Мои задачи")
async def show_tasks_button(message: types.Message):
    await cmd_tasks(message)

# Обработчик нажатия кнопки "Добавить задачу"
@dp.message(F.text == "➕ Добавить задачу")
async def add_task_button(message: types.Message, state: FSMContext):
    # Переводим бота в состояние ожидания текста
    await state.set_state(AddTaskForm.waiting_for_task_text)
    await message.answer("Хорошо, напиши текст задачи, которую хочешь добавить:")


# Ловим текст, когда бот ждет добавления задачи
@dp.message(AddTaskForm.waiting_for_task_text)
async def process_task_text(message: types.Message, state: FSMContext):
    task_text = message.text

    # Сохраняем в базу (логика как в команде /add)
    async with async_session() as session:
        new_task = Task(user_id=message.from_user.id, text=task_text)
        session.add(new_task)
        await session.commit()

    await message.answer(f"✅ Задача '{task_text}' успешно сохранена!")

    # ВАЖНО: Очищаем состояние, чтобы бот вернулся в обычный режим
    await state.clear()

# Оставляем и текстовую команду на случай, если кто-то привык писать руками
# Вывод списка задач с кнопками завершения
@dp.message(Command("tasks"))
async def cmd_tasks(message: types.Message):
    async with async_session() as session:
        # 1. Сначала получаем пользователя, чтобы знать его часовой пояс
        user_query = select(User).where(User.id == message.from_user.id)
        user_result = await session.execute(user_query)
        user = user_result.scalar_one_or_none()

        # Если юзер почему-то не в базе, ставим пояс по умолчанию
        user_tz = ZoneInfo(user.timezone) if user else ZoneInfo("Europe/Moscow")

        # 2. Получаем задачи
        query = select(Task).where(Task.user_id == message.from_user.id)
        result = await session.execute(query)
        tasks = result.scalars().all()

        if not tasks:
            await message.answer("У тебя пока нет задач. Нажми ➕ Добавить задачу")
            return

        response = "📋 Твои задачи:\n\n"
        builder = InlineKeyboardBuilder()

        for task in tasks:
            status = "✅" if task.is_completed else "⏳"
            time_str = ""
            if task.due_time:
                if task.due_time.tzinfo is None:
                    task.due_time = task.due_time.replace(tzinfo=ZoneInfo("UTC"))

                # ТЕПЕРЬ ИСПОЛЬЗУЕМ РЕАЛЬНЫЙ ПОЯС ЮЗЕРА!
                local_time = task.due_time.astimezone(user_tz)
                time_str = f" 🕐 {local_time.strftime('%H:%M %d.%m')}"

            response += f"{status} {task.id}. {task.text}{time_str}\n"

            if not task.is_completed:
                builder.button(text=f"✅ Завершить: {task.text}", callback_data=f"done_{task.id}")

        builder.adjust(1)

        await message.answer(
            response,
            reply_markup=builder.as_markup() if not all(task.is_completed for task in tasks) else None
        )


@dp.message(Command("done"))
async def cmd_done(message: types.Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Пожалуйста, укажи ID задачи.\nПример: /done 1")
        return
    try:
        task_id = int(parts[1])
    except ValueError:
        await message.answer("ID задачи должно быть числом!\nПример: /done 1")
        return

    async with async_session() as session:
        query = select(Task).where(Task.id == task_id, Task.user_id == message.from_user.id)
        result = await session.execute(query)
        task = result.scalar_one_or_none()

        if not task:
            await message.answer("У тебя нет задачи с таким ID. Проверь список /tasks")
            return
        if task.is_completed:
            await message.answer("Эта задача уже и так выполнена! ✅")
            return

        task.is_completed = True
        await session.commit()
        await message.answer(f"✅ Задача '{task.text}' успешно выполнена!")


# Обработчик нажатия кнопки "Настройки"
@dp.message(F.text == "⚙️ Настройки")
async def settings_button(message: types.Message):
    await message.answer("Выбери свой часовой пояс:", reply_markup=get_timezone_kb())


# Ловим нажатие Inline-кнопки с часовым поясом
@dp.callback_query(F.data.startswith("tz_"))
async def process_timezone(callback: CallbackQuery):
    # 1. Достаем данные из кнопки. Например, из "tz_Europe/Moscow" делаем "Europe/Moscow"
    tz_name = callback.data.replace("tz_", "")

    # 2. Сохраняем в базу
    async with async_session() as session:
        query = select(User).where(User.id == callback.from_user.id)
        result = await session.execute(query)
        user = result.scalar_one_or_none()

        if user:
            user.timezone = tz_name
            await session.commit()

    # 3. Меняем текст сообщения, чтобы юзер понял, что всё сохранилось
    # (Кнопки при этом пропадут, что удобно!)
    await callback.message.edit_text(f"✅ Часовой пояс успешно установлен: {tz_name}")

    # 4. Обязательно отвечаем на callback, чтобы убрать "часики" загрузки на кнопке в Telegram
    await callback.answer()


# Ловим нажатие Inline-кнопки завершения задачи
@dp.callback_query(F.data.startswith("done_"))
async def process_done(callback: CallbackQuery):
    # 1. Достаем ID задачи. Из "done_1" получаем "1", переводим в число
    task_id = int(callback.data.split("_")[1])

    async with async_session() as session:
        # Ищем задачу по ID и проверяем, что она принадлежит этому юзеру!
        query = select(Task).where(Task.id == task_id, Task.user_id == callback.from_user.id)
        result = await session.execute(query)
        task = result.scalar_one_or_none()

        if task and not task.is_completed:
            task.is_completed = True
            await session.commit()

            # Меняем текст сообщения: зачеркиваем задачу и меняем статус
            # В Telegram нет зачеркивания в самом тексте кнопки, поэтому отредактируем сообщение
            old_text = callback.message.text
            # Простая замена: меняем ⏳ на ✅ в строке с нашей задачей
            new_text = old_text.replace(f"⏳ {task.id}.", f"✅ {task.id}.")

            # Убираем кнопку, которую нажали (самый простой вариант - убрать все кнопки)
            await callback.message.edit_text(new_text, reply_markup=None)

            await callback.answer("Задача выполнена! ✅", show_alert=True)
        else:
            await callback.answer("Эта задача уже выполнена или не найдена.", show_alert=True)

@dp.message(Command("settimezone"))
async def cmd_settimezone(message: types.Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Пожалуйста, укажи свой часовой пояс.\nПример: /settimezone Europe/Moscow")
        return
    tz_name = parts[1]
    if "/" not in tz_name:
        await message.answer("Неверный формат. Используй формат Регион/Город, например: Europe/Moscow")
        return
    async with async_session() as session:
        query = select(User).where(User.id == message.from_user.id)
        result = await session.execute(query)
        user = result.scalar_one_or_none()
        if user:
            user.timezone = tz_name
            await session.commit()
            await message.answer(f"✅ Часовой пояс успешно установлен: {tz_name}")
        else:
            await message.answer("Сначала нужно зарегистрироваться! Нажми /start")


@dp.message(Command("remind"))
async def cmd_remind(message: types.Message):
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: /remind HH:MM <текст>\nПример: /remind 15:30 Позвонить маме")
        return
    time_str = parts[1]
    task_text = parts[2]
    try:
        hour, minute = map(int, time_str.split(":"))
    except:
        await message.answer("Неверный формат времени. Используй HH:MM\nПример: /remind 15:30 Позвонить маме")
        return

    async with async_session() as session:
        query = select(User).where(User.id == message.from_user.id)
        result = await session.execute(query)
        user = result.scalar_one_or_none()

        if not user:
            await message.answer("Сначала нажми /start")
            return

        user_tz = ZoneInfo(user.timezone)
        now_in_user_tz = datetime.now(user_tz)
        remind_time_local = now_in_user_tz.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if remind_time_local <= now_in_user_tz:
            remind_time_local += timedelta(days=1)

        remind_time_utc = remind_time_local.astimezone(ZoneInfo("UTC"))

        new_task = Task(user_id=message.from_user.id, text=task_text, due_time=remind_time_utc)
        session.add(new_task)
        await session.commit()

        await message.answer(
            f"✅ Напоминание установлено!\n"
            f"Задача: {task_text}\n"
            f"Время: {remind_time_local.strftime('%H:%M %d.%m.%Y')} ({user.timezone})"
        )


# ==========================================
# 2. ВЕБ-АДМИНКА И ЗАПУСК (из webapp.py)
# ==========================================

# Магия Lifespan: эта функция выполняется при старте FastAPI
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Создаем таблицы
    await create_tables()

    # 2. Запускаем планировщик
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_reminders, 'interval', minutes=1)
    scheduler.start()

    # 3. Запускаем бота в фоновом режиме!
    # asyncio.create_task позволяет боту работать параллельно с сайтом
    asyncio.create_task(dp.start_polling(bot))

    yield  # Приложение работает...

    # Код после yield выполнится при выключении
    scheduler.shutdown()


# Создаем приложение FastAPI и передаем ему наш lifespan
app = FastAPI(title="Planner Bot Admin", lifespan=lifespan)

# Специальный легковесный эндпоинт для мониторинга (UptimeRobot)
# Принимает и GET (от браузера), и HEAD (от пингеров)
@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check(request: Request):
    if request.method == "HEAD":
        # Для HEAD-запроса просто возвращаем пустой ответ со статусом 200 ОК
        return Response(status_code=200)
    # Для GET-запроса возвращаем JSON как раньше
    return {"status": "alive"}

@app.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    async with async_session() as session:
        total_users = await session.scalar(select(func.count(User.id)))
        total_tasks = await session.scalar(select(func.count(Task.id)))
        completed_tasks = await session.scalar(select(func.count(Task.id)).where(Task.is_completed == True))

        return templates.TemplateResponse(
            request, "index.html", {
                "total_users": total_users,
                "total_tasks": total_tasks,
                "completed_tasks": completed_tasks
            }
        )


@app.get("/stats")
async def get_stats():
    async with async_session() as session:
        total_users = await session.scalar(select(func.count(User.id)))
        total_tasks = await session.scalar(select(func.count(Task.id)))
        completed_tasks = await session.scalar(select(func.count(Task.id)).where(Task.is_completed == True))
        reminded_tasks = await session.scalar(select(func.count(Task.id)).where(Task.due_time.isnot(None)))
        return {
            "total_users": total_users,
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "pending_tasks": total_tasks - completed_tasks,
            "reminded_tasks": reminded_tasks
        }
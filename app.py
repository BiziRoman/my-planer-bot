import os
import asyncio
from contextlib import asynccontextmanager
from dotenv import load_dotenv

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler

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
            await message.answer("Привет! Ты новый пользователь. Я записал тебя в свою базу!")
        else:
            await message.answer("С возвращением! Ты уже есть в моей базе.")


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


@dp.message(Command("tasks"))
async def cmd_tasks(message: types.Message):
    async with async_session() as session:
        query = select(Task).where(Task.user_id == message.from_user.id)
        result = await session.execute(query)
        tasks = result.scalars().all()

        if not tasks:
            await message.answer("У тебя пока нет задач. Добавь первую командой /add")
            return

        response = "📋 Твои задачи:\n\n"
        for task in tasks:
            status = "✅" if task.is_completed else "⏳"
            time_str = ""
            if task.due_time:
                if task.due_time.tzinfo is None:
                    task.due_time = task.due_time.replace(tzinfo=ZoneInfo("UTC"))
                user_tz = ZoneInfo("Europe/Moscow")
                local_time = task.due_time.astimezone(user_tz)
                time_str = f" 🕐 {local_time.strftime('%H:%M %d.%m')}"
            response += f"{status} {task.id}. {task.text}{time_str}\n"

        await message.answer(response)


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
# Он НЕ делает запросов к базе данных, чтобы не тратить ресурсы
@app.get("/health")
async def health_check():
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
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from sqlalchemy import select

# Импортируем наши инструменты из файла database.py
from database import async_session, User, Base, engine, Task

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import os
from dotenv import load_dotenv

load_dotenv() # Загружаем переменные из .env файла

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ФУНКЦИЯ ДЛЯ СОЗДАНИЯ ТАБЛИЦ (запускается один раз при старте)
async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# Фоновая задача: проверка напоминаний
async def check_reminders():
    now_utc = datetime.now(ZoneInfo("UTC"))  # Текущее время по UTC

    async with async_session() as session:
        # Ищем задачи, где время наступило (меньше или равно сейчас) И они не выполнены
        query = select(Task).where(Task.due_time <= now_utc, Task.is_completed == False)
        result = await session.execute(query)
        tasks_to_send = result.scalars().all()

        for task in tasks_to_send:
            # Отправляем сообщение пользователю
            await bot.send_message(
                task.user_id,
                f"⏰ **НАПОМИНАНИЕ!**\n\nПора: {task.text}"
            )
            # Сразу же отмечаем задачу как выполненную,
            # чтобы не прислать напоминание повторно через минуту!
            task.is_completed = True

        # Сохраняем изменения (что задачи выполнены)
        if tasks_to_send:
            await session.commit()

# Обработчик команды /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    # Открываем сессию (открываем блокнот для записи)
    async with async_session() as session:
        # Спрашиваем у базы: есть ли пользователь с таким ID?
        query = select(User).where(User.id == message.from_user.id)
        result = await session.execute(query)
        user = result.scalar_one_or_none()  # Получаем либо объект юзера, либо None

        if user is None:
            # Если пользователя нет — создаем нового!
            new_user = User(
                id=message.from_user.id,
                username=message.from_user.username
            )
            session.add(new_user)  # Добавляем в сессию
            await session.commit()  # Сохраняем изменения (коммитим)
            await message.answer("Привет! Ты новый пользователь. Я записал тебя в свою базу!")
        else:
            # Если пользователь уже есть
            await message.answer("С возвращением! Ты уже есть в моей базе.")

# Обработчик команды /help
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "🤖 Я твой планировщик! Вот что я умею:\n\n"
        "/start - Запустить бота (и зарегистрироваться)\n"
        "/help - Показать эту справку\n"
        "/add <текст> - Добавить задачу (пример: /add Купить молоко)\n"
        "/tasks - Показать список моих задач\n"
        "/done <id> - Отметить задачу выполненной (пример: /done 1)"
        "/settimezone - Задачть часовой пояс пользователя (пример: Europe/Moscow)\n"
        "/remind - Задать время напоминания о событии (пример: /remind 20:00 Сделать перерыв)\n)"
    )


# Обработчик команды /add
@dp.message(Command("add"))
async def cmd_add(message: types.Message):
    # Отрезаем команду "/add " от текста сообщения
    # Например, из "/add Купить молоко" мы получим "Купить молоко"
    task_text = message.text.replace("/add", "").strip()

    # Если пользователь написал просто "/add" без текста
    if not task_text:
        await message.answer("Пожалуйста, напиши текст задачи после команды.\nПример: /add Купить молоко")
        return

    # Сохраняем задачу в базу
    async with async_session() as session:
        new_task = Task(
            user_id=message.from_user.id,
            text=task_text
        )
        session.add(new_task)
        await session.commit()

        await message.answer(f"✅ Задача '{task_text}' успешно сохранена!")


# Обработчик команды /tasks
@dp.message(Command("tasks"))
async def cmd_tasks(message: types.Message):
    async with async_session() as session:
        # 1. Спрашиваем у базы: "Дай мне все задачи, где user_id совпадает с ID юзера"
        query = select(Task).where(Task.user_id == message.from_user.id)
        result = await session.execute(query)

        # 2. Достаем сами объекты задач из результата запроса
        tasks = result.scalars().all()

        # 3. Если список пуст (задач нет)
        if not tasks:
            await message.answer("У тебя пока нет задач. Добавь первую командой /add")
            return

        # 4. Формируем красивый текст ответа
        response = "📋 Твои задачи:\n\n"
        for task in tasks:
            status = "✅" if task.is_completed else "⏳"

            # Если у задачи есть время (due_time не пустое)
            time_str = ""
            if task.due_time:
                # СТРАХОВКА ОТ "NAIVE" DATETIME
                # Если база отдала время без часового пояса, добавляем UTC
                if task.due_time.tzinfo is None:
                    task.due_time = task.due_time.replace(tzinfo=ZoneInfo("UTC"))

                # Теперь безопасно переводим в пояс пользователя
                user_tz = ZoneInfo("Europe/Moscow")  # Заглушка, потом будем брать из БД юзера
                local_time = task.due_time.astimezone(user_tz)
                time_str = f" 🕐 {local_time.strftime('%H:%M %d.%m')}"

            # ВАЖНО: Эта строчка должна быть внутри цикла for!
            # Она на одном уровне с status = ... и if task.due_time:
            response += f"{status} {task.id}. {task.text}{time_str}\n"

        await message.answer(response)


# Обработчик команды /done
@dp.message(Command("done"))
async def cmd_done(message: types.Message):
    # 1. Разбиваем сообщение на части. "/done 1" -> ["/done", "1"]
    parts = message.text.split()

    # Проверяем, ввел ли пользователь ID задачи
    if len(parts) < 2:
        await message.answer("Пожалуйста, укажи ID задачи.\nПример: /done 1")
        return

    # Пытаемся превратить текст в число
    try:
        task_id = int(parts[1])
    except ValueError:
        await message.answer("ID задачи должно быть числом!\nПример: /done 1")
        return

    # 2. Ищем задачу в базе
    async with async_session() as session:
        # Ищем задачу С ТАКИМ id И ПРИНАДЛЕЖАЩУЮ ЭТОМУ юзеру!
        query = select(Task).where(Task.id == task_id, Task.user_id == message.from_user.id)
        result = await session.execute(query)
        task = result.scalar_one_or_none()  # Получаем 1 задачу или None

        # 3. Если задача не найдена
        if not task:
            await message.answer("У тебя нет задачи с таким ID. Проверь список /tasks")
            return

        # Проверяем, не выполнена ли она уже
        if task.is_completed:
            await message.answer("Эта задача уже и так выполнена! ✅")
            return

        # 4. Меняем статус на выполнено
        task.is_completed = True

        # 5. Сохраняем изменения
        await session.commit()

        await message.answer(f"✅ Задача '{task.text}' успешно выполнена!")


# Обработчик команды /settimezone
@dp.message(Command("settimezone"))
async def cmd_settimezone(message: types.Message):
    # Разбиваем сообщение. Ожидаем: /settimezone Europe/Moscow
    parts = message.text.split()

    if len(parts) < 2:
        await message.answer(
            "Пожалуйста, укажи свой часовой пояс.\n"
            "Пример: /settimezone Europe/Moscow\n\n"
            "Популярные пояса:\n"
            "Europe/Moscow (МСК)\n"
            "Asia/Yekaterinburg (Екат)\n"
            "Asia/Omsk (Омск)\n"
            "Asia/Novosibirsk (Новосиб)\n"
            "Asia/Vladivostok (Владивосток)"
        )
        return

    # Берем вторую часть (сам пояс)
    tz_name = parts[1]

    # Простая проверка, что пояс введен более-менее правильно (содержит /)
    if "/" not in tz_name:
        await message.answer("Неверный формат пояса. Используй формат Регион/Город, например: Europe/Moscow")
        return

    # Сохраняем в базу
    async with async_session() as session:
        # Находим пользователя в базе
        query = select(User).where(User.id == message.from_user.id)
        result = await session.execute(query)
        user = result.scalar_one_or_none()

        if user:
            user.timezone = tz_name  # Меняем пояс
            await session.commit()  # Сохраняем
            await message.answer(f"✅ Часовой пояс успешно установлен: {tz_name}")
        else:
            await message.answer("Сначала нужно зарегистрироваться! Нажми /start")


# Обработчик команды /remind
@dp.message(Command("remind"))
async def cmd_remind(message: types.Message):
    parts = message.text.split(maxsplit=2)  # Разбиваем на 3 части: /remind, 15:30, Текст задачи

    if len(parts) < 3:
        await message.answer("Формат: /remind HH:MM <текст>\nПример: /remind 15:30 Позвонить маме")
        return

    time_str = parts[1]  # "15:30"
    task_text = parts[2]  # "Позвонить маме"

    # Проверяем, правильный ли формат времени
    try:
        # Превращаем строку "15:30" в объекты часов и минут
        hour, minute = map(int, time_str.split(":"))
    except:
        await message.answer("Неверный формат времени. Используй HH:MM\nПример: /remind 15:30 Позвонить маме")
        return

    # Находим пользователя в базе, чтобы узнать его часовой пояс
    async with async_session() as session:
        query = select(User).where(User.id == message.from_user.id)
        result = await session.execute(query)
        user = result.scalar_one_or_none()

        if not user:
            await message.answer("Сначала нажми /start")
            return

        # Получаем текущее время в часовом поясе пользователя
        user_tz = ZoneInfo(user.timezone)
        now_in_user_tz = datetime.now(user_tz)

        # Создаем дату/время напоминания В ЧАСОВОМ ПОЯСЕ ПОЛЬЗОВАТЕЛЯ
        remind_time_local = now_in_user_tz.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # Если время уже прошло сегодня, переносим на завтра
        if remind_time_local <= now_in_user_tz:
            remind_time_local += timedelta(days=1)

        # МАГИЯ: Переводим локальное время в UTC для сохранения в базу
        remind_time_utc = remind_time_local.astimezone(ZoneInfo("UTC"))

        # Сохраняем задачу
        new_task = Task(
            user_id=message.from_user.id,
            text=task_text,
            due_time=remind_time_utc  # Сохраняем время в UTC!
        )
        session.add(new_task)
        await session.commit()

        # Показываем пользователю, во сколько ему напомнят (по его местному времени)
        await message.answer(
            f"✅ Напоминание установлено!\n"
            f"Задача: {task_text}\n"
            f"Время: {remind_time_local.strftime('%H:%M %d.%m.%Y')} ({user.timezone})"
        )


# Главная функция запуска
async def main():
    # СНАЧАЛА создаем таблицы в базе
    await create_tables()

    # НАСТРАИВАЕМ И ЗАПУСКАЕМ ПЛАНИРОВЩИК
    scheduler = AsyncIOScheduler()
    # Добавляем задачу: запускать check_reminders каждую минуту (interval=1)
    scheduler.add_job(check_reminders, 'interval', minutes=1)
    scheduler.start()

    # ПОТОМ запускаем бота (он начинает слушать Telegram)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
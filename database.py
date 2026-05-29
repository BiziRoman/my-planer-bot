from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy import Column, Integer, String, DateTime, func, Boolean, ForeignKey
import os
from dotenv import load_dotenv

load_dotenv()

# 1. Строка подключения (адрес нашей базы в Docker)
DATABASE_URL = os.getenv("DATABASE_URL")

# 2. Создаем "двигатель" (engine) — это тоннель до базы данных
engine = create_async_engine(DATABASE_URL, echo=False)

# 3. Создаем фабрику сессий
# Сессия — это как "открытая страничка в блокноте". Мы берем сессию, что-то пишем, и закрываем.
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# 4. Базовый класс для наших таблиц
class Base(DeclarativeBase):
    pass


# 5. ОПИСЫВАЕМ ТАБЛИЦУ USERS (Пользователи)
class User(Base):
    __tablename__ = 'users'  # Имя таблицы в базе

    # Колонки (столбцы) таблицы:
    id = Column(Integer, primary_key=True, index=True)  # Уникальный ID (Telegram ID)
    username = Column(String, nullable=True)  # Имя пользователя (может быть пустым)
    created_at = Column(DateTime, server_default=func.now())  # Дата регистрации (ставится автоматически)

    # НОВАЯ КОЛОНКА: Часовой пояс. По умолчанию Москва (Europe/Moscow)
    timezone = Column(String, default="Europe/Moscow")


# Таблица Задач
class Task(Base):
    __tablename__ = 'tasks'

    id = Column(Integer, primary_key=True, autoincrement=True)  # ID задачи (генерируется сам)
    user_id = Column(Integer, ForeignKey('users.id'),
                     nullable=False)  # Кому принадлежит задача (ссылка на таблицу users)
    text = Column(String, nullable=False)  # Текст задачи
    due_time = Column(DateTime(timezone=True), nullable=True)  # Время, когда напомнить (пока может быть пустым)
    is_completed = Column(Boolean, default=False)  # Выполнена ли задача? По умолчанию - Нет (False)
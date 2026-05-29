from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from database import async_session, User, Task

# Создаем объект приложения FastAPI
app = FastAPI(title="Planner Bot Admin")

# Указываем FastAPI, где лежат наши HTML-шаблоны
templates = Jinja2Templates(directory="templates")


# Создаем маршрут для ГЛАВНОЙ СТРАНИЦЫ (админка)
@app.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    async with async_session() as session:
        total_users = await session.scalar(select(func.count(User.id)))
        total_tasks = await session.scalar(select(func.count(Task.id)))
        completed_tasks = await session.scalar(
            select(func.count(Task.id)).where(Task.is_completed == True)
        )

        # Передаем собранные данные в HTML-шаблон (СОВРЕМЕННЫЙ СИНТАКСИС)
        return templates.TemplateResponse(
            request,  # 1. Сначала запрос
            "index.html",  # 2. Потом имя шаблона
            {  # 3. Потом словарь с переменными
                "total_users": total_users,
                "total_tasks": total_tasks,
                "completed_tasks": completed_tasks
            }
        )


# Оставляем и старый API для программистов по адресу /stats
@app.get("/stats")
async def get_stats():
    async with async_session() as session:
        total_users = await session.scalar(select(func.count(User.id)))
        total_tasks = await session.scalar(select(func.count(Task.id)))
        completed_tasks = await session.scalar(
            select(func.count(Task.id)).where(Task.is_completed == True)
        )
        reminded_tasks = await session.scalar(
            select(func.count(Task.id)).where(Task.due_time.isnot(None))
        )
        return {
            "total_users": total_users,
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "pending_tasks": total_tasks - completed_tasks,
            "reminded_tasks": reminded_tasks
        }
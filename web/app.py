import json
import os
import time
import logging
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

KEEP_ALIVE_INTERVAL = 300  # 5 минут

def _get_keep_alive_url() -> str:
    port = os.getenv("PORT", "8080")
    return f"http://localhost:{port}/health"

async def keep_alive_loop():
    url = _get_keep_alive_url()
    while True:
        await asyncio.sleep(KEEP_ALIVE_INTERVAL)
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(url)
                logger.info(f"Keep-alive ping: {r.status_code}")
        except Exception as e:
            logger.warning(f"Keep-alive ping failed: {e}")

@asynccontextmanager
async def app_lifespan(app: FastAPI):
    task = asyncio.create_task(keep_alive_loop())
    yield
    task.cancel()

app = FastAPI(title="Telegram Bot Admin Panel", lifespan=app_lifespan)

# Подключаем статику и шаблоны
static_dir = Path(__file__).parent / "static"
templates_dir = Path(__file__).parent / "templates"
static_dir.mkdir(exist_ok=True)
templates_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/health")
async def health_check():
    """Health check endpoint for Render"""
    return {"status": "ok"}

templates = Jinja2Templates(directory=str(templates_dir))
try:
    templates.env.cache_size = 0  # совместимость с Jinja2 3.1.6 + Python 3.14
except Exception:
    pass

# Глобальные данные (будут установлены из bot.py)
BOT_DATA = {
    "user_messages_log": [],
    "bot_messages_log": [],
    "bot_criticism_log": [],
    "user_reputation": {},
    "user_achievements": {},
    "conversations": {},
    "therapy_enabled": True,
    "fool_history": {"fools": [], "last_announcement": 0},
    "admin_password": "admin123"
}

def set_bot_data(data: dict):
    """Устанавливает данные бота для веб-интерфейса"""
    global BOT_DATA
    BOT_DATA.update(data)

def check_auth(request: Request) -> bool:
    """Проверяет авторизацию"""
    password = request.cookies.get("admin_password")
    return password == BOT_DATA.get("admin_password", "admin123")

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Главная страница - дашборд"""
    if not check_auth(request):
        return templates.TemplateResponse("login.html", {"request": request})
    
    # Собираем статистику
    now = time.time()
    day_ago = now - 86400
    
    messages_today = len([m for m in BOT_DATA["user_messages_log"] if m[3] >= day_ago])
    bot_messages_today = len([m for m in BOT_DATA["bot_messages_log"] if m[3] >= day_ago])
    criticisms_today = len([m for m in BOT_DATA["bot_criticism_log"] if m[3] >= day_ago])
    
    # Топ пользователей
    user_counts = {}
    for msg in BOT_DATA["user_messages_log"]:
        if msg[3] >= day_ago:
            user = msg[1]
            user_counts[user] = user_counts.get(user, 0) + 1
    
    top_users = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    
    # Дурак дня
    current_fool = None
    if BOT_DATA["fool_history"]["fools"]:
        current_fool = BOT_DATA["fool_history"]["fools"][-1]
    
    context = {
        "request": request,
        "messages_today": messages_today,
        "bot_messages_today": bot_messages_today,
        "criticisms_today": criticisms_today,
        "top_users": top_users,
        "current_fool": current_fool,
        "therapy_enabled": BOT_DATA["therapy_enabled"],
        "total_users": len(BOT_DATA["user_reputation"]),
        "total_messages": len(BOT_DATA["user_messages_log"])
    }
    
    return templates.TemplateResponse("dashboard.html", context)

@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    """Обработка входа"""
    if password == BOT_DATA.get("admin_password", "admin123"):
        response = RedirectResponse("/", status_code=302)
        response.set_cookie("admin_password", password)
        return response
    return templates.TemplateResponse("login.html", {"request": request, "error": "Неверный пароль"})

@app.get("/logout")
async def logout():
    """Выход"""
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie("admin_password")
    return response

@app.get("/users", response_class=HTMLResponse)
async def users_page(request: Request):
    """Страница пользователей"""
    if not check_auth(request):
        return RedirectResponse("/", status_code=302)
    
    # Сортируем по репутации
    users = sorted(BOT_DATA["user_reputation"].items(), key=lambda x: x[1], reverse=True)
    
    context = {
        "request": request,
        "users": users,
        "achievements": BOT_DATA["user_achievements"]
    }
    
    return templates.TemplateResponse("users.html", context)

@app.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    """Страница статистики"""
    if not check_auth(request):
        return RedirectResponse("/", status_code=302)
    
    now = time.time()
    
    # Статистика по дням (последние 7 дней)
    daily_stats = []
    for i in range(7):
        day_start = now - (i + 1) * 86400
        day_end = now - i * 86400
        
        messages = len([m for m in BOT_DATA["user_messages_log"] 
                       if day_start <= m[3] < day_end])
        bot_msgs = len([m for m in BOT_DATA["bot_messages_log"] 
                       if day_start <= m[3] < day_end])
        
        daily_stats.append({
            "day": time.strftime("%d.%m", time.localtime(day_start)),
            "messages": messages,
            "bot_messages": bot_msgs
        })
    
    daily_stats.reverse()
    
    context = {
        "request": request,
        "daily_stats": daily_stats,
        "total_messages": len(BOT_DATA["user_messages_log"]),
        "total_bot_messages": len(BOT_DATA["bot_messages_log"]),
        "total_criticism": len(BOT_DATA["bot_criticism_log"])
    }
    
    return templates.TemplateResponse("stats.html", context)

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Страница настроек"""
    if not check_auth(request):
        return RedirectResponse("/", status_code=302)
    
    context = {
        "request": request,
        "therapy_enabled": BOT_DATA["therapy_enabled"]
    }
    
    return templates.TemplateResponse("settings.html", context)

@app.post("/settings/therapy/toggle")
async def toggle_therapy(request: Request):
    """Включение/выключение терапии"""
    if not check_auth(request):
        return RedirectResponse("/", status_code=302)
    
    BOT_DATA["therapy_enabled"] = not BOT_DATA["therapy_enabled"]
    return RedirectResponse("/settings", status_code=302)

@app.post("/settings/target/toggle")
async def toggle_target(request: Request):
    """Включение/выключение целевого пользователя"""
    if not check_auth(request):
        return RedirectResponse("/", status_code=302)
    
    # Импортируем глобальную переменную из bot.py
    import sys
    if "bot" in sys.modules:
        sys.modules["bot"].TARGET_USER_ENABLED = not sys.modules["bot"].TARGET_USER_ENABLED
        BOT_DATA["target_enabled"] = sys.modules["bot"].TARGET_USER_ENABLED
    return RedirectResponse("/settings", status_code=302)

@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request, page: int = 1):
    """Страница логов"""
    if not check_auth(request):
        return RedirectResponse("/", status_code=302)
    
    per_page = 50
    total = len(BOT_DATA["user_messages_log"])
    total_pages = (total + per_page - 1) // per_page
    
    start = (page - 1) * per_page
    end = start + per_page
    
    logs = BOT_DATA["user_messages_log"][start:end]
    
    context = {
        "request": request,
        "logs": logs,
        "page": page,
        "total_pages": total_pages,
        "total": total
    }
    
    return templates.TemplateResponse("logs.html", context)

def start_web_server(host: str = "0.0.0.0", port: int = 8080):
    """Запускает веб-сервер"""
    import uvicorn
    uvicorn.run(app, host=host, port=port)

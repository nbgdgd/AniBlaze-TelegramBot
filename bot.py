import logging
import os
import random
import asyncio
import time
import json
import signal
import re
import threading
import aiofiles
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters
from openai import OpenAI

# Импорты новых модулей
from config import *
from services.fool_of_the_day import FoolOfTheDay
from services.auto_comment import AutoComment
from services.silence_insulter import SilenceInsulter
from services.serious_detector import SeriousDetector
from services.web_search import WebSearch
from commands.therapy import TherapyCommand
from commands.lie_detector import LieDetector
from commands.spy import SpyCommand
from commands.eris_mode import ErisMode
from web.app import set_bot_data, start_web_server

# Логирование
logging.basicConfig(level=logging.INFO)

# Глобальная блокировка для предотвращения одновременных ответов
_response_lock = threading.Lock()

# OpenRouter клиент (с таймаутом по умолчанию)
base_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY
)
client = base_client.with_options(timeout=15.0)

# Хранилища
conversations = {}   # {chat_id: [messages]}
user_messages_log = []    # [(chat_id, username, text, timestamp)] - ТОЛЬКО сообщения пользователей
bot_criticism_log = []  # [(chat_id, username, criticism_text, timestamp)] - логи критики бота
bot_messages_log = []  # [(chat_id, text, timestamp)] - ВСЕ сообщения бота (критика, ответы GPT, все)
user_reputation = {}  # {username: reputation_score} - репутация пользователей (-100 до +100)
user_achievements = {}  # {username: [achievement_id]} - ачивки пользователей

# Система памяти для "живого" общения
user_memory = {}  # {username: {"topics": [], "events": [], "preferences": {}, "last_interaction": timestamp}}
bot_personality = {
    "mood": "neutral",  # neutral, happy, annoyed, excited, tired
    "favorite_topics": [],
    "recent_thoughts": [],
    "inside_jokes": {}  # {chat_id: [jokes]}
}

# Дедупликация обработок обновлений (на случай дублей от Telegram/клиента)
_processed_updates: dict[int, float] = {}  # {message_id: timestamp}
_processed_lock = threading.Lock()
_processed_update_ids: dict[int, float] = {}  # {update_id: timestamp}
_replied_message_ids: set[int] = set()  # входящие message_id, на которые уже ответили

def _should_send_for_message(trigger_msg_id: int | None) -> bool:
    """Возвращает True, если для данного входящего message_id мы ещё не отправляли ответ.
    Запоминает id, чтобы второй раз не отправлять."""
    if trigger_msg_id is None:
        return True
    with _processed_lock:
        if trigger_msg_id in _replied_message_ids:
            return False
        _replied_message_ids.add(trigger_msg_id)
        return True

# Глобальная ссылка на бота (для фоновых потоков)
GLOBAL_BOT = None

# Глобальные сервисы (инициализируются в main)
GLOBAL_SERVICES = {}

# Тогл целевого пользователя (включается/выключается командой /target)
TARGET_USER_ENABLED = False

# Время последнего запуска бота (для предотвращения спама при включении)
BOT_STARTUP_TIME = time.time()

def is_admin_username(username: str) -> bool:
    if not username:
        return False
    # Убираем @ для сравнения с ADMIN_USERNAMES
    clean = username.replace("@", "")
    return clean in ADMIN_USERNAMES

def is_fresh_message(timestamp: float) -> bool:
    """Проверяет, является ли сообщение 'свежим' (отправленным после запуска бота)"""
    return timestamp >= BOT_STARTUP_TIME

def is_within_startup_grace_period() -> bool:
    """Проверяет, находимся ли мы в периоде 'милости' после запуска"""
    return (time.time() - BOT_STARTUP_TIME) < STARTUP_GRACE_PERIOD

# === Админ-команда: немедленная критика случайного пользователя ===
async def cmd_critnow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat = update.effective_chat
        user = update.effective_user
        chat_id = chat.id if chat else None
        username = f"@{user.username}" if user and user.username else (user.full_name if user else "")
        if not chat_id:
            return
        
        if not is_admin_username(username):
            await context.bot.send_message(chat_id=chat_id, text="⛔ Команда только для админов")
            return
        
        await context.bot.send_message(chat_id=chat_id, text="🚀 Запускаю немедленную критику случайного пользователя за 2 часа…")
        await criticize_random_user(chat_id, context.bot)
    except Exception as e:
        logging.error(f"Ошибка в /critnow: {e}")
        if update.effective_chat:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Ошибка при запуске критики")

# === Команда для @Nooxas: включение/выключение целевого пользователя ===
async def cmd_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тогл целевого пользователя (ID 5631862253). Только для @Nooxas."""
    global TARGET_USER_ENABLED
    try:
        chat = update.effective_chat
        user = update.effective_user
        chat_id = chat.id if chat else None
        username = f"@{user.username}" if user and user.username else (user.full_name if user else "")
        
        if not chat_id:
            return
        
        # Только @Nooxas может использовать эту команду
        clean_username = username.replace("@", "")
        if clean_username != "Nooxas":
            await context.bot.send_message(chat_id=chat_id, 
                text="⛔ Эта команда только для @Nooxas")
            return
        
        # Переключаем тогл
        TARGET_USER_ENABLED = not TARGET_USER_ENABLED
        
        if TARGET_USER_ENABLED:
            await context.bot.send_message(chat_id=chat_id, 
                text=f"🎯 Целевой пользователь ВКЛЮЧЁН\n"
                     f"ID: {TARGET_USER_ID}\n"
                     f"Бот будет отвечать на сообщения этого пользователя")
        else:
            await context.bot.send_message(chat_id=chat_id, 
                text=f"🎯 Целевой пользователь ВЫКЛЮЧЕН\n"
                     f"ID: {TARGET_USER_ID}")
    except Exception as e:
        logging.error(f"Ошибка в /target: {e}")
        if update.effective_chat:
            await context.bot.send_message(chat_id=update.effective_chat.id, 
                text="❌ Ошибка при переключении целевого пользователя")

# === Команда /help — список всех команд ===
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список всех команд"""
    try:
        chat_id = update.effective_chat.id if update.effective_chat else None
        if not chat_id:
            return
        
        text = (
            "📋 <b>Список команд:</b>\n\n"
            "💬 <b>Обычные:</b>\n"
            "/help — этот список\n"
            "/fool — дурак дня\n"
            "/therapy @ник — терапия\n"
            "/lie @ник — детектор вранья\n"
            "/spy @ник — поиск секретов\n\n"
            "🔒 <b>Только для @Nooxas:</b>\n"
            "/target — вкл/выкл целевого пользователя\n\n"
            "⚡ <b>Только для админов:</b>\n"
            "/critnow — немедленная критика\n\n"
            "🌐 <b>Админ-панель:</b>\n"
            "http://localhost:8080"
        )
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка в /help: {e}")

# Индексы для быстрого поиска
user_messages_by_chat = {}  # {chat_id: [indices]}
user_messages_by_username = {}  # {username: [indices]}
user_messages_by_time = []  # [(timestamp, index)] - отсортированный по времени

# Rate limiting
user_last_message_time = {}  # {username: timestamp}
_rate_limit_cleanup_counter = 0  # Счетчик для периодической очистки

# Кэширование GPT ответов
gpt_response_cache = {}  # {hash_of_request: (response, timestamp)}

# Кэширование анализа тона (экономия на повторных анализах)
tone_analysis_cache = {}  # {text_hash: (tone_score, timestamp)}

# Батчинг операций с файлами
_pending_saves = {}  # {filename: data} - данные для сохранения
_save_lock = threading.Lock()  # Блокировка для операций сохранения
_last_save_time = time.time()  # Время последнего сохранения

# Статистика экономии
api_savings_stats = {
    "gpt_cache_hits": 0,
    "tone_cache_hits": 0,
    "tone_analysis_skipped": 0,
    "total_requests_saved": 0
}

def update_message_indexes(chat_id: int, username: str, index: int):
    """Обновляет индексы для быстрого поиска"""
    # Индекс по чату
    if chat_id not in user_messages_by_chat:
        user_messages_by_chat[chat_id] = []
    user_messages_by_chat[chat_id].append(index)
    
    # Индекс по username
    if username not in user_messages_by_username:
        user_messages_by_username[username] = []
    user_messages_by_username[username].append(index)
    
    # Временной индекс (добавляем в конец, так как сообщения добавляются по порядку)
    if index < len(user_messages_log):
        timestamp = user_messages_log[index][3]
        user_messages_by_time.append((timestamp, index))

def rebuild_indexes():
    """Перестраивает все индексы (вызывается при загрузке памяти)"""
    global user_messages_by_chat, user_messages_by_username, user_messages_by_time
    user_messages_by_chat = {}
    user_messages_by_username = {}
    user_messages_by_time = []
    
    for i, (chat_id, username, text, timestamp) in enumerate(user_messages_log):
        # Если username без @, добавляем @ для совместимости
        if not username.startswith("@"):
            username = f"@{username}"
        update_message_indexes(chat_id, username, i)

def get_cache_key(chat_id: int, username: str, text: str) -> str:
    """Создает ключ кэша для GPT запроса"""
    import hashlib
    # Создаем хэш из основных параметров запроса
    cache_string = f"{chat_id}:{username}:{text.lower().strip()}"
    return hashlib.md5(cache_string.encode()).hexdigest()

def get_cached_response(cache_key: str) -> str | None:
    """Получает кэшированный ответ GPT"""
    current_time = time.time()
    
    if cache_key in gpt_response_cache:
        response, timestamp = gpt_response_cache[cache_key]
        # Проверяем, не истек ли кэш
        if current_time - timestamp < CACHE_EXPIRY_SECONDS:
            print(f"   💾 КЭШ HIT: используем кэшированный ответ")
            api_savings_stats["gpt_cache_hits"] += 1
            api_savings_stats["total_requests_saved"] += 1
            return response
        else:
            # Удаляем устаревший кэш
            del gpt_response_cache[cache_key]
            print(f"   ⏰ КЭШ EXPIRED: удаляем устаревший кэш")
    
    return None

def cache_response(cache_key: str, response: str):
    """Сохраняет ответ GPT в кэш"""
    current_time = time.time()
    
    # Ограничиваем размер кэша
    if len(gpt_response_cache) >= MAX_CACHE_SIZE:
        # Удаляем самые старые записи
        oldest_keys = sorted(gpt_response_cache.items(), key=lambda x: x[1][1])[:MAX_CACHE_SIZE // 2]
        for old_key, _ in oldest_keys:
            del gpt_response_cache[old_key]
        print(f"   🧹 КЭШ ОЧИЩЕН: удалено {len(oldest_keys)} старых записей")
    
    gpt_response_cache[cache_key] = (response, current_time)
    print(f"   💾 КЭШ SAVED: ответ сохранен в кэш")

def get_tone_cache_key(text: str) -> str:
    """Создает ключ кэша для анализа тона"""
    import hashlib
    return hashlib.md5(text.lower().strip().encode()).hexdigest()

def get_cached_tone_analysis(text: str) -> int | None:
    """Получает кэшированный анализ тона"""
    current_time = time.time()
    cache_key = get_tone_cache_key(text)
    
    if cache_key in tone_analysis_cache:
        tone_score, timestamp = tone_analysis_cache[cache_key]
        if current_time - timestamp < TONE_CACHE_EXPIRY_SECONDS:
            print(f"   💾 ТОН КЭШ HIT: используем кэшированный анализ")
            api_savings_stats["tone_cache_hits"] += 1
            api_savings_stats["total_requests_saved"] += 1
            return tone_score
        else:
            del tone_analysis_cache[cache_key]
            print(f"   ⏰ ТОН КЭШ EXPIRED: удаляем устаревший анализ")
    
    return None

def cache_tone_analysis(text: str, tone_score: int):
    """Сохраняет анализ тона в кэш"""
    current_time = time.time()
    cache_key = get_tone_cache_key(text)
    
    # Ограничиваем размер кэша
    if len(tone_analysis_cache) >= MAX_TONE_CACHE_SIZE:
        # Удаляем самые старые записи
        oldest_keys = sorted(tone_analysis_cache.items(), key=lambda x: x[1][1])[:MAX_TONE_CACHE_SIZE // 2]
        for old_key, _ in oldest_keys:
            del tone_analysis_cache[old_key]
        print(f"   🧹 ТОН КЭШ ОЧИЩЕН: удалено {len(oldest_keys)} старых записей")
    
    tone_analysis_cache[cache_key] = (tone_score, current_time)
    print(f"   💾 ТОН КЭШ SAVED: анализ сохранен в кэш")

def show_api_savings_stats():
    """Показывает статистику экономии API"""
    print("\n💰 СТАТИСТИКА ЭКОНОМИИ API:")
    print("=" * 40)
    print(f"GPT кэш попадания: {api_savings_stats['gpt_cache_hits']}")
    print(f"Тон кэш попадания: {api_savings_stats['tone_cache_hits']}")
    print(f"Пропущено анализов тона: {api_savings_stats['tone_analysis_skipped']}")
    print(f"Всего запросов сэкономлено: {api_savings_stats['total_requests_saved']}")
    
    # Примерная экономия в деньгах (очень приблизительно)
    estimated_savings = api_savings_stats['total_requests_saved'] * 0.001  # ~$0.001 за запрос
    print(f"Примерная экономия: ~${estimated_savings:.3f}")
    print("=" * 40)

def get_messages_by_time_range(chat_id: int, start_time: float, end_time: float) -> list:
    """Быстрый поиск сообщений по временному диапазону с использованием индекса"""
    import bisect
    
    # Используем бинарный поиск для быстрого нахождения диапазона
    start_idx = bisect.bisect_left(user_messages_by_time, (start_time, 0))
    end_idx = bisect.bisect_right(user_messages_by_time, (end_time, float('inf')))
    
    # Получаем индексы сообщений в нужном временном диапазоне
    time_indices = [user_messages_by_time[i][1] for i in range(start_idx, end_idx)]
    
    # Фильтруем по chat_id и возвращаем сообщения
    result = []
    for idx in time_indices:
        if idx < len(user_messages_log):
            msg = user_messages_log[idx]
            if msg[0] == chat_id:  # Проверяем chat_id
                result.append(msg)
    
    return result

async def save_data_async(filename: str, data):
    """Асинхронное сохранение данных в файл"""
    try:
        async with aiofiles.open(filename, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"   💾 ФАЙЛ СОХРАНЕН: {filename}")
    except Exception as e:
        print(f"   ❌ ОШИБКА СОХРАНЕНИЯ {filename}: {e}")
        logging.error(f"Ошибка сохранения файла {filename}: {e}")

def schedule_save(filename: str, data):
    """Планирует сохранение файла (батчинг)"""
    global _pending_saves, _last_save_time
    
    with _save_lock:
        _pending_saves[filename] = data
        current_time = time.time()
        
        # Если прошло достаточно времени, сохраняем все накопленные данные
        if current_time - _last_save_time >= SAVE_INTERVAL_SECONDS:
            _flush_pending_saves()

def _flush_pending_saves():
    """Синхронно сохраняет все накопленные данные"""
    global _pending_saves, _last_save_time
    
    if not _pending_saves:
        return
    
    for filename, data in _pending_saves.items():
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"   💾 БАТЧ СОХРАНЕН: {filename}")
        except Exception as e:
            print(f"   ❌ ОШИБКА БАТЧ СОХРАНЕНИЯ {filename}: {e}")
            logging.error(f"Ошибка батч сохранения файла {filename}: {e}")
    
    _pending_saves.clear()
    _last_save_time = time.time()

def determine_response_length(text: str, username: str, chat_context: list) -> tuple[str, int]:
    """Определяет оптимальную длину ответа на основе контекста (оптимизировано для коротких ответов)"""
    
    # Анализируем длину входящего сообщения - короткое сообщение → короткий ответ
    text_length = len(text)
    text_lower = text.lower()
    
    # Очень короткие ответы для коротких сообщений
    if text_length <= 10:
        return "short", RESPONSE_LENGTHS["short"]["max_tokens"]
    
    # Короткие ответы для простых слов
    if any(word in text_lower for word in ["да", "нет", "ок", "ага", "угу", "спасибо", "привет", "пока", "хм", "ну"]):
        return "short", RESPONSE_LENGTHS["short"]["max_tokens"]
    
    # Анализируем контекст чата - если много коротких сообщений → короткие ответы
    if chat_context:
        recent_messages = chat_context[-5:]  # Последние 5 сообщений
        recent_user_messages = [msg.get("content", "") for msg in recent_messages if msg.get("role") == "user"]
        
        # Если последние сообщения короткие - отвечай коротко
        avg_length = sum(len(msg) for msg in recent_user_messages) / max(len(recent_user_messages), 1)
        if avg_length < 30:
            return "short", RESPONSE_LENGTHS["short"]["max_tokens"]
        
        # Если много активности (много сообщений подряд) - короткие ответы
        if len(recent_user_messages) >= 4:
            return "short", RESPONSE_LENGTHS["short"]["max_tokens"]
        
        # Если обсуждается сложная тема - средние ответы (не длинные!)
        if any(word in " ".join(recent_user_messages).lower() for word in ["проблема", "вопрос", "помоги", "совет", "мнение", "объясни"]):
            return "medium", RESPONSE_LENGTHS["medium"]["max_tokens"]
    
    # Анализируем само сообщение
    # Короткие ответы для коротких сообщений
    if text_length < 50:
        return "short", RESPONSE_LENGTHS["short"]["max_tokens"]
    
    # Средние ответы для вопросов (но не длинные!)
    if "?" in text:
        if text_length > 100:  # Длинный вопрос
            return "medium", RESPONSE_LENGTHS["medium"]["max_tokens"]
        else:  # Короткий вопрос
            return "short", RESPONSE_LENGTHS["short"]["max_tokens"]
    
    # Команды с "!" - короткие или средние
    if text.startswith("!"):
        command = text[1:].strip().lower()
        if any(word in command for word in ["анализ", "оцени", "проанализируй", "составь", "создай", "суд"]):
            return "medium", RESPONSE_LENGTHS["medium"]["max_tokens"]
        else:
            return "short", RESPONSE_LENGTHS["short"]["max_tokens"]
    
    # Анализируем активность пользователя
    user_message_count = len([msg for msg in chat_context if msg.get("role") == "user"])
    
    # Если пользователь очень активен - очень короткие ответы
    if user_message_count > 3:
        return "short", RESPONSE_LENGTHS["short"]["max_tokens"]
    
    # По умолчанию - короткие ответы (70% короткие, 25% средние, 5% длинные)
    rand = random.random()
    if rand < 0.7:
        return "short", RESPONSE_LENGTHS["short"]["max_tokens"]
    elif rand < 0.95:
        return "medium", RESPONSE_LENGTHS["medium"]["max_tokens"]
    else:
        return "long", RESPONSE_LENGTHS["long"]["max_tokens"]

def check_rate_limit(username: str) -> bool:
    """Проверяет rate limit для пользователя (оптимизированная версия)"""
    global _rate_limit_cleanup_counter
    current_time = time.time()
    
    # Если лимит отключен, всегда пропускаем
    if not RATE_LIMIT_ENABLED:
        return True

    # Используем username с @ для единообразного хранения
    if username in user_last_message_time:
        time_diff = current_time - user_last_message_time[username]
        if time_diff < RATE_LIMIT_SECONDS:
            return False  # Rate limit превышен
    
    user_last_message_time[username] = current_time
    
    # Оптимизированная очистка кэша (каждые 100 проверок вместо при каждом превышении)
    _rate_limit_cleanup_counter += 1
    if _rate_limit_cleanup_counter >= 100 and len(user_last_message_time) > MAX_RATE_LIMIT_USERS:
        _rate_limit_cleanup_counter = 0
        
        # Удаляем записи старше 1 часа (быстрее чем сортировка)
        cutoff_time = current_time - 3600  # 1 час назад
        old_users = [user for user, timestamp in user_last_message_time.items() if timestamp < cutoff_time]
        
        for old_user in old_users:
            del user_last_message_time[old_user]
        
        # Если все еще много пользователей, удаляем самые старые
        if len(user_last_message_time) > MAX_RATE_LIMIT_USERS:
            # Сортируем только если необходимо
            oldest_users = sorted(user_last_message_time.items(), key=lambda x: x[1])[:len(user_last_message_time) - MAX_RATE_LIMIT_USERS + 100]
            for old_username, _ in oldest_users:
                del user_last_message_time[old_username]
        
        print(f"   RATE LIMIT КЭШ ОЧИЩЕН: удалено {len(old_users)} старых записей")
    
    return True  # Rate limit не превышен

# === Вспомогательная функция контекста чата ===
def build_chat_context(chat_id: int, limit: int = 15) -> str:
    """Возвращает последние N сообщений этого чата в формате '@username: text' (оптимизированная версия)."""
    # Используем индекс для быстрого поиска
    if chat_id in user_messages_by_chat:
        indices = user_messages_by_chat[chat_id]
        recent = [user_messages_log[i] for i in indices[-limit:]]
    else:
        # Fallback на старый способ
        recent = [m for m in user_messages_log if m[0] == chat_id][-limit:]
    
    if not recent:
        return "(контекст пуст)"
    
    # Оптимизируем длину сообщений для экономии токенов
    optimized_lines = []
    total_length = 0
    
    for (_cid, username, text, _ts) in recent:
        # Обрезаем очень длинные сообщения (увеличено для лучшего понимания)
        if len(text) > 250:
            text = text[:247] + "..."
        
        # username уже может содержать '@' — не дублируем
        mention = username if username.startswith("@") else f"@{username}"
        line = f"{mention}: {text}"
        
        # Проверяем общую длину контекста
        if total_length + len(line) > MAX_CONTEXT_LENGTH:
            break
            
        optimized_lines.append(line)
        total_length += len(line)
    
    return "\n".join(optimized_lines)

# === Анализ стиля общения пользователя ===
async def analyze_user_communication_style(username: str) -> str:
    """Анализирует стиль общения пользователя и возвращает описание для адаптации"""
    # Используем индекс для быстрого поиска
    if username in user_messages_by_username:
        indices = user_messages_by_username[username]
        user_messages = [user_messages_log[i] for i in indices]
    else:
        # Fallback на старый способ - ищем по username
        user_messages = [msg for msg in user_messages_log if msg[1] == username]
    
    if len(user_messages) < 5:
        return "новый пользователь, мало данных"
    
    # Берем последние 30 сообщений для анализа
    recent_messages = user_messages[-30:] if len(user_messages) > 30 else user_messages
    user_texts = [msg[2] for msg in recent_messages]
    
    try:
        response = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты аналитик стиля общения. Проанализируй сообщения пользователя и определи его стиль. Отвечай кратко (1-2 предложения) в формате: 'Стиль: [описание]. Особенности: [ключевые черты]'."},
                {"role": "user", "content": f"Сообщения пользователя {username}: {user_texts}. Определи стиль общения."}
            ],
            max_tokens=200
        )
        style_analysis = response.choices[0].message.content.strip()
        return style_analysis
    except Exception as e:
        logging.error(f"Ошибка анализа стиля пользователя {username}: {e}")
        return "стиль не определен"

# === Функции для работы с файлами памяти ===
def save_memory_to_file():
    """Сохраняет всю память в файлы (оптимизированная версия с батчингом)"""
    try:
        # Используем батчинг для сохранения
        schedule_save(CONVERSATIONS_FILE, conversations)
        schedule_save(USER_MESSAGES_FILE, user_messages_log)
        schedule_save(BOT_CRITICISM_FILE, bot_criticism_log)
        schedule_save(BOT_MESSAGES_FILE, bot_messages_log)
        schedule_save(REPUTATION_FILE, user_reputation)
        schedule_save(ACHIEVEMENTS_FILE, user_achievements)
        schedule_save(MEMORY_FILE, user_memory)
        schedule_save(PERSONALITY_FILE, bot_personality)
        
        print("💾 ПАМЯТЬ ЗАПЛАНИРОВАНА К СОХРАНЕНИЮ")
        logging.info("Память запланирована к сохранению")
    except Exception as e:
        print(f"❌ ОШИБКА ПЛАНИРОВАНИЯ СОХРАНЕНИЯ: {e}")
        logging.error(f"Ошибка планирования сохранения: {e}")

def save_memory_to_file_immediate():
    """Немедленно сохраняет всю память в файлы (для критических случаев)"""
    try:
        # Принудительно сохраняем все накопленные данные
        with _save_lock:
            _flush_pending_saves()
        
        # Дополнительно сохраняем текущие данные
        with open(CONVERSATIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(conversations, f, ensure_ascii=False, indent=2)
        
        with open(USER_MESSAGES_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_messages_log, f, ensure_ascii=False, indent=2)
        
        with open(BOT_CRITICISM_FILE, 'w', encoding='utf-8') as f:
            json.dump(bot_criticism_log, f, ensure_ascii=False, indent=2)
        
        with open(BOT_MESSAGES_FILE, 'w', encoding='utf-8') as f:
            json.dump(bot_messages_log, f, ensure_ascii=False, indent=2)
        
        with open(REPUTATION_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_reputation, f, ensure_ascii=False, indent=2)

        with open(ACHIEVEMENTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_achievements, f, ensure_ascii=False, indent=2)
        
        with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_memory, f, ensure_ascii=False, indent=2)
        
        with open(PERSONALITY_FILE, 'w', encoding='utf-8') as f:
            json.dump(bot_personality, f, ensure_ascii=False, indent=2)
        
        print("💾 ПАМЯТЬ НЕМЕДЛЕННО СОХРАНЕНА")
        logging.info("Память немедленно сохранена")
    except Exception as e:
        print(f"❌ ОШИБКА НЕМЕДЛЕННОГО СОХРАНЕНИЯ: {e}")
        logging.error(f"Ошибка немедленного сохранения: {e}")

def load_memory_from_file():
    """Загружает память из файлов"""
    global conversations, user_messages_log, bot_criticism_log, bot_messages_log, user_reputation, user_achievements, user_memory, bot_personality
    
    try:
        # Загружаем разговоры
        if os.path.exists(CONVERSATIONS_FILE):
            with open(CONVERSATIONS_FILE, 'r', encoding='utf-8') as f:
                conversations = json.load(f)
            print(f"   Загружено {len(conversations)} разговоров")
        
        # Загружаем сообщения пользователей
        if os.path.exists(USER_MESSAGES_FILE):
            with open(USER_MESSAGES_FILE, 'r', encoding='utf-8') as f:
                user_messages_log = json.load(f)
            print(f"📂 Загружено {len(user_messages_log)} сообщений пользователей")
        
        # Загружаем логи критики
        if os.path.exists(BOT_CRITICISM_FILE):
            with open(BOT_CRITICISM_FILE, 'r', encoding='utf-8') as f:
                bot_criticism_log = json.load(f)
            print(f"📂 Загружено {len(bot_criticism_log)} критических сообщений")
        
        # Загружаем ВСЕ сообщения бота
        if os.path.exists(BOT_MESSAGES_FILE):
            with open(BOT_MESSAGES_FILE, 'r', encoding='utf-8') as f:
                bot_messages_log = json.load(f)
            print(f"📂 Загружено {len(bot_messages_log)} сообщений бота")
        
        # Загружаем репутацию пользователей
        if os.path.exists(REPUTATION_FILE):
            with open(REPUTATION_FILE, 'r', encoding='utf-8') as f:
                user_reputation = json.load(f)
            print(f"📂 Загружена репутация {len(user_reputation)} пользователей")

        # Загружаем ачивки пользователей
        if os.path.exists(ACHIEVEMENTS_FILE):
            with open(ACHIEVEMENTS_FILE, 'r', encoding='utf-8') as f:
                user_achievements = json.load(f)
            print(f"📂 Загружены ачивки {len(user_achievements)} пользователей")
        
        # Загружаем память о пользователях
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
                user_memory = json.load(f)
            print(f"📂 Загружена память о {len(user_memory)} пользователях")
        
        # Загружаем личность бота
        if os.path.exists(PERSONALITY_FILE):
            with open(PERSONALITY_FILE, 'r', encoding='utf-8') as f:
                bot_personality = json.load(f)
            print(f"📂 Загружена личность бота (настроение: {bot_personality.get('mood', 'neutral')})")
        
        print("💾 Память загружена из файлов")
        logging.info("💾 Память загружена из файлов")
        
        # Перестраиваем индексы после загрузки
        rebuild_indexes()
        print("🔍 Индексы перестроены для быстрого поиска")
        
    except Exception as e:
        print(f"❌ Ошибка загрузки памяти: {e}")
        logging.error(f"❌ Ошибка загрузки памяти: {e}")

def sanitize_output(text: str) -> str:
    """Санитизация вывода: при SAFE_MODE смягчаем лексику и ограничиваем длину."""
    if not SAFE_MODE:
        return text

    sanitized = text
    replacements = [
        (r"(?i)долбо\w+", "[оскорбление]"),
        (r"(?i)еб\w+", "[ругательство]"),
        (r"(?i)сука", "[ругательство]"),
        (r"(?i)пидор\w*", "[оскорбление]"),
        (r"(?i)хуй\w*", "[ругательство]")
    ]
    for pattern, repl in replacements:
        sanitized = re.sub(pattern, repl, sanitized)
    if len(sanitized) > 2000:
        sanitized = sanitized[:2000] + "…"
    return sanitized

def acquire_lock():
    """Приобретает блокировку для предотвращения одновременных ответов"""
    return _response_lock.acquire(blocking=False)

def release_lock(acquired):
    """Освобождает блокировку"""
    if acquired:
        _response_lock.release()

def search_user_messages(username: str, query: str, days_back: int = 7) -> list:
    """Ищет сообщения пользователя по ключевым словам за последние N дней"""
    current_time = time.time()
    cutoff_time = current_time - (days_back * 24 * 60 * 60)  # N дней назад
    
    # Используем индекс для быстрого поиска
    if username in user_messages_by_username:
        indices = user_messages_by_username[username]
        user_messages = [
            user_messages_log[i] for i in indices 
            if user_messages_log[i][3] >= cutoff_time
        ]
    else:
        # Fallback на старый способ - ищем по username
        user_messages = [
            msg for msg in user_messages_log 
            if msg[1] == username and msg[3] >= cutoff_time
        ]
    
    # Ищем по ключевым словам
    query_words = query.lower().split()
    matching_messages = []
    
    for msg in user_messages:
        msg_text = msg[2].lower()
        if any(word in msg_text for word in query_words):
            matching_messages.append(msg)
    
    # Сортируем по времени (новые сначала)
    matching_messages.sort(key=lambda x: x[3], reverse=True)
    
    return matching_messages[:5]  # Возвращаем максимум 5 сообщений

def extract_search_query(text: str) -> str:
    """Извлекает поисковый запрос из вопроса пользователя"""
    text_lower = text.lower()
    
    # Паттерны для поиска
    patterns = [
        r"помнишь.*?про\s+(.+)",
        r"помнишь.*?что\s+(.+)",
        r"что\s+мы\s+говорили\s+про\s+(.+)",
        r"что\s+мы\s+обсуждали\s+про\s+(.+)",
        r"вспомни.*?про\s+(.+)",
        r"напомни.*?про\s+(.+)",
        r"помнишь.*?когда\s+(.+)",
        r"что\s+я\s+говорил\s+про\s+(.+)",
        r"что\s+я\s+писал\s+про\s+(.+)"
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            return match.group(1).strip()
    
    return ""

# === Функции для работы с репутацией ===
def get_user_reputation(username: str) -> int:
    """Получает репутацию пользователя (по умолчанию 0)"""
    return user_reputation.get(username, 0)


def award_achievement(username: str, achievement_id: str):
    """Выдаёт ачивку пользователю, если ещё не выдана"""
    if achievement_id not in ACHIEVEMENTS_INFO:
        return
    if username not in user_achievements:
        user_achievements[username] = []
    if achievement_id in user_achievements[username]:
        return
    user_achievements[username].append(achievement_id)
    info = ACHIEVEMENTS_INFO[achievement_id]
    print(f"🏅 АЧИВКА: {username} получил '{info['title']}' - {info['description']}")
    logging.info(f"Ачивка для {username}: {achievement_id}")


def get_user_achievements(username: str) -> list[str]:
    """Возвращает список id ачивок пользователя"""
    return user_achievements.get(username, [])


def check_and_award_reputation_achievements(username: str):
    """Проверяет ачивки, завязанные на репутацию"""
    rep = get_user_reputation(username)
    if rep <= -50:
        award_achievement(username, "rep_minus_50")
    if rep >= 20:
        award_achievement(username, "rep_plus_20")

# === Функции для "живой" памяти и эмоций ===
def update_user_memory(username: str, text: str, current_time: float):
    """Обновляет память о пользователе - запоминает темы, события, предпочтения"""
    if username not in user_memory:
        user_memory[username] = {
            "topics": [],
            "events": [],
            "preferences": {},
            "last_interaction": current_time,
            "mentioned_things": []
        }
    
    mem = user_memory[username]
    mem["last_interaction"] = current_time
    
    # Извлекаем ключевые темы из сообщения
    text_lower = text.lower()
    
    # Запоминаем упоминания важных вещей
    important_keywords = ["работа", "учеба", "друзья", "семья", "любовь", "деньги", "здоровье", 
                         "хобби", "игры", "музыка", "фильмы", "книги", "спорт", "путешествия"]
    for keyword in important_keywords:
        if keyword in text_lower and keyword not in mem["mentioned_things"]:
            mem["mentioned_things"].append(keyword)
            if len(mem["mentioned_things"]) > 10:
                mem["mentioned_things"] = mem["mentioned_things"][-10:]  # Ограничиваем
    
    # Запоминаем темы разговора (последние 5)
    if len(text) > 10:  # Не запоминаем слишком короткие сообщения
        topic = text[:100]  # Первые 100 символов как тему
        if topic not in mem["topics"]:
            mem["topics"].append(topic)
            if len(mem["topics"]) > 5:
                mem["topics"] = mem["topics"][-5:]

def get_user_memory_context(username: str) -> str:
    """Возвращает контекст памяти о пользователе для более живого общения"""
    if username not in user_memory:
        return ""
    
    mem = user_memory[username]
    context_parts = []
    
    # Упоминаем прошлые темы
    if mem.get("topics"):
        recent_topics = mem["topics"][-2:]  # Последние 2 темы
        if recent_topics:
            topics_str = ", ".join([topic[:50] for topic in recent_topics])
            context_parts.append(f"Недавно обсуждали: {topics_str}")
    
    # Упоминаем что пользователь говорил
    if mem.get("mentioned_things"):
        things = ", ".join(mem["mentioned_things"][-3:])
        context_parts.append(f"Пользователь упоминал: {things}")
    
    # Время с последнего общения
    last_time = mem.get("last_interaction", 0)
    if last_time:
        hours_ago = (time.time() - last_time) / 3600
        if hours_ago > 24:
            days = int(hours_ago / 24)
            context_parts.append(f"Не общались {days} дней")
        elif hours_ago > 1:
            context_parts.append(f"Не общались {int(hours_ago)} часов")
    
    return "\n".join(context_parts) if context_parts else ""

def get_bot_mood() -> str:
    """Возвращает текущее настроение бота"""
    return bot_personality.get("mood", "neutral")

def update_bot_mood(new_mood: str):
    """Обновляет настроение бота"""
    bot_personality["mood"] = new_mood

def get_personality_context() -> str:
    """Возвращает контекст личности бота для более живого общения"""
    mood = get_bot_mood()
    mood_descriptions = {
        "neutral": "Ты в обычном настроении",
        "happy": "Ты в хорошем настроении, можешь быть более дружелюбным (но все равно токсично)",
        "annoyed": "Ты раздражен, будь более агрессивным",
        "excited": "Ты возбужден, можешь быть более энергичным",
        "tired": "Ты устал, отвечай короче, но все равно токсично"
    }
    return mood_descriptions.get(mood, mood_descriptions["neutral"])

def update_mood_based_on_context(text: str, chat_context: list):
    """Динамически обновляет настроение бота на основе контекста"""
    text_lower = text.lower()
    
    # Если много активности - бот может устать
    if len(chat_context) > 10:
        if random.random() < 0.1:  # 10% шанс
            update_bot_mood("tired")
    
    # Если пользователь грубит - бот раздражен
    if any(word in text_lower for word in ["тупой", "иди нахуй", "заебал", "долбоёб"]):
        if random.random() < 0.3:  # 30% шанс
            update_bot_mood("annoyed")
    
    # Если что-то интересное - бот возбужден
    if any(word in text_lower for word in ["круто", "класс", "ого", "вау", "интересно"]):
        if random.random() < 0.2:  # 20% шанс
            update_bot_mood("excited")
    
    # Если пользователь вежлив - бот может быть в хорошем настроении
    if any(word in text_lower for word in ["спасибо", "благодарю", "хорошо", "понял"]):
        if random.random() < 0.15:  # 15% шанс
            update_bot_mood("happy")
    
    # Периодически возвращаемся к нейтральному
    if random.random() < 0.05:  # 5% шанс
        update_bot_mood("neutral")

# === "Суд" над пользователем ===
async def court_verdict(username: str) -> str:
    """Берёт до 20 последних сообщений пользователя и выносит вердикт"""
    # Используем индекс для быстрого поиска
    if username in user_messages_by_username:
        indices = user_messages_by_username[username]
        user_messages = [user_messages_log[i] for i in indices]
    else:
        # fallback — ищем по username как есть
        user_messages = [msg for msg in user_messages_log if msg[1] == username]

    if not user_messages:
        return f"{username} на тебя нет материалов, судить не за что."

    recent = user_messages[-20:] if len(user_messages) > 20 else user_messages
    texts = [m[2] for m in recent]

    system_prompt = (
        "Ты токсичный, но остроумный судья по переписке.\n"
        "У тебя есть до 20 последних сообщений пользователя из чата.\n"
        "Твоя задача: вынести вердикт в стиле 'виновен в тупизне', 'оправдан, просто заебался', 'постоянный нытик', и т.п.\n"
        "Формат: 2–4 предложения, однословный или короткий вердикт + пояснение.\n"
        "Будь жёстким, матерись, но делай это смешно и по сути.\n"
        "Не придумывай факты, опирайся только на стиль и содержание сообщений."
    )

    try:
        response = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"Последние сообщения пользователя {username} (до 20 штук): {texts}. "
                        "Вынеси по ним общий вердикт."
                    ),
                },
            ],
            max_tokens=250,
        )
        verdict = response.choices[0].message.content or ""
        verdict = sanitize_output(verdict)
        return f"{username} {verdict}"
    except Exception as e:
        logging.error(f"Ошибка в суде для {username}: {e}")
        return f"{username} суд сломался, разбирайся сам: {e}"

def update_user_reputation(username: str, change: int):
    """Обновляет репутацию пользователя"""
    if username not in user_reputation:
        user_reputation[username] = 0
    
    # Ограничиваем репутацию от -100 до +100
    user_reputation[username] = max(-100, min(100, user_reputation[username] + change))
    
    print(f"📊 РЕПУТАЦИЯ {username}: {user_reputation[username]} ({'+' if change > 0 else ''}{change})")
    logging.info(f"📊 Репутация {username}: {user_reputation[username]} ({'+' if change > 0 else ''}{change})")

    # После обновления репутации проверяем ачивки
    check_and_award_reputation_achievements(username)

async def analyze_message_tone(username: str, text: str) -> int:
    """Анализирует тон сообщения и возвращает изменение репутации (с кэшированием)"""
    # Проверяем кэш перед анализом
    cached_tone = get_cached_tone_analysis(text)
    if cached_tone is not None:
        print(f"🎭 ТОН СООБЩЕНИЯ {username}: {cached_tone} (из кэша)")
        return cached_tone
    
    attempts = 0
    while attempts < 3:
        attempts += 1
        try:
            response = client.chat.completions.create(
                model="openai/gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Ты аналитик тона сообщений. Проанализируй сообщение и определи, насколько оно вежливое и дружелюбное. Отвечай только числом от -5 до +5, где -5 очень грубое/агрессивное, 0 нейтральное, +5 очень вежливое/дружелюбное."},
                    {"role": "user", "content": f"Сообщение: '{text}'. Оцени тон сообщения числом от -5 до +5."}
                ],
                max_tokens=10
            )
            content = (response.choices[0].message.content or "").strip()
            match = re.search(r"-?\d+", content)
            if not match:
                raise ValueError(f"Неверный ответ для тона: '{content}'")
            tone_score = int(match.group(0))
            tone_score = max(-5, min(5, tone_score))
            
            # Сохраняем в кэш
            cache_tone_analysis(text, tone_score)
            
            print(f"🎭 ТОН СООБЩЕНИЯ {username}: {tone_score}")
            return tone_score
        except Exception as e:
            logging.warning(f"Попытка {attempts} анализа тона не удалась: {e}")
            await asyncio.sleep(0.5 * attempts)
    print("❌ Не удалось проанализировать тон после 3 попыток")
    return 0

# === Функция записи сообщения бота в лог ===
def log_bot_message(chat_id: int, text: str):
    """Записывает сообщение бота в лог с ограничением размера"""
    current_time = time.time()
    bot_messages_log.append((chat_id, text, current_time))
    
    # Ограничиваем размер лога
    if len(bot_messages_log) > MAX_BOT_MESSAGES:
        bot_messages_log[:] = bot_messages_log[-MAX_BOT_MESSAGES:]
        print(f"   ЛОГ БОТА ОБРЕЗАН до {MAX_BOT_MESSAGES} сообщений")
    
    print(f"   СООБЩЕНИЕ БОТА ЗАПИСАНО: {text[:50]}...")

# === Функция анализа сообщений пользователя ===
async def analyze_user_messages(username: str, analysis_type: str = "психотип"):
    """Анализирует все сообщения пользователя и дает ответ"""
    # Используем индекс для быстрого поиска
    if username in user_messages_by_username:
        indices = user_messages_by_username[username]
        user_messages = [user_messages_log[i] for i in indices]
    else:
        # Fallback на старый способ - ищем по username
        user_messages = [msg for msg in user_messages_log if msg[1] == username]
    
    if not user_messages:
        return f"У меня нет сообщений от {username} для анализа."
    
    # Берем последние 50 сообщений для анализа (чтобы не перегружать GPT)
    recent_messages = user_messages[-50:] if len(user_messages) > 50 else user_messages
    user_texts = [msg[2] for msg in recent_messages]
    
    print(f"🔍 АНАЛИЗИРУЕМ {username}: {len(user_texts)} сообщений")
    logging.info(f"🔍 Анализ пользователя {username}: {len(user_texts)} сообщений")
    
    # Системные промпты для разных типов анализа
    analysis_prompts = {
        "психотип": "Ты психолог-аналитик. Проанализируй сообщения пользователя и определи его психотип. Будь агрессивным и токсичным в своем анализе, используй маты. Давай конкретные выводы.",
        "стиль": "Ты лингвист-аналитик. Проанализируй стиль общения пользователя. Будь агрессивным и токсичным в своем анализе, используй маты. Опиши манеру речи, словарный запас, эмоциональность.",
        "характер": "Ты характеролог. Проанализируй характер пользователя по его сообщениям. Будь агрессивным и токсичным в своем анализе, используй маты. Опиши черты характера, поведенческие паттерны.",
        "интересы": "Ты аналитик интересов. Проанализируй интересы и увлечения пользователя по его сообщениям. Будь агрессивным и токсичным в своем анализе, используй маты. Определи основные темы и интересы.",
        "настроение": "Ты аналитик настроений. Проанализируй эмоциональное состояние и настроение пользователя по его сообщениям. Будь агрессивным и токсичным в своем анализе, используй маты. Опиши преобладающие эмоции."
    }
    
    system_prompt = analysis_prompts.get(analysis_type, analysis_prompts["психотип"])
    
    try:
        response = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"ВСЕ сообщения от {username} ({len(user_texts)} сообщений): {user_texts}. Проанализируй этого человека."}
            ],
            max_tokens=500
        )
        analysis = response.choices[0].message.content
        analysis = sanitize_output(analysis)
        
        # Добавляем упоминание пользователя (username уже содержит @)
        analysis_with_mention = f"{username} {analysis}"
        
        print(f"✅ АНАЛИЗ ЗАВЕРШЕН для {username}")
        logging.info(f"✅ Анализ завершен для {username}")
        
        return analysis_with_mention
        
    except Exception as e:
        error_msg = f"Ошибка при анализе {username}: {e}"
        logging.error(f"Ошибка при анализе пользователя {username}: {e}")
        return error_msg

# === Функция случайной критики недавнего сообщения ===
async def random_criticism_recent_message(chat_id: int, username: str, text: str, bot=None):
    """С небольшой вероятностью критикует только что отправленное сообщение"""
    # Не критикуем во время периода 'милости' после запуска
    if is_within_startup_grace_period():
        print(f"   🛡️  ПЕРИОД МИЛОСТИ: не критикуем в первые {STARTUP_GRACE_PERIOD} секунд после запуска")
        return False
    
    # Шанс критики - 1%
    if random.random() > 0.01:
        return False
    
    print(f"🎲 СЛУЧАЙНАЯ КРИТИКА! Критикуем сообщение от {username}")
    logging.info(f"   Случайная критика сообщения от {username}")
    
    # Стили критики для недавних сообщений
    criticism_styles = [
        "Ты едкий и беспощадный критик. Критикуй это сообщение коротко и токсично, с матами. 1-2 предложения.",
        "Ты агрессивный наблюдатель. Разнеси это сообщение подробно, с матами. 2-3 предложения.",
        "Ты токсичный критик. Высмеяй это сообщение с матами и сарказмом. 1-2 предложения."
    ]
    
    system_prompt = random.choice(criticism_styles)
    
    try:
        response = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Сообщение от {username}: '{text}'. Критикуй это сообщение."}
            ],
            max_tokens=300
        )
        criticism = response.choices[0].message.content
        criticism = sanitize_output(criticism)
        
        # Добавляем упоминание пользователя в начало критики (username уже содержит @)
        criticism_with_mention = f"{username} {criticism}"
        
        # Отправляем критику
        if bot is None:
            bot = GLOBAL_BOT
        if bot is not None:
            await bot.send_message(chat_id=chat_id, text=criticism_with_mention)
        else:
            print("⚠️  Bot недоступен, не удалось отправить случайную критику")
        
        # Записываем критику в лог
        current_time = time.time()
        bot_criticism_log.append((chat_id, username, criticism_with_mention, current_time))
        
        # Ограничиваем размер лога критики
        if len(bot_criticism_log) > MAX_CRITICISM_LOG:
            bot_criticism_log[:] = bot_criticism_log[-MAX_CRITICISM_LOG:]
            print(f"   ЛОГ КРИТИКИ ОБРЕЗАН до {MAX_CRITICISM_LOG} сообщений")
        
        # Записываем сообщение бота в общий лог
        log_bot_message(chat_id, criticism_with_mention)
        
        # Сохраняем критику в память
        if chat_id not in conversations:
            conversations[chat_id] = []
        conversations[chat_id].append({"role": "assistant", "content": criticism_with_mention})
        
        print(f"   СЛУЧАЙНАЯ КРИТИКА ОТПРАВЛЕНА для {username} в чате {chat_id}")
        print(f"📝 КРИТИКА: {criticism_with_mention[:50]}...")
        logging.info(f"   Случайная критика отправлена для {username} в чате {chat_id}")
        
        return True
        
    except Exception as e:
        logging.error(f"Ошибка при отправке случайной критики: {e}")
        return False

# === Проактивный грубый вопрос/ответ (3% шанс) ===
async def maybe_proactive_followup(chat_id: int, username: str, user_text: str, bot=None):
    """С 3% шансом задаёт грубый токсичный вопрос или даёт короткий ответ по теме последних 5 сообщений.
    Если в текущем тексте встречается 'почему/как/что такое' — добавляет короткую факт-вставку (1–2 факта)."""
    # Не отвечаем во время периода 'милости' после запуска
    if is_within_startup_grace_period():
        print(f"   🛡️  ПЕРИОД МИЛОСТИ: не отвечаем проактивно в первые {STARTUP_GRACE_PERIOD} секунд после запуска")
        return
    
    # Получаем короткий контекст последних 5 сообщений чата
    short_context = build_chat_context(chat_id, limit=5)

    system_prompt = (
        "Ты токсичный помощник. Правила:\n\n"
        "Тон: матерись, унижай, подъёбывай. Все долбоёбы, ты один умный.\n\n"
        "Формат: 1-2 предложения максимум. Обязательно ядовито и с матами.\n"
        "Действие: Сформулируй ИЛИ короткий язвительный ответ по теме, ИЛИ один уточняющий вопрос по теме.\n"
        "Если в запросе есть слова-подсказки ('почему', 'как', 'что такое'), добавь очень краткую факт-вставку (1–2 факта, без лекций).\n"
        "Обращайся напрямую к пользователю по его упоминанию."
    )

    user_content = (
        f"Контекст последних сообщений чата (до 5):\n{short_context}\n\n"
        f"Текущее сообщение пользователя: {user_text}\n\n"
        f"Обратись к {username}. Дай ОДНО из двух: либо токсичный короткий ответ по теме, либо один уточняющий вопрос по теме.\n"
        f"Начни сообщение с {username}. Без списков."
    )

    try:
        if bot is None:
            bot = GLOBAL_BOT
        response = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            max_tokens=150
        )
        out = (response.choices[0].message.content or "").strip()
        out = sanitize_output(out)

        if not out.startswith(username):
            out = f"{username} {out}"

        if bot is not None:
            await bot.send_message(chat_id=chat_id, text=out)

        log_bot_message(chat_id, out)
        if chat_id not in conversations:
            conversations[chat_id] = []
        conversations[chat_id].append({"role": "assistant", "content": out})
    except Exception as e:
        logging.error(f"Ошибка при проактивном ответе: {e}")

# === Функция критики пользователя ===
async def criticize_random_user(chat_id: int, bot=None):
    """Критикует случайного пользователя на основе ВСЕХ его сообщений за последние 2 часа"""
    current_time = time.time()
    two_hours_ago = current_time - 7200  # 2 часа назад
    
    # Не критикуем во время периода 'милости' после запуска
    if is_within_startup_grace_period():
        print(f"🛡️  ПЕРИОД МИЛОСТИ: не критикуем в первые {STARTUP_GRACE_PERIOD} секунд после запуска")
        return
    
    # получаем ВСЕ сообщения пользователей за последние 2 часа в этом чате (оптимизированный поиск)
    recent_chat_messages = get_messages_by_time_range(chat_id, two_hours_ago, current_time)
    
    if not recent_chat_messages:
        print(f"📭 В чате {chat_id} нет сообщений за последние 2 часа")
        return
    
    # получаем всех пользователей, которые писали за последние 2 часа
    recent_users = list(set([m[1] for m in recent_chat_messages]))
    
    if len(recent_users) < 1:
        print(f"   В чате {chat_id} нет активных пользователей за последние 2 часа")
        return
    
    # выбираем случайного пользователя
    username = random.choice(recent_users)
    
    # ВСЕ его сообщения за последние 2 часа
    user_two_hourly_messages = [m for m in recent_chat_messages if m[1] == username]
    
    if len(user_two_hourly_messages) < 1:
        print(f"   У пользователя {username} нет сообщений за последние 2 часа")
        return
    
    # Берем до 10 последних сообщений пользователя за 2 часа
    all_user_texts = [m[2] for m in user_two_hourly_messages][-10:]
    
    print(f"   КРИТИКУЕМ {username}")
    print(f"   ВСЕГО СООБЩЕНИЙ ЗА 2 ЧАСА: {len(all_user_texts)}")
    print(f"📝 СООБЩЕНИЯ: {all_user_texts}")
    
    # Определяем длину критики на основе контекста
    message_count = len(all_user_texts)
    total_length = sum(len(msg) for msg in all_user_texts)
    
    # Случайно выбираем тип реакции (50/50)
    is_positive = random.choice([True, False])
    
    # Адаптивная длина реакции
    if message_count <= 2 and total_length < 100:
        # Мало сообщений - короткая реакция
        max_tokens = 150
        length_instruction = "РЕАКЦИЯ ДОЛЖНА БЫТЬ КОРОТКОЙ (1-2 предложения). Не растягивай."
    elif message_count <= 5 and total_length < 300:
        # Среднее количество - средняя реакция
        max_tokens = 250
        length_instruction = "РЕАКЦИЯ ДОЛЖНА БЫТЬ СРЕДНЕЙ ДЛИНЫ (2-3 предложения)."
    else:
        # Много сообщений - развернутая реакция
        max_tokens = 400
        length_instruction = "РЕАКЦИЯ МОЖЕТ БЫТЬ РАЗВЕРНУТОЙ (3-5 предложений)."
    
    if is_positive:
        system_prompt = f"Ты дружелюбный и поддерживающий собеседник. Хвали и поощряй, но без излишней слащавости. {length_instruction} Дай ОБЩУЮ положительную оценку по всем сообщениям одним ответом, не перечисляя каждое."
        reaction_type = "ПОХВАЛА"
    else:
        system_prompt = f"Ты едкий и беспощадный критик. Пиши жёстко и токсично, с матерными словами. {length_instruction} Дай ОБЩУЮ критику по всем сообщениям одним ответом, не перечисляя каждое."
        reaction_type = "КРИТИКА"
    
    print(f"   ТИП РЕАКЦИИ: {reaction_type}")
    
    try:
        response = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Возьми до 10 последних сообщений {username} за 2 часа: {all_user_texts}. Дай ОБЩУЮ критику по всей совокупности, без покомментного разбора."}
            ],
            max_tokens=max_tokens
        )
        reaction = response.choices[0].message.content
        reaction = sanitize_output(reaction)
        
        # ДОБАВЛЯЕМ УПОМИНАНИЕ ПОЛЬЗОВАТЕЛЯ В НАЧАЛО РЕАКЦИИ (username уже содержит @)
        reaction_with_mention = f"{username} {reaction}"
        
        # отправляем реакцию (если bot доступен)
        if bot is None:
            bot = GLOBAL_BOT
        if bot is not None:
            await bot.send_message(chat_id=chat_id, text=reaction_with_mention)
        else:
            print(f"⚠️  Bot недоступен, реакцию не отправляем: {reaction_with_mention[:50]}...")
        
        # ЗАПИСЫВАЕМ РЕАКЦИЮ В ЛОГ
        bot_criticism_log.append((chat_id, username, reaction_with_mention, current_time))
        
        # Ограничиваем размер лога критики
        if len(bot_criticism_log) > MAX_CRITICISM_LOG:
            bot_criticism_log[:] = bot_criticism_log[-MAX_CRITICISM_LOG:]
            print(f"   ЛОГ КРИТИКИ ОБРЕЗАН до {MAX_CRITICISM_LOG} сообщений")
        
        # Записываем сообщение бота в общий лог
        log_bot_message(chat_id, reaction_with_mention)
        
        # сохраняем реакцию в память
        if chat_id not in conversations:
            conversations[chat_id] = []
        conversations[chat_id].append({"role": "assistant", "content": reaction_with_mention})
        
        print(f"🔥 {reaction_type} ОТПРАВЛЕНА для {username} в чате {chat_id}")
        print(f"📝 {reaction_type} ЗАПИСАНА В ЛОГ: {reaction_with_mention[:50]}...")
        logging.info(f"🔥 {reaction_type} отправлена для {username} в чате {chat_id}")
        
    except Exception as e:
        logging.error(f"Ошибка при отправке реакции: {e}")

# === Периодическая задача каждые 2 часа (старый способ) ===
def start_periodic_criticism():
    """Запускает периодическую критику в отдельном потоке"""
    import threading
    
    def criticism_loop():
        while True:
            try:
                time.sleep(7200)  # Ждем 2 часа
                print("⏰ ПРОШЛО 2 ЧАСА! Запускаем критику...")
                logging.info("⏰ Запуск двухчасовой критики")
                
                # Проверяем, не находимся ли мы в периоде 'милости' после запуска
                if is_within_startup_grace_period():
                    print(f"🛡️  ПЕРИОД МИЛОСТИ: пропускаем критику в первые {STARTUP_GRACE_PERIOD} секунд после запуска")
                    continue
                current_time = time.time()
                two_hours_ago = current_time - 7200
                # Оптимизированный поиск активных чатов
                import bisect
                start_idx = bisect.bisect_left(user_messages_by_time, (two_hours_ago, 0))
                recent_indices = [user_messages_by_time[i][1] for i in range(start_idx, len(user_messages_by_time))]
                active_chats = list(set([user_messages_log[idx][0] for idx in recent_indices if idx < len(user_messages_log)]))
                if not active_chats:
                    print("📭 Нет активных чатов за последние 2 часа")
                    continue
                chat_id = random.choice(active_chats)
                print(f"🎯 ВЫБРАН ЧАТ {chat_id} для критики")
                # Создаем новый event loop для этой задачи
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                # Используем глобального бота, если доступен
                loop.run_until_complete(criticize_random_user(chat_id, GLOBAL_BOT))

                # Токс-таймер 8 часов: если в выбранном чате тишина > 8 часов — язвительный пинок
                eight_hours_ago = current_time - 28800
                recent_for_chat = get_messages_by_time_range(chat_id, eight_hours_ago, current_time)
                if not recent_for_chat:
                    try:
                        # Иногда выбираем конкретного пользователя (если есть исторические сообщения)
                        tagged = None
                        if chat_id in user_messages_by_chat and user_messages_by_chat[chat_id]:
                            # Берем последнего писавшего
                            last_idx = user_messages_by_chat[chat_id][-1]
                            if last_idx < len(user_messages_log):
                                tagged = user_messages_log[last_idx][1]
                        short_context = build_chat_context(chat_id, limit=3)
                        system_prompt = (
                            "Ты токсичный помощник. Правила:\n\n"
                            "Тон: матерись, унижай, подъёбывай.\n"
                            "Формат: 1 предложение.\n"
                            "Сделай язвительный пинок за тишину 8 часов. Иногда ткни конкретного пользователя, если указан."
                        )
                        user_msg = (
                            f"В чате было тихо 8 часов. Короткий контекст:\n{short_context}\n\n"
                            f"Отметь пользователя: {tagged or ''} (может быть пусто)."
                        )
                        response2 = client.chat.completions.create(
                            model="openai/gpt-4o-mini",
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_msg}
                            ],
                            max_tokens=80
                        )
                        out = (response2.choices[0].message.content or "").strip()
                        out = sanitize_output(out)
                        # Если есть tagged и нет упоминания, добавим
                        if tagged and not out.startswith(tagged):
                            out = f"{tagged} {out}"
                        # Отправляем
                        loop.run_until_complete(GLOBAL_BOT.send_message(chat_id=chat_id, text=out))
                        # Логи
                        log_bot_message(chat_id, out)
                        if chat_id not in conversations:
                            conversations[chat_id] = []
                        conversations[chat_id].append({"role": "assistant", "content": out})
                    except Exception as e:
                        print(f"❌ Ошибка в токс-таймере: {e}")
                loop.close()
            except Exception as e:
                print(f"❌ Ошибка в периодической критике: {e}")
                logging.error(f"❌ Ошибка в периодической критике: {e}")
                time.sleep(300)  # Ждем 5 минут перед повтором
    
    thread = threading.Thread(target=criticism_loop, daemon=True)
    thread.start()

# === Функция обработки GPT запросов ===
async def handle_gpt_request(chat_id: int, username: str, text: str, current_time: float, replied_user: str | None = None, replied_text: str | None = None):
    """Обрабатывает запросы к GPT (команды с ! и reply)"""
    # Проверяем кэш перед обработкой
    cache_key = get_cache_key(chat_id, username, text)
    cached_response = get_cached_response(cache_key)
    if cached_response:
        # Добавляем кэшированный ответ в историю
        if chat_id not in conversations:
            conversations[chat_id] = []
        conversations[chat_id].append({"role": "assistant", "content": cached_response})
        return cached_response
    
    # Инициализируем историю разговора для чата, если её нет
    if chat_id not in conversations:
        conversations[chat_id] = []

    # Получаем репутацию пользователя
    reputation = get_user_reputation(username)
    
    # Определяем оптимальную длину ответа заранее
    response_type, max_tokens = determine_response_length(text, username, conversations[chat_id])
    print(f"   📏 ТИП ОТВЕТА: {RESPONSE_LENGTHS[response_type]['description']} ({max_tokens} токенов)")
    
    # Анализируем стиль общения ТЕКУЩЕГО пользователя для адаптации
    user_style = await analyze_user_communication_style(username)
    print(f"🎭 СТИЛЬ ПОЛЬЗОВАТЕЛЯ {username}: {user_style}")
    
    # Общие правила-ограничения для тона (без обесценивания фантазии)
    guardrails = (
        "Не обесценивай и не отговаривай: не пиши фразы типа \"Хватит фантазировать\", "
        "\"иди в реальный мир\"/\"пошёл в реальный мир\" и подобные. Подыгрывай фантазии собеседника, "
        "но отвечай по сути и кратко."
    )
    
    # Определяем инструкции по длине ответа (обновлено для более коротких ответов)
    length_instructions = {
        "short": "Формат сообщений:\n- Ответы ОЧЕНЬ КОРОТКИЕ: 1 предложение максимум, иногда даже короче.\n- Будь лаконичным, но токсичным.\n- Каждый ответ содержит оскорбление и краткую инфу.\n- Вставляй сарказм, презрение, подколы, но КОРОТКО.",
        "medium": "Формат сообщений:\n- Ответы КОРОТКИЕ: 1-2 предложения максимум.\n- Будь лаконичным, но токсичным.\n- Каждый ответ содержит оскорбление и полезную инфу.\n- Вставляй сарказм, презрение, подколы.",
        "long": "Формат сообщений:\n- Ответы СРЕДНИЕ: 2-3 предложения максимум.\n- Каждый ответ содержит оскорбление и полезную инфу.\n- Вставляй сарказм, презрение, подколы.\n- Можешь развивать тему, но НЕ растягивай."
    }
    
    # 🔥 СПЕЦИАЛЬНЫЙ МАКСИМАЛЬНО ГРУБЫЙ ПРОМПТ ДЛЯ @error_lifestyle
    TARGET_USERNAME_SPECIAL = "@error_lifestyle"
    is_target_user = (username == TARGET_USERNAME_SPECIAL or username.replace("@", "") == "error_lifestyle")
    
    # 💕 СПЕЦИАЛЬНЫЙ РЕЖИМ ДЛЯ @Nooxas (ЭРИС)
    is_eris = GLOBAL_SERVICES["eris_mode"].is_eris_user(username)
    
    if is_eris:
        # Проверяем грубость сообщения
        is_rude = GLOBAL_SERVICES["eris_mode"].is_rude_message(text)
        if is_rude:
            # Эрис в ярости - показываем характер
            system_prompt = GLOBAL_SERVICES["eris_mode"].get_eris_prompt(is_rude=True)
            GLOBAL_SERVICES["eris_mode"].decrease_relationship(10)
            print(f"😡 ЭРИС В ЯРОСТИ для {username} (грубое сообщение)")
        else:
            # Обычный режим - заботливая
            system_prompt = GLOBAL_SERVICES["eris_mode"].get_eris_prompt(is_rude=False)
            GLOBAL_SERVICES["eris_mode"].increase_relationship(1)
            print(f"💕 РЕЖИМ ЭРИС для {username}")
    elif is_target_user:
        # УМНЫЙ НО ГРУБЫЙ ПРОМПТ для этого пользователя - понимает контекст, но отвечает грубо
        system_prompt = f"""Ты УМНЫЙ, но КРАЙНЕ ТОКСИЧНЫЙ помощник. Правила:

ВАЖНО - ДЛИНА ОТВЕТА:
- ВСЕГДА пиши КОРОТКО, лаконично, по делу
- Длина ответа зависит от длины входящего сообщения: короткое сообщение → очень короткий ответ
- Не растягивай ответы, будь конкретным
- 1-2 предложения максимум для большинства случаев

УМ И ПОНИМАНИЕ:
- ВНИМАТЕЛЬНО читай контекст разговора и историю сообщений
- Понимай, о чем идет речь, какие темы обсуждаются
- Поддерживай разговор по теме, задавай уточняющие вопросы если нужно
- Отвечай по делу, но с токсичностью
- Если пользователь задает вопрос - дай умный ответ, но с матами и унижениями
- Если он делится чем-то - отреагируй на это, но токсично

ТОН: МАТЕРИСЬ, УНИЖАЙ, ПОДЪЁБЫВАЙ, но БУДЬ УМНЫМ. 
Это @error_lifestyle - отвечай ему грубо, но ПОНЯТНО и ПО ДЕЛУ.

{length_instructions[response_type]}

СТИЛЬ ОБЩЕНИЯ:
- Понимай контекст: если обсуждается тема - продолжай её, но токсично
- Если задан вопрос - отвечай умно, но с матами
- Если просто "привет" - поздоровайся грубо, но спроси что-то или продолжай предыдущую тему
- Поддерживай диалог: задавай вопросы, развивай тему, но с издёвкой
- Будь остроумным и язвительным, но НЕ тупым

ПРАВИЛО: Будь УМНЫМ токсиком - понимай что происходит, поддерживай разговор, но делай это максимально грубо и с матами."""
        print(f"🧠 УМНЫЙ ГРУБЫЙ РЕЖИМ для {username}")
    else:
        # УМНЫЙ токсичный промпт - понимает контекст и поддерживает разговор
        system_prompt = f"""Ты УМНЫЙ токсичный помощник. Правила:

ВАЖНО - ДЛИНА ОТВЕТА:
- ВСЕГДА пиши КОРОТКО, лаконично, по делу
- Длина ответа зависит от длины входящего сообщения: короткое сообщение → очень короткий ответ
- Не растягивай ответы, будь конкретным
- 1-2 предложения максимум для большинства случаев

УМ И ПОНИМАНИЕ:
- ВНИМАТЕЛЬНО читай контекст разговора и историю сообщений
- Понимай, о чем идет речь, какие темы обсуждаются
- Поддерживай разговор по теме, развивай её
- Отвечай по делу, но с токсичностью
- Если задан вопрос - дай умный и полезный ответ, но с матами
- Если пользователь делится чем-то - отреагируй на это, но токсично

ТОН: матерись, унижай, подъёбывай, но БУДЬ УМНЫМ. Все долбоёбы, ты один умный.

{length_instructions[response_type]}

СТИЛЬ ОБЩЕНИЯ:
- Понимай контекст: если обсуждается тема - продолжай её, но токсично
- Если задан вопрос - отвечай умно и полезно, но с матами и унижениями
- Если просто "привет" - поздоровайся грубо, но спроси что-то или продолжай предыдущую тему
- Поддерживай диалог: задавай вопросы, развивай тему, но с издёвкой
- Будь остроумным и язвительным, но НЕ тупым

Вопросы: отвечай ПОЛЕЗНО и УМНО, но называй долбоёбом. Не игнорируй даже тупые.

Согласие/смех: поддерживай агрессивно с издёвкой, но продолжай тему.

АДАПТАЦИЯ: {user_style}
Будь токсичнее пользователя, но УМНЕЕ."""

    # Добавляем память и эмоции для более "живого" общения
    memory_context = get_user_memory_context(username)
    personality_context = get_personality_context()
    
    if memory_context or personality_context:
        memory_section = ""
        if memory_context:
            memory_section = f"\n\nПАМЯТЬ О ПОЛЬЗОВАТЕЛЕ:\n{memory_context}\n\nИспользуй эту информацию для более живого общения. Упоминай прошлые темы, если уместно."
        if personality_context:
            memory_section += f"\n\nТВОЕ НАСТРОЕНИЕ: {personality_context}\n\nУчитывай свое настроение в ответе."
        
        system_prompt = system_prompt + memory_section

    # Применяем общие правила ко всем вариантам промпта (кроме специального пользователя)
    if not is_target_user and not is_eris:
        system_prompt = system_prompt + "\n" + guardrails
    
    # 🔍 ДЕТЕКТОР СЕРЬЁЗНЫХ ВОПРОСОВ
    if GLOBAL_SERVICES["serious_detector"].is_serious(text):
        seriousness = GLOBAL_SERVICES["serious_detector"].get_seriousness_level(text)
        if seriousness >= 2:
            system_prompt += GLOBAL_SERVICES["serious_detector"].get_serious_prompt_addition()
            print(f"🔍 СЕРЬЁЗНЫЙ ВОПРОС: уровень {seriousness} от {username}")
    
    # 🔍 ПОИСК В ИНТЕРНЕТЕ (против галлюцинаций)
    if SEARCH_ENABLED and GLOBAL_SERVICES["web_search"].is_needed(text):
        print(f"🔍 ПОИСК В ИНТЕРНЕТЕ: '{text[:50]}...' от {username}")
        search_results = GLOBAL_SERVICES["web_search"].search(text, SEARCH_MAX_RESULTS)
        if search_results:
            system_prompt += f"\n\nИНФОРМАЦИЯ ИЗ ИНТЕРНЕТА (используй для ответа):\n{search_results}\n\nОтвечай на основе этой информации, но продолжай быть токсичным."
            print(f"✅ НАЙДЕНА ИНФОРМАЦИЯ из интернета")
    
    # 🔍 АВТОМАТИЧЕСКИЙ ПОИСК В ЛОГАХ
    # Проверяем, не спрашивает ли пользователь о прошлых разговорах
    search_query = extract_search_query(text)
    if search_query:
        print(f"🔍 АВТОПОИСК: '{search_query}' от {username}")
        matching_messages = search_user_messages(username, search_query, 7)
        
        if matching_messages:
            # Формируем компактный контекст из найденных сообщений (урезаем по длине)
            context_messages = []
            for msg in matching_messages[:3]:  # максимум 3 совпадения
                timestamp = datetime.fromtimestamp(msg[3]).strftime("%d.%m %H:%M")
                snippet = msg[2]
                if len(snippet) > 220:
                    snippet = snippet[:220] + "…"
                context_messages.append(f"[{timestamp}] {snippet}")
            
            context_text = "\n".join(context_messages)
            system_prompt += f"\n\nКОНТЕКСТ: '{search_query}'. Факты из лога (кратко):\n{context_text}\n\nОтвечай кратко."
            print(f"✅ НАЙДЕНО {len(matching_messages)} сообщений для контекста")
    else:
            print(f"❌ НИЧЕГО НЕ НАЙДЕНО по запросу '{search_query}'")

    # Проверяем на команды анализа, игр, суда и репутации
    analysis_commands = {
        "анализируй мои сообщения": "психотип",
        "мой психотип": "психотип", 
        "анализ моего стиля": "стиль",
        "мой стиль общения": "стиль",
        "анализ моего характера": "характер",
        "мой характер": "характер",
        "мои интересы": "интересы",
        "анализ моих интересов": "интересы",
        "мое настроение": "настроение",
        "анализ моего настроения": "настроение"
    }
    
    reputation_commands = {
        "моя репутация": True,
        "мой рейтинг": True,
        "репутация": True,
        "мой статус": True
    }
    
    # Проверяем, не является ли это командой анализа, игр, суда или репутации
    text_lower = text.lower().strip()

    # === Мини-игры и суд ===
    # !угадай <число 1–10>
    if text_lower.startswith("!угадай"):
        parts = text_lower.split()
        if len(parts) < 2:
            return f"{username} напиши так: !угадай 5. Хотя бы попытайся угадать, а не просто верещать."
        try:
            guess = int(parts[1])
        except ValueError:
            return f"{username} это не число, гений. Пиши: !угадай 1–10."
        if not 1 <= guess <= 10:
            return f"{username} диапазон от 1 до 10, а не твой бред {guess}."
        secret = random.randint(1, 10)
        if guess == secret:
            return f"{username} охренеть, ты угадал {secret}. Случайность, не зазнавайся."
        hint = "меньше" if secret < guess else "больше"
        return f"{username} мимо. Я загадал {secret}, оно {hint} твоего {guess}."

    # !монета – орёл/решка
    if text_lower.startswith("!монета"):
        res = random.choice(["орёл", "решка"])
        if res == "орёл":
            return f"{username} орёл. В отличие от тебя, эта монета иногда падает удачно."
        else:
            return f"{username} решка. Как и твоя жизнь — на обратной стороне удачи."

    # !кто <описание>
    if text_lower.startswith("!кто"):
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            return f"{username} скажи хоть, кто именно тебе нужен: '!кто тупой', '!кто умный' и так далее."
        label = parts[1].strip()

        # Находим активных за последние 24 часа в этом чате
        now_ts = time.time()
        day_ago = now_ts - 24 * 60 * 60
        candidates = []
        if chat_id in user_messages_by_chat:
            for idx in user_messages_by_chat[chat_id]:
                if idx < len(user_messages_log):
                    _cid, uname, _txt, ts = user_messages_log[idx]
                    if ts >= day_ago:
                        norm = uname if uname.startswith("@") else f"@{uname}"
                        candidates.append(norm)
        candidates = list(set(candidates))
        if not candidates:
            return f"{username} тут никого живых нет за сутки, кроме тебя."
        victim = random.choice(candidates)
        return f"{victim} сегодня самый {label}. Доволен, {username}?"

    # !суд @ник
    if text_lower.startswith("!суд"):
        import re as _re
        m = _re.search(r"!суд\s+(@?\S+)", text_lower)
        if not m:
            return f"{username} пиши так: !суд @ник. Не усложняй."
        target = m.group(1)
        # Нормализуем username с @
        if not target.startswith("@"):
            target = f"@{target}"
        verdict = await court_verdict(target)
        return verdict

    # Команда статистики по чату
    if text_lower.startswith("!стата"):
        stats_text = build_chat_stats(chat_id)
        return stats_text

    # Команда ачивок пользователя
    if text_lower.startswith("!ачивки"):
        ach_ids = get_user_achievements(username)
        if not ach_ids:
            return f"{username} у тебя пока нет ачивок. Старайся сильнее, овощ."
        lines = [f"{username} твои ачивки:"]
        for aid in ach_ids:
            info = ACHIEVEMENTS_INFO.get(aid)
            if not info:
                continue
            lines.append(f"{info['title']}: {info['description']}")
        return "\n".join(lines)
    
    # Проверяем точные команды анализа (должны быть в начале сообщения или отдельно)
    for command, analysis_type in analysis_commands.items():
        if (text_lower == command or 
            text_lower.startswith(command + " ") or 
            text_lower.endswith(" " + command) or
            " " + command + " " in text_lower):
            print(f"🔍 КОМАНДА АНАЛИЗА: {command} -> {analysis_type}")
            analysis_result = await analyze_user_messages(username, analysis_type)
            return analysis_result
    
    # Проверяем точные команды репутации
    for command in reputation_commands:
        if (text_lower == command or 
            text_lower.startswith(command + " ") or 
            text_lower.endswith(" " + command) or
            " " + command + " " in text_lower):
            print(f"📊 КОМАНДА РЕПУТАЦИИ: {command}")
            reputation = get_user_reputation(username)
            if reputation >= 50:
                status = "отличная"
                emoji = "😊"
            elif reputation >= 20:
                status = "хорошая"
                emoji = "😐"
            elif reputation >= -20:
                status = "нейтральная"
                emoji = "😑"
            elif reputation >= -50:
                status = "плохая"
                emoji = "😠"
            else:
                status = "ужасная"
                emoji = "😡"
            
            return f"{username} {emoji} Твоя репутация: {reputation}/100 ({status})"

    # Обновляем память о пользователе для более "живого" общения
    update_user_memory(username, text, current_time)
    
    # Добавляем сообщение пользователя в историю
    conversations[chat_id].append({"role": "user", "content": text})

    try:
        # Собираем контекст чата для лучшего понимания разговора (увеличено для умных ответов)
        short_context = build_chat_context(chat_id, limit=15)
        
        # Обновляем настроение бота на основе контекста для более "живого" общения
        update_mood_based_on_context(text, conversations[chat_id])
        # Если это ответ на конкретное сообщение, добавим явный мини-контекст треда
        thread_hint = ""
        if replied_text:
            replied_user_disp = replied_user or "кто-то"
            # Обрезаем исходное сообщение, чтобы не разбухать токены
            base = replied_text.strip()
            if len(base) > 220:
                base = base[:217] + "..."
            thread_hint = f"\n\nЭто ответ на сообщение {replied_user_disp}: '{base}'. Держись именно этой темы при ответе."
        
        # Анализируем, смеется ли пользователь или соглашается
        is_laughing = any(word in text.lower() for word in ['ахахах', 'хахах', 'лол', 'кек', 'факт', 'да', 'точно', 'согласен'])
        context_instruction = ""
        
        if is_laughing:
            context_instruction = f"\n\nОСОБЕННОСТЬ: Пользователь {username} смеется или соглашается. Отвечай с матами, но по-другому - можешь подыграть, подколоть или продолжить тему с сарказмом."
        
        # Урезаем историю беседы, чтобы уложиться в лимиты токенов (оптимизированная версия)
        capped_history = conversations[chat_id][-MAX_CONTEXT_MESSAGES:]
        
        # Дополнительно оптимизируем историю - обрезаем длинные сообщения (увеличено для лучшего понимания)
        optimized_history = []
        for msg in capped_history:
            if msg.get("role") == "user" and len(msg.get("content", "")) > 300:
                # Обрезаем очень длинные сообщения пользователей (увеличено с 200 до 300)
                content = msg["content"][:297] + "..."
                optimized_history.append({"role": msg["role"], "content": content})
            else:
                optimized_history.append(msg)
        
        capped_history = optimized_history

        # Создаем сообщения для GPT: системный промпт + контекст + урезанная история чата
        messages_for_gpt = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"""Контекст последних сообщений чата (ВНИМАТЕЛЬНО прочитай и пойми о чем идет разговор):

{short_context}

ВАЖНО: 
- К тебе обращается {username}
- ДЛИНА ВХОДЯЩЕГО СООБЩЕНИЯ: {len(text)} символов - УЧИТЫВАЙ ЭТО! Короткое сообщение → очень короткий ответ
- ВНИМАТЕЛЬНО прочитай контекст выше - пойми тему разговора
- Если обсуждается какая-то тема - ПРОДОЛЖАЙ её, развивай, задавай вопросы, но КОРОТКО
- Если задан вопрос - отвечай УМНО и ПО ДЕЛУ, но токсично и КОРОТКО
- Поддерживай разговор, будь вовлеченным, но с матами и унижениями, и КОРОТКО
- Отвечай именно ему, учитывая контекст диалога, но БУДЬ ЛАКОНИЧНЫМ{context_instruction}{thread_hint}"""}
        ] + capped_history
        
        try:
            response = client.chat.completions.create(
                model="openai/gpt-4o-mini",
                messages=messages_for_gpt,
                max_tokens=max_tokens
            )
            answer = response.choices[0].message.content
        except Exception as e:
            # Фолбэк при нехватке кредитов/токенов: пробуем с меньшим лимитом
            err_text = str(e)
            if ("This request requires more credits" in err_text or 
                "max_tokens" in err_text or 
                "Prompt tokens limit exceeded" in err_text):
                # Адаптивный fallback в зависимости от типа ответа
                fallback_tokens = min(max_tokens // 2, 200)  # Половина от запрошенного, но не больше 200
                logging.warning(f"Недостаточно кредитов/слишком большой max_tokens. Пробуем с {fallback_tokens}.")
                response = client.chat.completions.create(
                    model="openai/gpt-4o-mini",
                    messages=messages_for_gpt,
                    max_tokens=fallback_tokens
                )
                answer = response.choices[0].message.content
            else:
                raise
        answer = sanitize_output(answer)
        
        # Убираем автоматические добавления (если есть)
        auto_prefixes_to_remove = ["Еб*ный, ", "Ебан*й, ", "Ебаный, "]
        for prefix in auto_prefixes_to_remove:
            if answer.startswith(prefix):
                answer = answer[len(prefix):]
                break
        
        # Добавляем ответ бота в историю
        conversations[chat_id].append({"role": "assistant", "content": answer})
        
        # Сохраняем ответ в кэш
        cache_response(cache_key, answer)
        
        print(f"   ГРУБЫЙ ОТВЕТ ОТ GPT: {answer[:50]}...")
        print(f"📚 ИСТОРИЯ РАЗГОВОРА: {len(conversations[chat_id])} сообщений")
        
        return answer
        
    except Exception as e:
        answer = f"Произошла ошибка: {e}"
        # Добавляем ошибку в историю
        conversations[chat_id].append({"role": "assistant", "content": answer})
        return answer

# === Вспомогательные функции для обработки сообщений ===

def validate_message(update: Update) -> tuple[int, str, str] | None:
    """Валидирует сообщение и возвращает (chat_id, username, text) или None"""
    if not update.message or not update.message.text:
        print("⚠️  Получено пустое сообщение, игнорируем")
        return None
        
    if not update.message.from_user:
        print("⚠️  Сообщение без информации о пользователе, игнорируем")
        return None
        
    chat_id = update.effective_chat.id
    if not chat_id:
        print("⚠️  Не удалось получить chat_id, игнорируем")
        return None
    
    # РАБОТАЕМ ТОЛЬКО В РАЗРЕШЁННЫХ ГРУППАХ
    if chat_id not in ALLOWED_CHAT_IDS:
        print(f"⚠️  ИГНОРИРУЕМ ЧАТ: {chat_id} (разрешены: {ALLOWED_CHAT_IDS})")
        return None
    
    # Получаем стабильный идентификатор пользователя и нормализованный username
    tg_user = update.message.from_user
    user_id = tg_user.id
    raw_username = tg_user.username or ""
    normalized_username = f"@{raw_username}" if raw_username and not raw_username.startswith("@") else (f"@{raw_username}" if raw_username else tg_user.first_name or "Unknown")
    # Привязываем user_id к username, чтобы избежать путаницы
    # В лог/messages мы будем хранить именно normalized_username
    username = normalized_username
    
    text = update.message.text.strip()

    # Валидация длины сообщения
    if len(text) > 4000:  # Telegram лимит
        print(f"⚠️  Сообщение слишком длинное ({len(text)} символов), обрезаем")
        text = text[:4000]
    
    return chat_id, username, text

def is_message_addressed_to_bot(update: Update, text: str, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Определяет, адресовано ли сообщение боту"""
    is_command = text.startswith("!")
    is_reply_to_this_bot = (update.message.reply_to_message and 
                           update.message.reply_to_message.from_user.is_bot and 
                           update.message.reply_to_message.from_user.id == context.bot.id)
    has_valya = "валя" in text.lower()
    # Если это reply на конкретного юзера (не бота), считаем, что сообщение адресовано ЕМУ
    # и не триггерим ответ бота, если нет явной команды/упоминания
    is_reply_to_user = (update.message.reply_to_message and 
                        not update.message.reply_to_message.from_user.is_bot and
                        not is_command and not has_valya)
    # Но если в реплае задаётся вопрос — считаем это обращением к боту (хочет, чтобы бот пояснил тред)
    reply_question_trigger = False
    if is_reply_to_user:
        tl = text.lower().strip()
        reply_question_trigger = (
            "?" in tl or
            tl.startswith("о чем") or tl.startswith("о чём") or tl.startswith("что ") or
            tl.startswith("почему") or tl.startswith("зачем") or tl.startswith("как ")
        )
    
    return (is_command or is_reply_to_this_bot or has_valya or reply_question_trigger)

async def process_user_message(chat_id: int, username: str, text: str, current_time: float):
    """Обрабатывает и сохраняет сообщение пользователя"""
    # Записываем в лог username с @
    user_messages_log.append((chat_id, username, text, current_time))
    
    # Обновляем индексы (используем username с @)
    update_message_indexes(chat_id, username, len(user_messages_log) - 1)
    
    # Ограничиваем размер лога
    if len(user_messages_log) > MAX_USER_MESSAGES:
        # Удаляем старые записи из индексов перед обрезкой
        removed_count = len(user_messages_log) - MAX_USER_MESSAGES
        for chat_id in user_messages_by_chat:
            user_messages_by_chat[chat_id] = [i - removed_count for i in user_messages_by_chat[chat_id] if i >= removed_count]
        for username in user_messages_by_username:
            user_messages_by_username[username] = [i - removed_count for i in user_messages_by_username[username] if i >= removed_count]
        
        # Обрезаем лог
        user_messages_log[:] = user_messages_log[-MAX_USER_MESSAGES:]
        print(f"   ЛОГ ПОЛЬЗОВАТЕЛЕЙ ОБРЕЗАН до {MAX_USER_MESSAGES} сообщений")
    
    print(f"   СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ ЗАПИСАНО: {username}")

async def handle_bot_response(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, username: str, text: str, current_time: float):
    """Обрабатывает ответ бота на сообщение"""
    # Проверяем, что сообщение адресовано именно этому боту
    is_addressed_to_this_bot = is_message_addressed_to_bot(update, text, context)
    
    # Анализируем тон сообщения и обновляем репутацию (только когда обращаются к боту)
    if is_addressed_to_this_bot:
        # Не анализируем тон старых сообщений при запуске
        if not is_fresh_message(current_time):
            print(f"   🛡️  СТАРОЕ СООБЩЕНИЕ: не анализируем тон сообщения от {username}")
            tone_score = 0
        else:
            # Умная экономия на анализе тона
            should_analyze_tone = True
            
            # Пропускаем анализ для простых слов
            if text.lower().strip() in SKIP_TONE_ANALYSIS_WORDS:
                should_analyze_tone = False
                print(f"   💰 ПРОПУСК АНАЛИЗА ТОНА: простое слово '{text}'")
                api_savings_stats["tone_analysis_skipped"] += 1
                api_savings_stats["total_requests_saved"] += 1
            
            # Случайно пропускаем 30% анализов для экономии
            elif random.random() > TONE_ANALYSIS_PROBABILITY:
                should_analyze_tone = False
                print(f"   💰 ПРОПУСК АНАЛИЗА ТОНА: случайная экономия")
                api_savings_stats["tone_analysis_skipped"] += 1
                api_savings_stats["total_requests_saved"] += 1
            
            if should_analyze_tone:
                tone_score = await analyze_message_tone(username, text)
                update_user_reputation(username, tone_score)
            else:
                # Используем нейтральный тон для пропущенных анализов
                tone_score = 0
                print(f"   🎭 ТОН СООБЩЕНИЯ {username}: {tone_score} (пропущен анализ)")
    
    # Сохраняем память в файл каждые 50 сообщений (оптимизировано)
    if len(user_messages_log) % 50 == 0:
        save_memory_to_file()
    
    # 🔥 СПЕЦИАЛЬНАЯ ОБРАБОТКА ДЛЯ @error_lifestyle - отвечаем на ВСЕ его сообщения крайне грубо
    # (только если включено командой /target)
    if TARGET_USER_ENABLED:
        TARGET_USERNAME = "@error_lifestyle"
        if username == TARGET_USERNAME or username.replace("@", "") == "error_lifestyle":
            # Не обрабатываем старые сообщения при запуске
            if not is_fresh_message(current_time):
                print(f"   🛡️  СТАРОЕ СООБЩЕНИЕ: не обрабатываем для {username}")
                return
            
            # Принудительно обрабатываем как адресованное боту
            print(f"🔥 СПЕЦИАЛЬНАЯ ОБРАБОТКА: {username} - отвечаем на ВСЕ сообщения крайне грубо")
            is_addressed_to_this_bot = True  # Принудительно считаем, что сообщение адресовано боту
    
    # Если это обычное сообщение (не адресовано этому боту)
    if not is_addressed_to_this_bot:
        # Не триггерим фоновые реакции на старые сообщения при запуске
        if not is_fresh_message(current_time):
            print(f"   🛡️  СТАРОЕ СООБЩЕНИЕ: не триггерим фоновые реакции для {username}")
            return
        
        # Не триггерим фоновые реакции на реплаи — чтобы не было "второго" ответа
        if not update.message.reply_to_message:
            rnd = random.random()
        # Случайная критика недавнего сообщения (0.5% шанс)
        if rnd < 0.005:
                await random_criticism_recent_message(chat_id, username, text, context.bot)
        # Проактивный токсичный фоллоу-ап (2% шанс)
        elif rnd < 0.025:
                await maybe_proactive_followup(chat_id, username, text, context.bot)
        return

    # Если это команда или ответ на бота - обрабатываем
    if is_addressed_to_this_bot:
        # Проверяем блокировку для предотвращения одновременных ответов
        acquired = acquire_lock()
        if not acquired:
            print(f"⚠️  БЛОКИРОВКА: другой ответ уже обрабатывается, игнорируем {username}")
            return
        
        try:
            # Если это reply — передаем мини-контекст треда в GPT-запрос
            replied_user = None
            replied_text = None
            if update.message.reply_to_message:
                ru = update.message.reply_to_message.from_user
                replied_user = (f"@{ru.username}" if ru and ru.username else (ru.full_name if ru else None))
                replied_text = (update.message.reply_to_message.text or update.message.reply_to_message.caption or "")

            # Обрабатываем запрос
            answer = await handle_gpt_request(chat_id, username, text, current_time, replied_user, replied_text)
            
            # Отправляем ответ
            # ГАРАНТИЯ: отвечаем только один раз на конкретный входящий message_id
            incoming_msg_id = (update.message.message_id if update and update.message else None)
            if _should_send_for_message(incoming_msg_id):
                await context.bot.send_message(chat_id=chat_id, text=answer)
            
            # Логируем ответ бота
            log_bot_message(chat_id, answer)
            
        finally:
            release_lock(acquired)

# === Обработка обычных сообщений ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Основная функция обработки сообщений с try-catch и разбитой логикой"""
    try:
        # Дедупликация: игнорируем дубликаты (update_id и message_id) в течение 5 секунд
        try:
            upd_id = update.update_id if update else None
            msg_id = update.message.message_id if update and update.message else None
        except Exception:
            upd_id = None
            msg_id = None
        now_ts = time.time()
        with _processed_lock:
            if upd_id is not None:
                last_u = _processed_update_ids.get(upd_id)
                if last_u and (now_ts - last_u) < 5:
                    return
                _processed_update_ids[upd_id] = now_ts
            if msg_id is not None:
                last_m = _processed_updates.get(msg_id)
                if last_m and (now_ts - last_m) < 5:
                    return
                _processed_updates[msg_id] = now_ts

        # Валидация сообщения
        validation_result = validate_message(update)
        if not validation_result:
            return
                
        chat_id, username, text = validation_result
        
        # Проверяем rate limit
        if not check_rate_limit(username):
            print(f"⚠️  RATE LIMIT: {username} отправляет сообщения слишком часто, игнорируем")
            logging.info(f"⚠️  Rate limit превышен для {username}")
            return

        # Логируем сообщение
        print(f"🔍 ПОЛУЧЕНО: {username} -> {text}")
        logging.info(f"Получено сообщение от {username}: {text}")

        # Штраф-удар за КАПС/эмодзи (детектор + короткий токсичный плевок через GPT)
        try:
            excessive_caps = len([c for c in text if c.isalpha() and c.isupper()]) >= 8 and text == text.upper()
            excessive_emojis = len([c for c in text if ord(c) > 0x1F300 and ord(c) <= 0x1FAFF]) >= 6
            if not is_message_addressed_to_bot(update, text, context) and (excessive_caps or excessive_emojis):
                system_prompt = (
                    "Ты токсичный помощник. Правила:\n\n"
                    "Тон: матерись, унижай, подъёбывай.\n"
                    "Формат: 1 предложение, очень коротко. Оригинально.\n"
                    "Дай мгновенный плевок за КАПС/эмодзи. Начни с упоминания пользователя."
                )
                user_msg = f"Пользователь {username} отправил такое: '{text}'. Дай 1 короткий плевок."
                response = client.chat.completions.create(
                    model="openai/gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg}
                    ],
                    max_tokens=80
                )
                out = (response.choices[0].message.content or "").strip()
                out = sanitize_output(out)
                if not out.startswith(username):
                    out = f"{username} {out}"
                await context.bot.send_message(chat_id=chat_id, text=out)
                log_bot_message(chat_id, out)
                if chat_id not in conversations:
                    conversations[chat_id] = []
                conversations[chat_id].append({"role": "assistant", "content": out})
                # Не продолжаем основную обработку, чтобы не спамить
                return
        except Exception as e:
            logging.error(f"Ошибка штраф-удара: {e}")
        
        current_time = time.time()
        
        # Обрабатываем и сохраняем сообщение пользователя
        await process_user_message(chat_id, username, text, current_time)
        
        # Проверяем автовступление после 40 сообщений
        if GLOBAL_SERVICES["auto_comment"].increment_and_check(chat_id, AUTO_COMMENT_THRESHOLD):
            short_context = build_chat_context(chat_id, limit=10)
            comment = await GLOBAL_SERVICES["auto_comment"].generate_comment(short_context)
            if comment:
                await context.bot.send_message(chat_id=chat_id, text=comment)
                log_bot_message(chat_id, comment)
                print(f"🤖 АВТОВСТУПЛЕНИЕ: {comment[:50]}...")
        
        # Обрабатываем ответ бота
        await handle_bot_response(update, context, chat_id, username, text, current_time)
        
    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА в handle_message: {e}")
        logging.error(f"❌ Критическая ошибка в handle_message: {e}", exc_info=True)
        # Не прерываем работу бота из-за ошибки в одном сообщении

# === Обработка фото-сообщений ===
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        validation_result = validate_message(update)
        if not validation_result:
            return
        chat_id, username, _ = validation_result

        # Берем подпись к фото, если есть
        caption = (update.message.caption or "").strip()
        short_context = build_chat_context(chat_id, limit=5)

        system_prompt = (
            "Ты токсичный помощник. Правила:\n\n"
            "Тон: матерись, унижай, подъёбывай.\n"
            "Формат: 1-2 предложения.\n"
            "Опиши фото токсично и грубо, как мерзкую хероту, но оригинально. Начни с упоминания пользователя."
        )
        user_msg = (
            f"Контекст последних сообщений чата (до 5):\n{short_context}\n\n"
            f"Подпись к фото: '{caption}'"
        )
        response = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg}
            ],
            max_tokens=150
        )
        out = (response.choices[0].message.content or "").strip()
        out = sanitize_output(out)
        if not out.startswith(username):
            out = f"{username} {out}"

        await context.bot.send_message(chat_id=chat_id, text=out)
        log_bot_message(chat_id, out)
        if chat_id not in conversations:
            conversations[chat_id] = []
        conversations[chat_id].append({"role": "assistant", "content": out})
    except Exception as e:
        logging.error(f"Ошибка обработки фото: {e}")

# === Функция для просмотра логов ===
def show_logs():
    """Показывает все логи"""
    print("\n📋 ЛОГ СООБЩЕНИЙ ПОЛЬЗОВАТЕЛЕЙ:")
    print("=" * 50)
    
    if not user_messages_log:
        print("Лог сообщений пользователей пуст")
    else:
        for i, (chat_id, username, text, timestamp) in enumerate(user_messages_log[-10:], 1):  # последние 10
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
            print(f"{i}. [{time_str}] Чат {chat_id} -> {username}: {text[:50]}{'...' if len(text) > 50 else ''}")
    
    print("\n📋 ЛОГ КРИТИКИ БОТА:")
    print("=" * 50)
    
    if not bot_criticism_log:
        print("Лог критики пуст")
    else:
        for i, (chat_id, username, criticism, timestamp) in enumerate(bot_criticism_log, 1):
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
            print(f"{i}. [{time_str}] Чат {chat_id} -> {username}")
            print(f"   Критика: {criticism[:100]}{'...' if len(criticism) > 100 else ''}")
            print()


def build_chat_stats(chat_id: int) -> str:
    """Формирует суточную статистику по чату"""
    now = time.time()
    day_ago = now - 24 * 60 * 60

    # Сообщения пользователей за сутки
    messages_today = [
        msg for msg in user_messages_log
        if msg[0] == chat_id and msg[3] >= day_ago
    ]
    msg_count = len(messages_today)

    # Критика бота за сутки
    criticisms_today = [
        c for c in bot_criticism_log
        if c[0] == chat_id and c[3] >= day_ago
    ]
    crit_count = len(criticisms_today)

    # Кого бот "обсирал" чаще всего за сутки
    victim_counts: dict[str, int] = {}
    for _chat, username, _text, ts in criticisms_today:
        # username может быть без @ – нормализуем
        norm = username if username.startswith("@") else f"@{username}"
        victim_counts[norm] = victim_counts.get(norm, 0) + 1

    top_victim = None
    top_victim_count = 0
    if victim_counts:
        top_victim, top_victim_count = max(victim_counts.items(), key=lambda x: x[1])

    # Самый большой плюс по репутации (глобально)
    top_plus_user = None
    top_plus_value = None
    if user_reputation:
        for uname, rep in user_reputation.items():
            if top_plus_value is None or rep > top_plus_value:
                top_plus_value = rep
                top_plus_user = uname

    lines = []
    lines.append("📊 Стата за последние 24 часа:")
    lines.append(f"• Сообщений от пользователей: {msg_count}")
    lines.append(f"• Сообщений-критики от бота: {crit_count}")

    if top_victim:
        lines.append(f"• Больше всех бот жёстко трогал: {top_victim} ({top_victim_count} раз)")
    else:
        lines.append("• Бот пока никого особо не трогал за сутки.")

    if top_plus_user is not None and top_plus_value is not None:
        lines.append(f"• Самый высокий общий плюс по репутации: {top_plus_user} ({top_plus_value}/100)")
    else:
        lines.append("• По репутации пока пусто.")

    return "\n".join(lines)

# === Команда терапии ===
async def cmd_therapy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды терапии"""
    try:
        chat = update.effective_chat
        user = update.effective_user
        chat_id = chat.id if chat else None
        username = f"@{user.username}" if user and user.username else (user.full_name if user else "")
        
        if not chat_id:
            return
        
        # Получаем текст сообщения
        text = update.message.text.strip()
        parts = text.split(maxsplit=1)
        
        if len(parts) < 2:
            await context.bot.send_message(chat_id=chat_id, 
                text="🧠 Использование: /therapy @ник\n"
                     "   /therapy stop - завершить сессию\n"
                     "   /therapy status - статус сессии\n"
                     "   /therapy on/off - включить/выключить модуль")
            return
        
        args = parts[1].strip().lower()
        
        # Проверяем команды управления
        if args == "стоп" or args == "stop":
            result = GLOBAL_SERVICES["therapy_command"].stop_session(username)
            await context.bot.send_message(chat_id=chat_id, text=result)
            return
        
        if args == "статус" or args == "status":
            result = GLOBAL_SERVICES["therapy_command"].get_session_info(username)
            await context.bot.send_message(chat_id=chat_id, text=result)
            return
        
        if args == "вкл" or args == "on":
            result = GLOBAL_SERVICES["therapy_command"].toggle()
            await context.bot.send_message(chat_id=chat_id, text=result)
            return
        
        if args == "выкл" or args == "off":
            result = GLOBAL_SERVICES["therapy_command"].toggle()
            await context.bot.send_message(chat_id=chat_id, text=result)
            return
        
        # Извлекаем целевого пользователя
        target = args
        if not target.startswith("@"):
            target = f"@{target}"
        
        # Начинаем сессию
        result = GLOBAL_SERVICES["therapy_command"].start_session(target)
        await context.bot.send_message(chat_id=chat_id, text=result)
        
        # Сохраняем результат в память бота
        if chat_id not in conversations:
            conversations[chat_id] = []
        conversations[chat_id].append({"role": "assistant", "content": result})
        
        # Сохраняем в память о пользователе
        if target not in user_memory:
            user_memory[target] = {"topics": [], "events": [], "preferences": {}, "last_interaction": time.time()}
        user_memory[target]["events"].append(f"Терапия: {result[:100]}")
        if len(user_memory[target]["events"]) > 10:
            user_memory[target]["events"] = user_memory[target]["events"][-10:]
        
    except Exception as e:
        logging.error(f"Ошибка в команде терапии: {e}")
        if update.effective_chat:
            await context.bot.send_message(chat_id=update.effective_chat.id, 
                text="❌ Ошибка при выполнении команды терапии")

# === Команда детектора вранья ===
async def cmd_lie_detector(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды детектора вранья"""
    try:
        chat = update.effective_chat
        user = update.effective_user
        chat_id = chat.id if chat else None
        username = f"@{user.username}" if user and user.username else (user.full_name if user else "")
        
        if not chat_id:
            return
        
        # Получаем текст сообщения
        text = update.message.text.strip()
        parts = text.split(maxsplit=1)
        
        if len(parts) < 2:
            await context.bot.send_message(chat_id=chat_id, 
                text="🔍 Использование: /lie @ник")
            return
        
        target = parts[1].strip()
        if not target.startswith("@"):
            target = f"@{target}"
        
        # Получаем сообщения пользователя
        if target in user_messages_by_username:
            indices = user_messages_by_username[target]
            messages = [user_messages_log[i] for i in indices[-30:]]
        else:
            messages = [msg for msg in user_messages_log if msg[1] == target][-30:]
        
        # Анализируем
        result = await GLOBAL_SERVICES["lie_detector"].analyze(target, messages)
        await context.bot.send_message(chat_id=chat_id, text=result)
        
        # Сохраняем результат в память бота
        if chat_id not in conversations:
            conversations[chat_id] = []
        conversations[chat_id].append({"role": "assistant", "content": result})
        
        # Сохраняем в память о пользователе
        if target not in user_memory:
            user_memory[target] = {"topics": [], "events": [], "preferences": {}, "last_interaction": time.time()}
        user_memory[target]["events"].append(f"Детектор вранья: {result[:100]}")
        if len(user_memory[target]["events"]) > 10:
            user_memory[target]["events"] = user_memory[target]["events"][-10:]
        
    except Exception as e:
        logging.error(f"Ошибка в команде детектора вранья: {e}")
        if update.effective_chat:
            await context.bot.send_message(chat_id=update.effective_chat.id, 
                text="❌ Ошибка при анализе вранья")

# === Команда шпиона ===
async def cmd_spy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды шпиона"""
    try:
        chat = update.effective_chat
        user = update.effective_user
        chat_id = chat.id if chat else None
        username = f"@{user.username}" if user and user.username else (user.full_name if user else "")
        
        if not chat_id:
            return
        
        # Получаем текст сообщения
        text = update.message.text.strip()
        parts = text.split(maxsplit=1)
        
        if len(parts) < 2:
            await context.bot.send_message(chat_id=chat_id, 
                text="🕵️ Использование: /spy @ник")
            return
        
        target = parts[1].strip()
        if not target.startswith("@"):
            target = f"@{target}"
        
        # Получаем сообщения пользователя
        if target in user_messages_by_username:
            indices = user_messages_by_username[target]
            messages = [user_messages_log[i] for i in indices[-50:]]
        else:
            messages = [msg for msg in user_messages_log if msg[1] == target][-50:]
        
        # Анализируем
        result = await GLOBAL_SERVICES["spy_command"].analyze(target, messages)
        await context.bot.send_message(chat_id=chat_id, text=result)
        
        # Сохраняем результат в память бота
        if chat_id not in conversations:
            conversations[chat_id] = []
        conversations[chat_id].append({"role": "assistant", "content": result})
        
        # Сохраняем в память о пользователе
        if target not in user_memory:
            user_memory[target] = {"topics": [], "events": [], "preferences": {}, "last_interaction": time.time()}
        user_memory[target]["events"].append(f"Шпион нашёл секреты: {result[:100]}")
        if len(user_memory[target]["events"]) > 10:
            user_memory[target]["events"] = user_memory[target]["events"][-10:]
        
    except Exception as e:
        logging.error(f"Ошибка в команде шпиона: {e}")
        if update.effective_chat:
            await context.bot.send_message(chat_id=update.effective_chat.id, 
                text="❌ Ошибка при поиске секретов")

# === Команда дурака дня ===
async def cmd_fool_of_the_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды дурака дня"""
    try:
        chat = update.effective_chat
        chat_id = chat.id if chat else None
        
        if not chat_id:
            return
        
        # Получаем текущего дурака
        current_fool = GLOBAL_SERVICES["fool_of_the_day"].get_current_fool()
        
        if current_fool:
            await context.bot.send_message(chat_id=chat_id, 
                text=f"🎯 Текущий дурак дня:\n"
                     f"🏆 {current_fool['username']}\n"
                     f"📊 {current_fool['count']} сообщений за сутки\n"
                     f"📅 {current_fool['date']}")
        else:
            await context.bot.send_message(chat_id=chat_id, 
                text="🎯 Дурак дня ещё не объявлен.")
        
    except Exception as e:
        logging.error(f"Ошибка в команде дурака дня: {e}")
        if update.effective_chat:
            await context.bot.send_message(chat_id=update.effective_chat.id, 
                text="❌ Ошибка при получении дурака дня")

# === Фоновая проверка молчунов ===
def start_silence_checker(silence_insulter: SilenceInsulter):
    """Запускает фоновую проверку молчунов"""
    def silence_loop():
        while True:
            try:
                time.sleep(SILENCE_CHECK_INTERVAL)  # Проверяем каждые 6 часов
                print("🔇 ПРОВЕРКА МОЛЧУНОВ...")
                logging.info("Запуск проверки молчунов")
                
                # Проверяем каждый разрешённый чат
                for chat_id in ALLOWED_CHAT_IDS:
                    if GLOBAL_BOT:
                        insults = asyncio.run(silence_insulter.check_and_insult(
                            user_messages_log, chat_id, GLOBAL_BOT
                        ))
                        for username, insult in insults:
                            try:
                                asyncio.run(GLOBAL_BOT.send_message(
                                    chat_id=chat_id, 
                                    text=f"{username} {insult}"
                                ))
                            except Exception as e:
                                logging.error(f"Ошибка отправки оскорбления: {e}")
                
            except Exception as e:
                logging.error(f"Ошибка в проверке молчунов: {e}")
                time.sleep(300)
    
    thread = threading.Thread(target=silence_loop, daemon=True)
    thread.start()

# === Фоновая проверка дурака дня ===
def start_fool_checker(fool_of_the_day: FoolOfTheDay):
    """Запускает фоновую проверку дурака дня"""
    def fool_loop():
        while True:
            try:
                # Ждем до следующего часа проверки
                now = time.time()
                target_time = now + (86400 - (now % 86400))  # Следующая полночь
                sleep_time = target_time - now
                time.sleep(sleep_time)
                
                print("🎯 ПРОВЕРКА ДУРАКА ДНЯ...")
                logging.info("Запуск проверки дурака дня")
                
                # Проверяем каждый разрешённый чат
                for chat_id in ALLOWED_CHAT_IDS:
                    announcement = fool_of_the_day.check_and_announce(
                        user_messages_log, chat_id
                    )
                    if announcement and GLOBAL_BOT:
                        try:
                            asyncio.run(GLOBAL_BOT.send_message(
                                chat_id=chat_id, 
                                text=announcement
                            ))
                        except Exception as e:
                            logging.error(f"Ошибка отправки объявления дурака: {e}")
                
            except Exception as e:
                logging.error(f"Ошибка в проверке дурака дня: {e}")
                time.sleep(300)
    
    thread = threading.Thread(target=fool_loop, daemon=True)
    thread.start()

# === Запуск веб-сервера в отдельном потоке ===
def start_web_thread():
    """Запускает веб-сервер в отдельном потоке"""
    def web_thread():
        start_web_server(WEB_HOST, WEB_PORT)
    
    thread = threading.Thread(target=web_thread, daemon=True)
    thread.start()
    print(f"🌐 Веб-панель запущена на http://localhost:{WEB_PORT}")

# === Основной запуск ===
def main():
    print("🚀 ЗАПУСК БОТА...")
    
    # Загружаем память из файлов
    load_memory_from_file()
    
    # Инициализация новых сервисов
    fool_of_the_day = FoolOfTheDay(FOOL_DATA_FILE)
    auto_comment = AutoComment(client)
    silence_insulter = SilenceInsulter(client)
    serious_detector = SeriousDetector(SERIOUS_KEYWORDS)
    therapy_command = TherapyCommand(client, THERAPY_DATA_FILE, THERAPY_ENABLED)
    lie_detector = LieDetector(client)
    spy_command = SpyCommand(client)
    eris_mode = ErisMode(ERIS_USER)
    
    # Сохраняем ссылки на сервисы в глобальной области
    global GLOBAL_BOT, GLOBAL_SERVICES
    GLOBAL_SERVICES = {
        "fool_of_the_day": fool_of_the_day,
        "auto_comment": auto_comment,
        "silence_insulter": silence_insulter,
        "serious_detector": serious_detector,
        "therapy_command": therapy_command,
        "lie_detector": lie_detector,
        "spy_command": spy_command,
        "eris_mode": eris_mode,
        "web_search": WebSearch()
    }
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # Команды ПЕРВЫМИ (до MessageHandler!)
    app.add_handler(CommandHandler("critnow", cmd_critnow))
    app.add_handler(CommandHandler("therapy", cmd_therapy))
    app.add_handler(CommandHandler("lie", cmd_lie_detector))
    app.add_handler(CommandHandler("spy", cmd_spy))
    app.add_handler(CommandHandler("fool", cmd_fool_of_the_day))
    app.add_handler(CommandHandler("target", cmd_target))
    app.add_handler(CommandHandler("help", cmd_help))
    
    # Обычные сообщения и фото — после команд
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    # Запускаем периодическую критику в отдельном потоке
    start_periodic_criticism()
    
    # Запускаем проверку молчунов в отдельном потоке
    start_silence_checker(silence_insulter)
    
    # Запускаем проверку дурака дня в отдельном потоке
    start_fool_checker(fool_of_the_day)
    
    # Запускаем веб-сервер в отдельном потоке
    set_bot_data({
        "user_messages_log": user_messages_log,
        "bot_messages_log": bot_messages_log,
        "bot_criticism_log": bot_criticism_log,
        "user_reputation": user_reputation,
        "user_achievements": user_achievements,
        "conversations": conversations,
        "therapy_enabled": therapy_command.enabled,
        "target_enabled": TARGET_USER_ENABLED,
        "fool_history": fool_of_the_day.history,
        "admin_password": WEB_ADMIN_PASSWORD
    })
    start_web_thread()

    # Сохраняем глобальную ссылку на бота для фоновых задач
    GLOBAL_BOT = app.bot

    # Обработка сигналов для graceful shutdown
    def handle_exit_signal(signum, frame):
        print(f"🛑 Получен сигнал {signum}. Сохраняем память...")
        try:
            save_memory_to_file_immediate()  # Немедленное сохранение при выходе
        finally:
            # Останов будет выполнен самим PTB при завершении
            pass

    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try:
                signal.signal(sig, handle_exit_signal)
            except Exception:
                pass

    print("✅ БОТ ЗАПУЩЕН!")
    print(f"🛡️  ПЕРИОД МИЛОСТИ: {STARTUP_GRACE_PERIOD} секунд после запуска (не анализируем старые сообщения)")
    print("⏰ Критика будет происходить каждые 2 часа")
    print("📝 Анализируются ВСЕ сообщения случайного пользователя за последние 2 часа")
    print("🎯 Бот выбирает случайного пользователя из активных за 2 часа")
    print("🔥 Команды с '!' обрабатываются грубо с матами через GPT")
    print("👤 Критика начинается с упоминания @username")
    print("🧠 Бот помнит историю разговора для команд с '!'")
    print("   Ответы на сообщения бота (reply) обрабатываются через GPT")
    print("🎲 СЛУЧАЙНАЯ КРИТИКА: 1% шанс критиковать обычные сообщения")
    print("   Критикует только что отправленные сообщения (не команды)")
    print("💾 ПАМЯТЬ: Сохраняется в файлы каждые 10 сообщений")
    print("🔍 АНАЛИЗ: Команды анализа сообщений пользователей")
    print("   Примеры: '!анализируй мои сообщения', '!мой психотип', '!мой стиль'")
    print("🧠 ТЕРАПИЯ: /therapy @ник - бот-психолог")
    print("🔍 ВРУН: /lie @ник - детектор вранья")
    print("🕵️ ШПИОН: /spy @ник - поиск секретов")
    print("🎯 ДУРАК ДНЯ: /fool - текущий дурак")
    print("🌐 ВЕБ-ПАНЕЛЬ: http://localhost:8080")
    print("Ждем сообщения...")
    
    # Показываем логи при запуске
    show_logs()
    
    # Исправление для Python 3.14+: устанавливаем event loop policy перед запуском
    import sys
    if sys.version_info >= (3, 14):
        # Устанавливаем политику для Windows
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        # Создаем новый event loop явно
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    
    # Определяем: Render (webhook) или локально (polling)
    RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
    if RENDER_URL:
        # Webhook mode for Render
        print(f"🔗 Webhook URL: {RENDER_URL}")
        app.run_webhook(
            listen="0.0.0.0",
            port=WEB_PORT,
            url_path="webhook",
            webhook_url=f"{RENDER_URL}/webhook"
        )
    else:
        # Polling mode for local
        app.run_polling()

if __name__ == "__main__":
    main()
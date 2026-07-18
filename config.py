import os
from pathlib import Path

# === Пути ===
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# === Токены (из .env или переменных окружения) ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

# === Разрешённые чаты ===
ALLOWED_CHAT_IDS = [-1002868313903]

# === Режим безопасного вывода ===
SAFE_MODE = False

# === Админы ===
ADMIN_USERNAMES = {"admin", "root"}

# === Лимиты ===
MAX_USER_MESSAGES = 10000
MAX_BOT_MESSAGES = 5000
MAX_CRITICISM_LOG = 2000
MAX_CONTEXT_MESSAGES = 12
MAX_CONTEXT_LENGTH = 3000

# === Rate Limiting ===
RATE_LIMIT_ENABLED = False
RATE_LIMIT_SECONDS = 0
MAX_RATE_LIMIT_USERS = 1000

# === Кэширование ===
CACHE_EXPIRY_SECONDS = 3600
MAX_CACHE_SIZE = 1000
TONE_CACHE_EXPIRY_SECONDS = 1800
MAX_TONE_CACHE_SIZE = 500

# === Сохранение ===
SAVE_INTERVAL_SECONDS = 30
MEMORY_SAVE_INTERVAL = 50

# === Период милости ===
STARTUP_GRACE_PERIOD = 300

# === Автовступление ===
AUTO_COMMENT_THRESHOLD = 40

# === Молчуны ===
SILENCE_INSULT_HOURS = 24
SILENCE_CHECK_INTERVAL = 6 * 3600

# === Дурак дня ===
FOOL_CHECK_HOUR = 0
FOOL_DATA_FILE = DATA_DIR / "fool_history.json"

# === Терапия ===
THERAPY_DATA_FILE = DATA_DIR / "therapy_sessions.json"
THERAPY_ENABLED = True

# === Специальный пользователь ===
ERIS_USER = "@Nooxas"
TARGET_USER_ID = 5631862253

# === Файлы данных ===
CONVERSATIONS_FILE = BASE_DIR / "conversations.json"
USER_MESSAGES_FILE = BASE_DIR / "user_messages.json"
BOT_CRITICISM_FILE = BASE_DIR / "bot_criticism.json"
BOT_MESSAGES_FILE = BASE_DIR / "bot_messages.json"
REPUTATION_FILE = BASE_DIR / "user_reputation.json"
ACHIEVEMENTS_FILE = BASE_DIR / "user_achievements.json"
MEMORY_FILE = BASE_DIR / "user_memory.json"
PERSONALITY_FILE = BASE_DIR / "bot_personality.json"

# === Web UI ===
WEB_HOST = "0.0.0.0"
WEB_PORT = int(os.getenv("PORT", "8080"))
WEB_ADMIN_PASSWORD = os.getenv("WEB_ADMIN_PASSWORD", "admin123")

# === Длина ответов ===
RESPONSE_LENGTHS = {
    "short": {"max_tokens": 80, "description": "очень короткий"},
    "medium": {"max_tokens": 150, "description": "короткий"},
    "long": {"max_tokens": 250, "description": "средний"}
}

# === Пропуск анализа тона ===
SKIP_TONE_ANALYSIS_WORDS = ["да", "нет", "ок", "ага", "угу", "спасибо", "привет", "пока", "1", "2", "3", "4", "5"]
TONE_ANALYSIS_PROBABILITY = 0.7

# === Статистика экономии ===
API_SAVINGS_STATS = {
    "gpt_cache_hits": 0,
    "tone_cache_hits": 0,
    "tone_analysis_skipped": 0,
    "total_requests_saved": 0
}

# === Ачивки ===
ACHIEVEMENTS_INFO = {
    "rep_minus_50": {
        "title": "🧨 Король токсика",
        "description": "Репутация упала до -50 или ниже."
    },
    "rep_plus_20": {
        "title": "😇 Слишком добрый для этого чата",
        "description": "Репутация поднялась до +20 или выше."
    }
}

# === Серьёзные вопросы (детектор) ===
SERIOUS_KEYWORDS = [
    "помоги", "совет", "проблема", "как сделать", "почему", "что значит",
    "объясни", "расскажи", "научи", "подскажи", "что такое", "как работает",
    "как исправить", "ошибка", "не работает", "сломалось", "баг"
]

# === Стили критики ===
CRITICISM_STYLES = [
    "Ты едкий и беспощадный критик. Критикуй это сообщение коротко и токсично, с матами. 1-2 предложения.",
    "Ты агрессивный наблюдатель. Разнеси это сообщение подробно, с матами. 2-3 предложения.",
    "Ты токсичный критик. Высмеяй это сообщение с матами и сарказмом. 1-2 предложения."
]

# === DuckDuckGo поиск ===
SEARCH_ENABLED = True
SEARCH_MAX_RESULTS = 3

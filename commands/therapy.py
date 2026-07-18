import json
import time
import logging
from pathlib import Path
from openai import OpenAI

logger = logging.getLogger(__name__)

class TherapySession:
    """Сессия терапии для конкретного пользователя"""
    
    def __init__(self, username: str):
        self.username = username
        self.start_time = time.time()
        self.messages = []
        self.mood = "neutral"
    
    def add_message(self, role: str, content: str):
        """Добавляет сообщение в сессию"""
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": time.time()
        })
    
    def get_context(self) -> list:
        """Возвращает контекст для GPT"""
        return [{"role": msg["role"], "content": msg["content"]} 
                for msg in self.messages[-10:]]  # последние 10 сообщений
    
    def to_dict(self) -> dict:
        """Конвертирует в словарь"""
        return {
            "username": self.username,
            "start_time": self.start_time,
            "messages": self.messages,
            "mood": self.mood
        }


class TherapyCommand:
    """Команда терапии - бот-психолог который лечит матами"""
    
    def __init__(self, client: OpenAI, data_file: Path, enabled: bool = True):
        self.client = client
        self.data_file = data_file
        self.enabled = enabled
        self.sessions = {}  # {username: TherapySession}
        self._load_sessions()
    
    def _load_sessions(self):
        """Загружает сессии из файла"""
        try:
            if self.data_file.exists():
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for username, session_data in data.get("sessions", {}).items():
                        session = TherapySession(username)
                        session.start_time = session_data.get("start_time", 0)
                        session.messages = session_data.get("messages", [])
                        session.mood = session_data.get("mood", "neutral")
                        self.sessions[username] = session
        except Exception as e:
            logger.error(f"Ошибка загрузки сессий терапии: {e}")
    
    def _save_sessions(self):
        """Сохраняет сессии в файл"""
        try:
            data = {
                "sessions": {username: session.to_dict() 
                            for username, session in self.sessions.items()}
            }
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения сессий терапии: {e}")
    
    def start_session(self, username: str) -> str:
        """Начинает новую сессию терапии"""
        if not self.enabled:
            return "Модуль терапии отключен. Админ может включить через /therapy toggle"
        
        self.sessions[username] = TherapySession(username)
        self._save_sessions()
        
        return (
            f"🧠 *ТЕРАПИЯ НАЧАТА* для {username}\n\n"
            f"Ты попал к токсичному психологу. Я слушаю твои проблемы и отвечаю с матами.\n"
            f"Просто пиши свои проблемы, я буду отвечать.\n"
            f"Чтобы закончить: !терапия стоп"
        )
    
    def stop_session(self, username: str) -> str:
        """Завершает сессию терапии"""
        if username not in self.sessions:
            return f"{username}, у тебя нет активной сессии терапии."
        
        session = self.sessions[username]
        duration = int((time.time() - session.start_time) / 60)
        message_count = len([m for m in session.messages if m["role"] == "user"])
        
        del self.sessions[username]
        self._save_sessions()
        
        return (
            f"🧠 *ТЕРАПИЯ ЗАВЕРШЕНА* для {username}\n"
            f"⏱ Длительность: {duration} минут\n"
            f"📝 Сообщений: {message_count}\n"
            f"💡 Надеюсь, тебе стало лучше (или хотя бы смешнее)."
        )
    
    def toggle(self) -> str:
        """Включает/выключает модуль терапии"""
        self.enabled = not self.enabled
        status = "включен" if self.enabled else "выключен"
        return f"Модуль терапии {status}"
    
    def is_active(self, username: str) -> bool:
        """Проверяет, активна ли сессия для пользователя"""
        return username in self.sessions
    
    async def process_message(self, username: str, text: str) -> str:
        """Обрабатывает сообщение в рамках терапии"""
        if not self.enabled:
            return None
        
        if username not in self.sessions:
            return None
        
        session = self.sessions[username]
        session.add_message("user", text)
        
        # Генерируем ответ
        system_prompt = (
            "Ты токсичный психолог. Твоя задача - 'лечить' пациентов матами и оскорблениями.\n"
            "Правила:\n"
            "1. Слушай проблему пациента\n"
            "2. Отвечай с матами, но по существу\n"
            "3. Давай советы, но в токсичной форме\n"
            "4. Используй юмор и сарказм\n"
            "5. Будь грубым, но не слишком жестоким\n"
            "6. Формат: 2-4 предложения\n"
            "7. Начни с упоминания пациента"
        )
        
        context = session.get_context()
        
        try:
            response = self.client.chat.completions.create(
                model="openai/gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    *context
                ],
                max_tokens=300
            )
            answer = (response.choices[0].message.content or "").strip()
            session.add_message("assistant", answer)
            self._save_sessions()
            return f"🧠 *ТЕРАПЕВТ:* {answer}"
        except Exception as e:
            logger.error(f"Ошибка терапии: {e}")
            return f"🧠 Ошибка терапии: {e}"
    
    def get_session_info(self, username: str) -> str:
        """Возвращает информацию о сессии"""
        if username not in self.sessions:
            return f"{username}, у тебя нет активной сессии."
        
        session = self.sessions[username]
        duration = int((time.time() - session.start_time) / 60)
        message_count = len([m for m in session.messages if m["role"] == "user"])
        
        return (
            f"🧠 *Сессия терапии* для {username}\n"
            f"⏱ Начало: {time.strftime('%H:%M', time.localtime(session.start_time))}\n"
            f"⏱ Длительность: {duration} минут\n"
            f"📝 Сообщений: {message_count}"
        )

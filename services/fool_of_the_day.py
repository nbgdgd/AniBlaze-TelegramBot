import json
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

class FoolOfTheDay:
    """Сервис 'Дурак дня' - автоматически выбирает самого активного дурака за сутки"""
    
    def __init__(self, data_file: Path):
        self.data_file = data_file
        self.history = self._load_history()
        self.last_announcement = self.history.get("last_announcement", 0)
    
    def _load_history(self) -> dict:
        """Загружает историю дураков из файла"""
        try:
            if self.data_file.exists():
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Ошибка загрузки истории дураков: {e}")
        return {"fools": [], "last_announcement": 0}
    
    def _save_history(self):
        """Сохраняет историю дураков в файл"""
        try:
            with self.data_file.open('w', encoding='utf-8') as f:
                json.dump(self.history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения истории дураков: {e}")
    
    def check_and_announce(self, user_messages_log: list, chat_id: int, bot=None) -> str | None:
        """Проверяет и объявляет дурака дня (если прошли сутки)"""
        now = time.time()
        
        # Проверяем, прошли ли сутки с последнего объявления
        if now - self.last_announcement < 86400:  # 24 часа
            return None
        
        # Находим самого активного за последние 24 часа
        day_ago = now - 86400
        recent_messages = [
            msg for msg in user_messages_log
            if msg[0] == chat_id and msg[3] >= day_ago
        ]
        
        if not recent_messages:
            return None
        
        # Подсчитываем сообщения по пользователям
        user_counts = {}
        for msg in recent_messages:
            username = msg[1]
            if username not in user_counts:
                user_counts[username] = 0
            user_counts[username] += 1
        
        if not user_counts:
            return None
        
        # Находим самого активного
        fool_username = max(user_counts, key=user_counts.get)
        fool_count = user_counts[fool_username]
        
        # Формируем сообщение
        announcement = (
            f"🏆 ДУРАК ДНЯ: {fool_username}\n"
            f"📊 {fool_count} сообщений за сутки\n"
            f"🎯 Ты так много писал, что даже бот тебя заметил"
        )
        
        # Сохраняем в историю
        self.history["fools"].append({
            "username": fool_username,
            "count": fool_count,
            "timestamp": now,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M")
        })
        self.history["last_announcement"] = now
        self.last_announcement = now
        
        # Ограничиваем историю (последние 30 дней)
        if len(self.history["fools"]) > 30:
            self.history["fools"] = self.history["fools"][-30:]
        
        self._save_history()
        
        logger.info(f"Объявлен дурак дня: {fool_username} ({fool_count} сообщений)")
        return announcement
    
    def get_current_fool(self) -> dict | None:
        """Возвращает текущего дурака дня (из последнего объявления)"""
        if not self.history["fools"]:
            return None
        return self.history["fools"][-1]
    
    def get_history(self, limit: int = 7) -> list:
        """Возвращает историю дураков за последние N дней"""
        return self.history["fools"][-limit:]

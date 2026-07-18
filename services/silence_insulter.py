import time
import random
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)

class SilenceInsulter:
    """Сервис оскорбления молчунов - оскорбляет тех, кто не пишет более суток"""
    
    def __init__(self, client: OpenAI):
        self.client = client
        self.last_insult_time = {}  # {username: timestamp}
        self.insult_cooldown = 86400  # не оскорблять повторно в течение 24 часов
    
    def should_insult(self, username: str) -> bool:
        """Проверяет, нужно ли оскорблять пользователя"""
        now = time.time()
        last_insult = self.last_insult_time.get(username, 0)
        
        if now - last_insult < self.insult_cooldown:
            return False
        
        return True
    
    def mark_insulted(self, username: str):
        """Отмечает, что пользователь был оскорблен"""
        self.last_insult_time[username] = time.time()
    
    async def generate_insult(self, username: str, last_message_time: float = None) -> str:
        """Генерирует оскорбление для молчуна"""
        hours_silent = 0
        if last_message_time:
            hours_silent = int((time.time() - last_message_time) / 3600)
        
        system_prompt = (
            "Ты токсичный помощник, который оскорбляет тех, кто давно не писал.\n"
            "Формат: 1-2 предложения. Матерись, унижай.\n"
            "Начни с упоминания пользователя.\n"
            "Спроси где он был, почему молчит, обосрался ли."
        )
        
        time_context = f"Последнее сообщение было {hours_silent} часов назад" if hours_silent else "Давно не писал"
        
        user_msg = (
            f"Пользователь {username} не пишет в чате.\n"
            f"{time_context}.\n"
            f"Оскорбь его за молчание."
        )
        
        try:
            response = self.client.chat.completions.create(
                model="openai/gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg}
                ],
                max_tokens=150
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as e:
            logger.error(f"Ошибка генерации оскорбления: {e}")
            return None
    
    async def check_and_insult(self, user_messages_log: list, chat_id: int, bot=None) -> list:
        """Проверяет молчунов и оскорбляет их"""
        now = time.time()
        day_ago = now - 86400
        
        # Находим всех уникальных пользователей в чате
        all_users = set()
        for msg in user_messages_log:
            if msg[0] == chat_id:
                all_users.add(msg[1])
        
        # Находим последнее сообщение каждого пользователя
        last_message_times = {}
        for msg in user_messages_log:
            if msg[0] == chat_id:
                username = msg[1]
                if username not in last_message_times or msg[3] > last_message_times[username]:
                    last_message_times[username] = msg[3]
        
        # Оскорбляем тех, кто молчит более суток
        insults = []
        for username in all_users:
            last_time = last_message_times.get(username, 0)
            
            # Пропускаем если пользователь писал недавно
            if last_time >= day_ago:
                continue
            
            # Пропускаем если уже оскорбляли
            if not self.should_insult(username):
                continue
            
            # Пропускаем ботов
            if username.lower().startswith("bot"):
                continue
            
            insult = await self.generate_insult(username, last_time)
            if insult:
                self.mark_insulted(username)
                insults.append((username, insult))
                logger.info(f"Оскорблен молчун: {username}")
        
        return insults

import random
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)

class AutoComment:
    """Сервис автовступления после N сообщений"""
    
    def __init__(self, client: OpenAI):
        self.client = client
        self.message_counter = {}  # {chat_id: count}
    
    def increment_and_check(self, chat_id: int, threshold: int = 40) -> bool:
        """Увеличивает счётчик и проверяет, пора ли вступать"""
        if chat_id not in self.message_counter:
            self.message_counter[chat_id] = 0
        
        self.message_counter[chat_id] += 1
        
        if self.message_counter[chat_id] >= threshold:
            self.message_counter[chat_id] = 0
            return True
        return False
    
    def reset_counter(self, chat_id: int):
        """Сбрасывает счётчик для чата"""
        self.message_counter[chat_id] = 0
    
    async def generate_comment(self, chat_context: str, bot_username: str = "Бот") -> str:
        """Генерирует токсичный комментарий к контексту чата"""
        system_prompt = (
            "Ты токсичный помощник, который вмешивается в разговор после долгого молчания.\n"
            "Формат: 2-3 предложения. Матерись, унижай, подъёбывай.\n"
            "Начни с упоминания кого-то из чата (случайно выбери).\n"
            "Комментируй общую тему разговора токсично."
        )
        
        user_msg = (
            f"Контекст последних сообщений чата:\n{chat_context}\n\n"
            f"Вмешайся в разговор после 40 сообщений молчания. Будь токсичным."
        )
        
        try:
            response = self.client.chat.completions.create(
                model="openai/gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg}
                ],
                max_tokens=200
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as e:
            logger.error(f"Ошибка генерации автокомментария: {e}")
            return None

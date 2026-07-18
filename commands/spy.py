import random
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)

class SpyCommand:
    """Шпион - бот ворует 'секреты' из сообщений пользователя"""
    
    def __init__(self, client: OpenAI):
        self.client = client
    
    async def analyze(self, username: str, messages: list) -> str:
        """Анализирует сообщения и находит 'секреты'"""
        if not messages:
            return f"🔍 У {username} нет сообщений для анализа."
        
        # Берём последние 50 сообщений
        recent_messages = messages[-50:] if len(messages) > 50 else messages
        message_texts = [msg[2] for msg in recent_messages]
        
        system_prompt = (
            "Ты шпион, который ищет секреты в сообщениях людей.\n"
            "Ищи:\n"
            "1. Личную информацию (имена, адреса, телефоны)\n"
            "2. Тайны и интриги\n"
            "3. Намёки на что-то скрытое\n"
            "4. Противоречия в словах\n"
            "5. Секретные планы\n"
            "6. Личные предпочтения\n"
            "7. Слабости и зависимости\n\n"
            "Формат ответа:\n"
            "🕵️ СЕКРЕТ #1: [секрет]\n"
            "🕵️ СЕКРЕТ #2: [секрет]\n"
            "🕵️ СЕКРЕТ #3: [секрет]\n\n"
            "Если секретов нет, напиши: 'У {username} нет секретов (или он хорошо их прячет).'"
        )
        
        user_msg = (
            f"Найди секреты в последних {len(message_texts)} сообщениях пользователя {username}.\n"
            f"Сообщения: {message_texts}"
        )
        
        try:
            response = self.client.chat.completions.create(
                model="openai/gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg}
                ],
                max_tokens=400
            )
            analysis = (response.choices[0].message.content or "").strip()
            return f"🕵️ *ШПИОНСКИЙ ОТЧЁТ* для {username}\n\n{analysis}"
        except Exception as e:
            logger.error(f"Ошибка шпиона: {e}")
            return f"🕵️ Ошибка шпиона: {e}"

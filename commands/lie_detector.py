import logging
from openai import OpenAI

logger = logging.getLogger(__name__)

class LieDetector:
    """Детектор вранья - анализирует паттерны лжи в сообщениях"""
    
    def __init__(self, client: OpenAI):
        self.client = client
    
    async def analyze(self, username: str, messages: list) -> str:
        """Анализирует последние 30 сообщений пользователя на предмет лжи"""
        if not messages:
            return f"🔍 У {username} нет сообщений для анализа."
        
        # Берём последние 30 сообщений
        recent_messages = messages[-30:] if len(messages) > 30 else messages
        message_texts = [msg[2] for msg in recent_messages]  # текст сообщения
        
        system_prompt = (
            "Ты детектор лжи. Анализируй сообщения пользователя и ищи паттерны лжи.\n"
            "Ищи:\n"
            "1. Противоречия между сообщениями\n"
            "2. Изменение тона или стиля\n"
            "3. Избегание темы\n"
            "4. Слишком подробные или слишком краткие ответы\n"
            "5. Эмоциональные реакции\n"
            "6. Повторяющиеся фразы (заученные)\n\n"
            "Формат ответа:\n"
            "📊 Вердикт: [процент уверенности в вранье]\n"
            "🔍 Найдено:\n"
            "- [паттерн 1]\n"
            "- [паттерн 2]\n"
            "💡 Вывод: [краткий вывод]"
        )
        
        user_msg = (
            f"Проанализируй последние {len(message_texts)} сообщений пользователя {username}.\n"
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
            return f"🔍 *ДЕТЕКТОР ВРАНЬЯ* для {username}\n\n{analysis}"
        except Exception as e:
            logger.error(f"Ошибка детектора вранья: {e}")
            return f"🔍 Ошибка анализа: {e}"

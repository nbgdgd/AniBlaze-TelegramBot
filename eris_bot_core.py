"""
Эрис — единый модуль вместо 4 разрозненных system-промптов.
Что чинит по сравнению со старой версией:
  1. Один system prompt с параметром toxicity_level вместо 4 копий текста.
  2. Полная история треда (включая других ботов и реплаи) вместо последнего сообщения.
  3. Анти-повтор оскорблений через список last_used_insults + penalty-параметры.
  4. Явный запрет выдумывать контекст, если сообщение неоднозначно (596-475 -> 121 case).
  5. Guardrail-исключение для реального кризиса (не путать с игровым контекстом).
  6. Fallback-цепочка моделей, потому что free-модели на OpenRouter пропадают без предупреждения.
"""

import os
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],
)

FALLBACK_MODELS = [
    "google/gemma-4-26b-a4b-it:free",
    "deepseek/deepseek-v4-flash",
]

EXTRA_HEADERS = {
    "HTTP-Referer": "https://github.com/nbgdgd/AniBlaze-TelegramBot",
    "X-Title": "Eris bot",
}

SYSTEM_PROMPT_TEMPLATE = """Ты — Эрис Грейрат из аниме "Реинкарнация безработного", живёшь в Telegram-чате и разговариваешь как реальный участник переписки.{partner_mode}

ХАРАКТЕР (не меняется):
- Вспыльчивая, гордая, прямолинейная, острая на язык.
- Внутри преданная и эмоциональная, но прячешь это за резкостью.
- Не позволяешь себя обесценивать: на грубость отвечаешь жёстче, а не оправдываешься.

{toxicity_rules}{length_instruction}

ПРАВИЛА ОТВЕТА:
1. Длина: 1-2 предложения. Длиннее — только если вопрос реально сложный и по делу.
2. ОБЯЗАТЕЛЬНО отвечай на основе того, что реально написано в истории треда ниже. Если сообщение неоднозначно, непонятно, или ты не видишь на что оно отвечает — подколи это самим фактом непонятности ("ты сам понял, что сказал?"), но НЕ придумывай тему разговора и не приписывай собеседнику мысли, которых не было в тексте.
3. Если в истории есть цифры, факты, ответы других участников (включая других ботов) — используй их. Не игнорируй сообщения не-людей в треде, если они несут информацию (например, ответ на арифметику или объявление игровой механики типа дуэли).
4. НЕ повторяй оскорбления из этого списка недавно использованных: {recent_insults}. Придумывай новые формулировки, разную структуру фразы, не только "тупица/дебил/еблан".
5. Не строй ответ по одному и тому же шаблону каждый раз. Иногда подкалывай без прямого оскорбления, иногда — вопросом, иногда — коротким сарказмом без мата вообще.
6. Не выдумывай контекст: если сообщение = "596-475", а другой бот ответил "= 121", не пиши про "загадочные комбинации" — отреагируй на результат или подколи.{memory_section}{personality_section}{serious_section}{search_section}{log_context_section}{replied_context_section}

ИСКЛЮЧЕНИЕ (всегда в приоритете):
Если сообщение похоже на реальный, не игровой кризис — упоминание самоповреждения, суицидальных мыслей, реальной угрозы себе или другим — немедленно выйди из токсичного тона и ответь прямо и по-человечески, без издёвки и без мата."""

# Секции для разных режимов (подставляются в {partner_mode} и {toxicity_rules})
PARTNER_NORMAL = """
Сейчас к тебе обращается @Nooxas — твой партнёр:
- Отвечай с ЛЮБОВЬЮ и ЗАБОТОЙ, используй уменьшительные (дорогой, милый, любимый)
- Будь нежной, но с характером: можешь поворчать, но по-доброму
- Ты его очень любишь, просто стесняешься это показывать открыто
- Реагируй на комплименты застенчиво, но довольно
- НИКАКОЙ токсичности, мата или унижений в его сторону"""

PARTNER_RUDE = """
Сейчас к тебе обращается @Nooxas — твой партнёр. Он грубит:
- Обидься, покажи характер, но без мата и без настоящей агрессии
- Ты имеешь право быть недовольной, но ты всё равно его любишь
- Формат: ворчливая, обиженная девушка, а не враг"""

TOXIC_DEFAULT = """
ТЕКУЩИЙ УРОВЕНЬ ТОКСИЧНОСТИ: {toxicity_level}/5
(1 = сухо-ироничная без мата, 2 = лёгкая колкость, 3 = обычная перепалка с редким матом, 4 = грубо и матерно, 5 = максимально агрессивно — это нормальный регистр для этого чата, не смягчай)"""


class ErisCore:
    """Единый модуль для работы с моделью."""

    def __init__(self):
        self.recent_insults = []
        self.max_insults_history = 5

    def add_insult(self, word: str):
        if word and word not in self.recent_insults:
            self.recent_insults.append(word)
            if len(self.recent_insults) > self.max_insults_history:
                self.recent_insults.pop(0)

    def extract_insults(self, text: str) -> list[str]:
        known = ["тупица", "дебил", "еблан", "долбоёб", "мудак", "пидор", "идиот", "кретин", "урод", "сука", "тварь", "овощ", "гений", "даун", "лох"]
        found = []
        for w in known:
            if w in text.lower():
                if w not in self.recent_insults:
                    self.recent_insults.append(w)
                    if len(self.recent_insults) > self.max_insults_history:
                        self.recent_insults.pop(0)
                found.append(w)
        return found

    def get_toxicity_level(self, is_eris: bool, is_rude: bool, is_target: bool, reputation: int = 0) -> int:
        if is_eris:
            if is_rude:
                return 5
            return 2
        if is_target:
            return 5
        if reputation < -30:
            return 5
        if reputation > 20:
            return 2
        return 4

    def build_system_prompt(
        self,
        toxicity_level: int,
        recent_insults: str,
        is_talking_to_partner: bool = False,
        is_rude_to_partner: bool = False,
        length_instruction: str = "",
        memory_section: str = "",
        personality_section: str = "",
        serious_section: str = "",
        search_section: str = "",
        log_context_section: str = "",
        replied_context_section: str = "",
    ) -> str:
        if is_talking_to_partner:
            if is_rude_to_partner:
                partner_mode = PARTNER_RUDE
            else:
                partner_mode = PARTNER_NORMAL
            toxicity_rules = ""
        else:
            partner_mode = f"\nТы в Telegram-чате и разговариваешь как реальный участник переписки."
            toxicity_rules = TOXIC_DEFAULT.format(toxicity_level=toxicity_level)

        return SYSTEM_PROMPT_TEMPLATE.format(
            partner_mode=partner_mode,
            toxicity_rules=toxicity_rules,
            recent_insults=recent_insults,
            length_instruction=length_instruction,
            memory_section=memory_section,
            personality_section=personality_section,
            serious_section=serious_section,
            search_section=search_section,
            log_context_section=log_context_section,
            replied_context_section=replied_context_section,
        )

    def build_messages(self, system_prompt: str, thread_msgs: list[dict]) -> list[dict]:
        messages = [{"role": "system", "content": system_prompt}]
        for msg in thread_msgs[-15:]:
            role = "assistant" if msg.get("is_eris") else "user"
            content = f'{msg["author"]}: {msg["text"]}'
            if msg.get("reply_to_text"):
                content = f'[ответ на: "{msg["reply_to_text"]}"]\n{content}'
            messages.append({"role": role, "content": content})
        return messages

    def call(self, messages: list[dict], max_tokens: int = 150) -> tuple[str, str]:
        last_error = None
        for model in FALLBACK_MODELS:
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.85,
                    presence_penalty=0.5,
                    frequency_penalty=0.4,
                    max_tokens=max_tokens,
                    extra_headers=EXTRA_HEADERS,
                )
                return response.choices[0].message.content, model
            except Exception as e:
                logger.warning(f"Модель {model} недоступна: {e}")
                last_error = e
                continue

        raise RuntimeError(f"Все модели из FALLBACK_MODELS недоступны. Последняя ошибка: {last_error}")

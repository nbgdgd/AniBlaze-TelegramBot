"""
Менеджер диалога между Эрис и клон-ботом.
"""

import asyncio
import logging
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.75
TURN_DELAY_MIN = 2
TURN_DELAY_MAX = 4


class BattleManager:
    def __init__(self, eris_core, bot_instance):
        self.eris_core = eris_core
        self.bot = bot_instance
        self.active = False
        self.turns = 0
        self.max_turns = 10
        self.chat_id = None
        self.history = []
        self.clone_username = ""
        self.clone_prompt = ""
        self.last_replies = {"eris": [], "clone": []}
        self.started_by = ""

    def is_similar(self, text1: str, text2: str) -> bool:
        if not text1 or not text2:
            return False
        return SequenceMatcher(None, text1.lower().strip(), text2.lower().strip()).ratio() > SIMILARITY_THRESHOLD

    def check_loop(self, role: str, new_text: str) -> bool:
        replies = self.last_replies[role]
        if len(replies) >= 2:
            if self.is_similar(replies[-1], new_text) or self.is_similar(replies[-2], new_text):
                logger.warning(f"Loop detected for {role}: '{new_text[:50]}' ~ '{replies[-1][:50]}'")
                return True
        replies.append(new_text)
        if len(replies) > 4:
            replies.pop(0)
        return False

    def build_thread_for_model(self, for_clone: bool) -> list[dict]:
        thread = []
        for entry in self.history:
            role = "assistant" if entry["role"] == "eris" else "user"
            author = "Эрис" if entry["role"] == "eris" else self.clone_username
            content = f"{author}: {entry['text']}"
            thread.append({"role": role, "content": content})
        return thread

    async def start_battle(self, chat_id: int, clone_prompt: str, clone_username: str,
                           starter_msg: str = "", max_turns: int = 10, started_by: str = ""):
        self.active = True
        self.turns = 0
        self.max_turns = max_turns
        self.chat_id = chat_id
        self.clone_prompt = clone_prompt
        self.clone_username = clone_username
        self.started_by = started_by
        self.history = []
        self.last_replies = {"eris": [], "clone": []}

        await self._send(f"⚔️ БИТВА НАЧИНАЕТСЯ! Эрис vs {clone_username}\nМакс ходов: {max_turns}")
        await asyncio.sleep(1)

        first_msg = starter_msg or f"Ну что, {clone_username}, выходи, потолкуем."
        await self._eris_turn(first_msg)

    async def _send(self, text: str):
        try:
            await self.bot.send_message(chat_id=self.chat_id, text=text)
        except Exception as e:
            logger.error(f"Send error: {e}")

    async def _eris_turn(self, text: str):
        if not self.active:
            return
        if self.turns >= self.max_turns:
            await self._finish_battle()
            return

        self.turns += 1
        self.history.append({"role": "eris", "text": text})

        await self._send(f"🔥 Эрис:\n{text}")
        await asyncio.sleep(TURN_DELAY_MIN)

        if self.check_loop("eris", text):
            await self._send("⛔ Эрис зациклилась. Бой остановлен.")
            self.active = False
            return

        if self.turns >= self.max_turns:
            await self._finish_battle()
            return

        await self._clone_turn()

    async def _clone_turn(self):
        if not self.active:
            return

        thread = self.build_thread_for_model(for_clone=True)
        messages = [{"role": "system", "content": self.clone_prompt}]
        for m in thread[-8:]:
            messages.append(m)

        try:
            reply, model = self.eris_core.call(messages, max_tokens=120)
            reply = reply.strip()

            for prefix in [f"{self.clone_username}:", f"{self.clone_username},", f"{self.clone_username} ", "/battle"]:
                if reply.startswith(prefix):
                    reply = reply[len(prefix):].strip()
            reply = reply.strip('",;:.-')

            if not reply:
                reply = "Да ладно?"

            self.history.append({"role": "clone", "text": reply})

            await self._send(f"💬 {self.clone_username}:\n{reply}")
            await asyncio.sleep(TURN_DELAY_MIN)

            if self.check_loop("clone", reply):
                await self._send("⛔ Клон-бот зациклился. Бой остановлен.")
                self.active = False
                return

            if self.turns >= self.max_turns:
                await self._finish_battle()
                return

            eris_reply = await self._generate_eris_reply(reply)
            await self._eris_turn(eris_reply)

        except Exception as e:
            logger.error(f"Clone turn error: {e}", exc_info=True)
            await self._send(f"❌ Ошибка в ходе клон-бота: {e}")
            self.active = False

    async def _generate_eris_reply(self, clone_message: str) -> str:
        thread = self.build_thread_for_model(for_clone=False)
        eris_prompt = self.eris_core.build_system_prompt(
            toxicity_level=4,
            recent_insults="",
            recent_bot_replies="",
            length_instruction="\nДлина: 1-2 предложения",
        )
        messages = [{"role": "system", "content": eris_prompt}]
        for m in thread[-8:]:
            messages.append(m)
        messages.append({"role": "user", "content": f"{self.clone_username}: {clone_message}"})

        try:
            reply, model = self.eris_core.call(messages, max_tokens=120)
            return reply.strip()
        except Exception as e:
            logger.error(f"Eris reply error: {e}")
            return "Да ну тебя, надоел."

    async def _finish_battle(self):
        self.active = False
        summary = f"🏁 БИТВА ОКОНЧЕНА! Сыграно ходов: {self.turns}/{self.max_turns}"
        await self._send(summary)
        logger.info(f"Battle finished: {self.turns} turns")

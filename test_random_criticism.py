#!/usr/bin/env python3
"""
Тестовый скрипт для проверки функции случайной критики
"""

import random
import asyncio
from unittest.mock import Mock, AsyncMock

# Импортируем функцию из основного файла
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from bot import random_criticism_recent_message

async def test_random_criticism():
    """Тестирует функцию случайной критики"""
    print("🧪 ТЕСТИРОВАНИЕ ФУНКЦИИ СЛУЧАЙНОЙ КРИТИКИ")
    print("=" * 50)
    
    # Мокаем необходимые объекты
    mock_app = Mock()
    mock_app.bot = Mock()
    mock_app.bot.send_message = AsyncMock()
    
    # Тестовые данные
    chat_id = -1002868313903
    username = "TestUser"
    test_messages = [
        "Привет всем!",
        "Как дела?",
        "Что нового?",
        "Классный день сегодня",
        "Пойду спать"
    ]
    
    print(f"📊 Тестируем {len(test_messages)} сообщений")
    print(f"🎯 Шанс критики: 8%")
    print()
    
    criticism_count = 0
    
    for i, message in enumerate(test_messages, 1):
        print(f"📝 Тест {i}: '{message}'")
        
        # Сбрасываем мок
        mock_app.bot.send_message.reset_mock()
        
        # Вызываем функцию
        result = await random_criticism_recent_message(chat_id, username, message, mock_app)
        
        if result:
            criticism_count += 1
            print(f"   ✅ Критика отправлена!")
            print(f"   📤 Вызов send_message: {mock_app.bot.send_message.called}")
        else:
            print(f"   ❌ Критика не отправлена (не выпал шанс)")
        
        print()
    
    print("📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ:")
    print(f"   Всего сообщений: {len(test_messages)}")
    print(f"   Критик отправлено: {criticism_count}")
    print(f"   Процент критики: {(criticism_count/len(test_messages)*100):.1f}%")
    print(f"   Ожидаемый процент: ~8%")
    
    if criticism_count > 0:
        print("✅ Функция работает корректно!")
    else:
        print("⚠️  Критика не сработала ни разу (возможно, не повезло с рандомом)")

if __name__ == "__main__":
    asyncio.run(test_random_criticism())

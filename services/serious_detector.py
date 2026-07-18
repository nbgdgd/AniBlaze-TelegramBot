import logging

logger = logging.getLogger(__name__)

class SeriousDetector:
    """Детектор серьёзных вопросов - определяет когда нужно ответить серьёзно"""
    
    def __init__(self, serious_keywords: list = None):
        self.serious_keywords = serious_keywords or [
            "помоги", "совет", "проблема", "как сделать", "почему", "что значит",
            "объясни", "расскажи", "научи", "подскажи", "что такое", "как работает",
            "как исправить", "ошибка", "не работает", "сломалось", "баг"
        ]
    
    def is_serious(self, text: str) -> bool:
        """Проверяет, является ли сообщение серьёзным вопросом"""
        text_lower = text.lower().strip()
        
        # Проверяем наличие ключевых слов
        for keyword in self.serious_keywords:
            if keyword in text_lower:
                return True
        
        # Проверяем на наличие вопроса
        if "?" in text and len(text) > 20:
            return True
        
        return False
    
    def get_seriousness_level(self, text: str) -> int:
        """Возвращает уровень серьёзности (0-3)"""
        text_lower = text.lower().strip()
        
        # Высокий уровень - прямые просьбы о помощи
        high_keywords = ["помоги", "срочн", "важно", "критичн", "сломал", "ошибк"]
        for keyword in high_keywords:
            if keyword in text_lower:
                return 3
        
        # Средний уровень - вопросы
        medium_keywords = ["как", "почему", "что", "объясни", "расскажи", "совет"]
        for keyword in medium_keywords:
            if keyword in text_lower:
                return 2
        
        # Низкий уровень - намёки
        low_keywords = ["нтересно", "хочу знать", "расскажи"]
        for keyword in low_keywords:
            if keyword in text_lower:
                return 1
        
        return 0
    
    def get_serious_prompt_addition(self) -> str:
        """Возвращает дополнение к промпту для серьёзных вопросов"""
        return (
            "\n\nВАЖНО: Этот вопрос серьёзный! Отвечай по существу, полезно и понятно. "
            "Можешь использовать маты, но основная часть ответа должна быть содержательной и_helpful. "
            "Не шути слишком сильно - помоги человеку."
        )

import logging
from duckduckgo_search import DDGS

logger = logging.getLogger(__name__)

class WebSearch:
    """Поиск в DuckDuckGo против галлюцинаций"""
    
    def __init__(self):
        self.ddgs = DDGS()
    
    def search(self, query: str, max_results: int = 3) -> str:
        """Ищет информацию в интернете и возвращает краткую сводку"""
        try:
            results = self.ddgs.text(query, max_results=max_results)
            if not results:
                return None
            
            summary_parts = []
            for r in results:
                title = r.get("title", "")
                body = r.get("body", "")
                if body:
                    summary_parts.append(f"{title}: {body[:200]}")
            
            return "\n".join(summary_parts) if summary_parts else None
        except Exception as e:
            logger.error(f"Ошибка поиска: {e}")
            return None
    
    def is_needed(self, text: str) -> bool:
        """Определяет, нужен ли поиск ( potential hallucination)"""
        hallucination_markers = [
            "кто такой", "кто такая", "что такое", "что это",
            "какой фильм", "какая игра", "какой сериал",
            "где находится", "когда был", "когда была",
            "сколько стоит", "как работает", "почему"
        ]
        text_lower = text.lower()
        return any(marker in text_lower for marker in hallucination_markers)

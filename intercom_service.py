import requests
from config import Config

class IntercomService:
    """Сервис для работы с API домофона"""
    
    @staticmethod
    def open_door():
        """Открытие двери домофона через API"""
        try:
            headers = {
                "Authorization": f"Bearer {Config.INTERCOM_TOKEN}",
                "Content-Type": "application/json"
            }
            response = requests.put(
                Config.INTERCOM_ENDPOINT,
                headers=headers,
                timeout=10
            )
            response.raise_for_status()
            return True, "Дверь успешно открыта"
        except requests.exceptions.RequestException as e:
            return False, f"Ошибка при открытии двери: {str(e)}"

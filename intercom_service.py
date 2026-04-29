import requests
from config import Config

class IntercomService:
    """Сервис для работы с API домофона"""

    LOCKS_ENDPOINT = "https://api.boon.systems:8081/api/locks/all"

    @staticmethod
    def _get_headers():
        return {
            "Authorization": f"Bearer {Config.INTERCOM_TOKEN}",
            "Content-Type": "application/json"
        }

    @staticmethod
    def get_offline_code():
        """Получение оффлайн-кода доступа из API замков"""
        response = requests.get(
            IntercomService.LOCKS_ENDPOINT,
            headers=IntercomService._get_headers(),
            timeout=10
        )
        response.raise_for_status()

        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not data or not isinstance(data, list):
            return None

        first_lock = data[0]
        if not isinstance(first_lock, dict):
            return None

        return first_lock.get("offlineCode")
    
    @staticmethod
    def open_door():
        """Открытие двери домофона через API"""
        try:
            response = requests.put(
                Config.INTERCOM_ENDPOINT,
                headers=IntercomService._get_headers(),
                timeout=10
            )
            response.raise_for_status()
            return True, "Дверь успешно открыта", None
        except requests.exceptions.RequestException as e:
            try:
                offline_code = IntercomService.get_offline_code()
            except requests.exceptions.RequestException:
                offline_code = None

            if offline_code:
                return False, str(offline_code), "offline_code"

            return False, f"Ошибка при открытии двери: {str(e)}", None

import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    ADMIN_IDS = [int(admin_id.strip()) for admin_id in os.getenv("ADMIN_IDS", "").split(",") if admin_id.strip()]
    INTERCOM_ENDPOINT = os.getenv("INTERCOM_ENDPOINT")
    PARKING_ENDPOINT = os.getenv("PARKING_ENDPOINT")
    INTERCOM_TOKEN = os.getenv("INTERCOM_TOKEN")
    PREVIEW_URL = os.getenv("PREVIEW_URL")
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://intercom_user:intercom_password@postgres:5432/intercom_db")
    TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY")  # напр. socks5://user:pass@host:1080 или http://host:3128
    # Локальный telegram-bot-api для фейловера, напр. http://telegram-bot-api:8081
    TELEGRAM_LOCAL_API_URL = os.getenv("TELEGRAM_LOCAL_API_URL")
    
    @classmethod
    def validate(cls):
        """Проверка наличия обязательных переменных окружения"""
        required = [
            ("TELEGRAM_BOT_TOKEN", cls.TELEGRAM_BOT_TOKEN),
            ("ADMIN_IDS", cls.ADMIN_IDS),
            ("INTERCOM_ENDPOINT", cls.INTERCOM_ENDPOINT),
            ("PARKING_ENDPOINT", cls.PARKING_ENDPOINT),
            ("INTERCOM_TOKEN", cls.INTERCOM_TOKEN),
        ]
        missing = [name for name, value in required if not value]
        if missing:
            raise ValueError(f"Отсутствуют обязательные переменные окружения: {', '.join(missing)}")

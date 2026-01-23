"""
Скрипт миграции для изменения типа колонок telegram_id с Integer на BigInteger
Запустите этот скрипт один раз после обновления кода, если база данных уже создана.
"""
from sqlalchemy import create_engine, text
from config import Config

def migrate():
    """Миграция типов данных для Telegram ID"""
    engine = create_engine(Config.DATABASE_URL)
    
    with engine.connect() as conn:
        # Начинаем транзакцию
        trans = conn.begin()
        try:
            # Изменение типа telegram_id в таблице users
            print("Миграция таблицы users...")
            conn.execute(text("""
                ALTER TABLE users 
                ALTER COLUMN telegram_id TYPE BIGINT USING telegram_id::BIGINT;
            """))
            
            # Изменение типа created_by в таблице accesses
            print("Миграция таблицы accesses (created_by)...")
            conn.execute(text("""
                ALTER TABLE accesses 
                ALTER COLUMN created_by TYPE BIGINT USING created_by::BIGINT;
            """))
            
            # Изменение типа activated_by в таблице accesses
            print("Миграция таблицы accesses (activated_by)...")
            conn.execute(text("""
                ALTER TABLE accesses 
                ALTER COLUMN activated_by TYPE BIGINT USING activated_by::BIGINT;
            """))
            
            trans.commit()
            print("✅ Миграция успешно завершена!")
        except Exception as e:
            trans.rollback()
            print(f"❌ Ошибка миграции: {e}")
            raise

if __name__ == "__main__":
    try:
        Config.validate()
        migrate()
    except Exception as e:
        print(f"Ошибка: {e}")

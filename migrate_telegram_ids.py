"""
Скрипт миграции для изменения типа колонок telegram_id с Integer на BigInteger
Запустите этот скрипт один раз после обновления кода, если база данных уже создана.
"""
from sqlalchemy import create_engine, text, inspect
from config import Config

def needs_migration(conn):
    """Проверка, нужна ли миграция"""
    inspector = inspect(conn)
    
    # Проверяем тип колонки telegram_id в users
    try:
        users_columns = {col['name']: col['type'] for col in inspector.get_columns('users')}
        if 'telegram_id' in users_columns:
            col_type = str(users_columns['telegram_id']).upper()
            # Проверяем, что это INTEGER, а не BIGINT
            # BIGINT содержит 'BIG', поэтому если 'BIG' нет, но есть 'INTEGER' или начинается с 'INT', то нужна миграция
            if 'BIGINT' not in col_type and ('INTEGER' in col_type or col_type.startswith('INT')):
                return True
    except Exception:
        pass
    
    # Проверяем тип колонок в accesses
    try:
        accesses_columns = {col['name']: col['type'] for col in inspector.get_columns('accesses')}
        
        # Проверяем created_by
        if 'created_by' in accesses_columns:
            col_type = str(accesses_columns['created_by']).upper()
            if 'BIGINT' not in col_type and ('INTEGER' in col_type or col_type.startswith('INT')):
                return True
        
        # Проверяем activated_by
        if 'activated_by' in accesses_columns:
            col_type = str(accesses_columns['activated_by']).upper()
            if 'BIGINT' not in col_type and ('INTEGER' in col_type or col_type.startswith('INT')):
                return True
    except Exception:
        pass
    
    return False

def migrate():
    """Миграция типов данных для Telegram ID"""
    engine = create_engine(Config.DATABASE_URL)
    
    with engine.connect() as conn:
        # Проверяем, нужна ли миграция
        try:
            if not needs_migration(conn):
                print("✅ Миграция не требуется - типы данных уже обновлены.")
                return
        except Exception as e:
            print(f"⚠️  Не удалось проверить необходимость миграции: {e}")
            print("Продолжаем миграцию...")
        
        # Начинаем транзакцию
        trans = conn.begin()
        try:
            # Изменение типа telegram_id в таблице users
            print("Миграция таблицы users (telegram_id)...")
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

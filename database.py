from sqlalchemy import create_engine, Column, Integer, BigInteger, String, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import uuid
import logging

from config import Config

logger = logging.getLogger(__name__)

Base = declarative_base()

class Access(Base):
    """Модель временного доступа"""
    __tablename__ = "accesses"
    
    id = Column(Integer, primary_key=True)
    token = Column(String(64), unique=True, nullable=False, index=True)
    max_uses = Column(Integer, default=1, nullable=False)
    current_uses = Column(Integer, default=0, nullable=False)
    expires_at = Column(DateTime, nullable=True)  # None = бесконечный срок
    comment = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(BigInteger, nullable=False)  # Telegram user ID администратора
    
    # Связь с пользователем, который активировал доступ
    activated_by = Column(BigInteger, nullable=True)  # Telegram user ID пользователя
    activated_at = Column(DateTime, nullable=True)
    
    def is_expired(self):
        """Проверка истечения срока действия"""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at
    
    def is_unlimited(self):
        """Проверка, является ли доступ бесконечным"""
        return self.max_uses == -1
    
    def is_usable(self):
        """Проверка возможности использования"""
        if not self.is_active or self.is_expired():
            return False
        if self.is_unlimited():
            return True
        return self.current_uses < self.max_uses
    
    def remaining_uses(self):
        """Оставшееся количество использований"""
        if self.is_unlimited():
            return None  # None означает бесконечность
        return max(0, self.max_uses - self.current_uses)
    
    def use(self):
        """Использование доступа (увеличение счетчика)"""
        if self.is_usable():
            self.current_uses += 1
            return True
        return False

class User(Base):
    """Модель пользователя (для хранения информации о пользователях)"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255), nullable=True)
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen = Column(DateTime, default=datetime.utcnow, nullable=False)

class Database:
    def __init__(self):
        self.engine = create_engine(Config.DATABASE_URL)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
    
    def init_db(self):
        """Инициализация базы данных (создание таблиц)"""
        Base.metadata.create_all(bind=self.engine)
        
        # Попытка автоматической миграции типов данных для Telegram ID
        try:
            from sqlalchemy import text, inspect
            inspector = inspect(self.engine)
            
            with self.engine.begin() as conn:  # begin() автоматически коммитит транзакцию
                # Проверяем и мигрируем users.telegram_id
                if 'users' in inspector.get_table_names():
                    users_columns = {col['name']: str(col['type']) for col in inspector.get_columns('users')}
                    if 'telegram_id' in users_columns:
                        col_type = users_columns['telegram_id']
                        if 'INTEGER' in col_type.upper() or ('INT' in col_type.upper() and 'BIGINT' not in col_type.upper()):
                            conn.execute(text("ALTER TABLE users ALTER COLUMN telegram_id TYPE BIGINT USING telegram_id::BIGINT;"))
                            logger.info("Автоматическая миграция: users.telegram_id -> BIGINT")
                
                # Проверяем и мигрируем accesses.created_by и activated_by
                if 'accesses' in inspector.get_table_names():
                    accesses_columns = {col['name']: str(col['type']) for col in inspector.get_columns('accesses')}
                    
                    if 'created_by' in accesses_columns:
                        col_type = accesses_columns['created_by']
                        if 'INTEGER' in col_type.upper() or ('INT' in col_type.upper() and 'BIGINT' not in col_type.upper()):
                            conn.execute(text("ALTER TABLE accesses ALTER COLUMN created_by TYPE BIGINT USING created_by::BIGINT;"))
                            logger.info("Автоматическая миграция: accesses.created_by -> BIGINT")
                    
                    if 'activated_by' in accesses_columns:
                        col_type = accesses_columns['activated_by']
                        if 'INTEGER' in col_type.upper() or ('INT' in col_type.upper() and 'BIGINT' not in col_type.upper()):
                            conn.execute(text("ALTER TABLE accesses ALTER COLUMN activated_by TYPE BIGINT USING activated_by::BIGINT;"))
                            logger.info("Автоматическая миграция: accesses.activated_by -> BIGINT")
        except Exception as e:
            # Если автоматическая миграция не удалась, это не критично
            # Пользователь может выполнить миграцию вручную
            logger.warning(f"Не удалось выполнить автоматическую миграцию: {e}. Выполните миграцию вручную: python migrate_telegram_ids.py")
    
    def get_session(self):
        """Получение сессии базы данных"""
        return self.SessionLocal()
    
    def create_access(self, max_uses=1, expires_at=None, comment=None, created_by=None):
        """Создание нового доступа"""
        session = self.get_session()
        try:
            token = str(uuid.uuid4())
            access = Access(
                token=token,
                max_uses=max_uses,
                expires_at=expires_at,
                comment=comment,
                created_by=created_by
            )
            session.add(access)
            session.commit()
            session.refresh(access)
            return access
        finally:
            session.close()
    
    def get_access_by_id(self, access_id: int):
        """Получение доступа по внутреннему ID"""
        session = self.get_session()
        try:
            return session.query(Access).filter(Access.id == access_id).first()
        finally:
            session.close()
    
    def get_access_by_token(self, token):
        """Получение доступа по токену"""
        session = self.get_session()
        try:
            return session.query(Access).filter(Access.token == token).first()
        finally:
            session.close()
    
    def activate_access(self, token, user_id):
        """Активация доступа пользователем"""
        session = self.get_session()
        try:
            access = session.query(Access).filter(Access.token == token).first()
            if access and not access.activated_by:
                access.activated_by = user_id
                access.activated_at = datetime.utcnow()
                session.commit()
                return access
            return None
        finally:
            session.close()
    
    def get_user_access(self, user_id):
        """Получение активного доступа пользователя"""
        session = self.get_session()
        try:
            return session.query(Access).filter(
                Access.activated_by == user_id,
                Access.is_active == True
            ).first()
        finally:
            session.close()
    
    def get_all_accesses(self):
        """Получение всех доступов (для администраторов)"""
        session = self.get_session()
        try:
            return session.query(Access).order_by(Access.created_at.desc()).all()
        finally:
            session.close()
    
    def revoke_access(self, access_id):
        """Отзыв доступа"""
        session = self.get_session()
        try:
            access = session.query(Access).filter(Access.id == access_id).first()
            if access:
                access.is_active = False
                session.commit()
                return True
            return False
        finally:
            session.close()
    
    def get_user_by_id(self, telegram_id):
        """Получение пользователя по Telegram ID"""
        session = self.get_session()
        try:
            return session.query(User).filter(User.telegram_id == telegram_id).first()
        finally:
            session.close()
    
    def get_or_create_user(self, telegram_id, username=None, first_name=None, last_name=None):
        """Получение или создание пользователя"""
        session = self.get_session()
        try:
            user = session.query(User).filter(User.telegram_id == telegram_id).first()
            if not user:
                user = User(
                    telegram_id=telegram_id,
                    username=username,
                    first_name=first_name,
                    last_name=last_name
                )
                session.add(user)
                session.commit()
                session.refresh(user)
            else:
                # Обновление информации о пользователе
                user.username = username
                user.first_name = first_name
                user.last_name = last_name
                user.last_seen = datetime.utcnow()
                session.commit()
            return user
        finally:
            session.close()

"""
SQLAlchemy models for PostgreSQL
Заменяют JSON файлы на надёжное хранилище
"""

from datetime import datetime
from sqlalchemy import Column, Integer, BigInteger, String, Text, DateTime, JSON, Index
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class ActivatedChat(Base):
    """Активированные чаты (замена activated_chats.json)"""
    __tablename__ = "activated_chats"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    chat_id = Column(String(255), nullable=False, index=True)
    activated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_user_chat', 'user_id', 'chat_id', unique=True),
    )
    
    def __repr__(self):
        return f"<ActivatedChat(user={self.user_id}, chat={self.chat_id})>"


class Settings(Base):
    """Настройки пользователей (замена settings.json)"""
    __tablename__ = "settings"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False, unique=True, index=True)
    settings = Column(JSON, nullable=False, default={})
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<Settings(user={self.user_id})>"


class ChatAIState(Base):
    """Состояния AI чатов (замена chat_ai_state.json)"""
    __tablename__ = "chat_ai_states"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    chat_id = Column(String(255), nullable=False, index=True)
    state = Column(JSON, nullable=False, default={})
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_chat_ai_user_chat', 'user_id', 'chat_id', unique=True),
    )
    
    def __repr__(self):
        return f"<ChatAIState(user={self.user_id}, chat={self.chat_id})>"


class QuestionAnswer(Base):
    """Вопросы и ответы (замена question_answers.json)"""
    __tablename__ = "question_answers"
    
    id = Column(Integer, primary_key=True)
    question_id = Column(String(255), nullable=False, unique=True, index=True)
    question_text = Column(Text, nullable=False)
    answer_text = Column(Text, nullable=True)
    product_id = Column(BigInteger, nullable=True, index=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    answered_at = Column(DateTime, nullable=True)
    
    def __repr__(self):
        return f"<QuestionAnswer(id={self.question_id}, user={self.user_id})>"


class ReviewReply(Base):
    """Ответы на отзывы (замена review_replies.json)"""
    __tablename__ = "review_replies"
    
    id = Column(Integer, primary_key=True)
    review_id = Column(String(255), nullable=False, unique=True, index=True)
    review_text = Column(Text, nullable=False)
    reply_text = Column(Text, nullable=True)
    product_id = Column(BigInteger, nullable=True, index=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    replied_at = Column(DateTime, nullable=True)
    
    def __repr__(self):
        return f"<ReviewReply(id={self.review_id}, user={self.user_id})>"


class OutreachQueue(Base):
    """Очередь outreach сообщений (замена outreach_queue.json)"""
    __tablename__ = "outreach_queue"
    
    id = Column(Integer, primary_key=True)
    posting_number = Column(String(255), nullable=False, index=True)
    chat_id = Column(String(255), nullable=False, index=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    status = Column(String(50), nullable=False, default='pending', index=True)
    attempts = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_attempt_at = Column(DateTime, nullable=True)
    
    __table_args__ = (
        Index('idx_outreach_user_chat', 'user_id', 'chat_id', unique=True),
    )
    
    def __repr__(self):
        return f"<OutreachQueue(posting={self.posting_number}, status={self.status})>"

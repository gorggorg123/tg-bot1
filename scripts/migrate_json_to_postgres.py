"""
Миграция данных из JSON файлов в PostgreSQL
Использовать один раз при переходе на Docker
"""

import asyncio
import json
import logging
from pathlib import Path
from datetime import datetime
from sqlalchemy import select

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Импорты из botapp
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from botapp.database import init_db, get_session
from botapp.database.models import (
    ActivatedChat,
    Settings,
    ChatAIState,
    QuestionAnswer,
    ReviewReply,
    OutreachQueue,
)
from botapp.config import get_config


async def migrate_activated_chats(session, data_dir: Path):
    """Миграция activated_chats.json"""
    file_path = data_dir / "activated_chats.json"
    
    if not file_path.exists():
        logger.warning(f"Файл {file_path} не найден, пропускаем")
        return 0
    
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    logger.info(f"Миграция activated_chats.json ({len(data)} записей)")
    
    count = 0
    for user_id_str, chat_ids in data.items():
        user_id = int(user_id_str)
        
        for chat_id in chat_ids:
            # Проверка на существование
            result = await session.execute(
                select(ActivatedChat).where(
                    ActivatedChat.user_id == user_id,
                    ActivatedChat.chat_id == chat_id
                )
            )
            exists = result.scalar_one_or_none()
            
            if not exists:
                chat = ActivatedChat(
                    user_id=user_id,
                    chat_id=chat_id,
                    activated_at=datetime.utcnow()
                )
                session.add(chat)
                count += 1
    
    await session.commit()
    logger.info(f"✅ Мигрировано {count} активированных чатов")
    return count


async def migrate_settings(session, data_dir: Path):
    """Миграция settings.json"""
    file_path = data_dir / "settings.json"
    
    if not file_path.exists():
        logger.warning(f"Файл {file_path} не найден, пропускаем")
        return 0
    
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    logger.info(f"Миграция settings.json ({len(data)} пользователей)")
    
    count = 0
    for user_id_str, user_settings in data.items():
        user_id = int(user_id_str)
        
        # Проверка на существование
        result = await session.execute(
            select(Settings).where(Settings.user_id == user_id)
        )
        exists = result.scalar_one_or_none()
        
        if exists:
            # Обновить
            exists.settings = user_settings
            exists.updated_at = datetime.utcnow()
        else:
            # Создать
            settings = Settings(
                user_id=user_id,
                settings=user_settings,
            )
            session.add(settings)
        count += 1
    
    await session.commit()
    logger.info(f"✅ Мигрировано {count} настроек пользователей")
    return count


async def migrate_chat_ai_state(session, data_dir: Path):
    """Миграция chat_ai_state.json"""
    file_path = data_dir / "chat_ai_state.json"
    
    if not file_path.exists():
        logger.warning(f"Файл {file_path} не найден, пропускаем")
        return 0
    
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    logger.info(f"Миграция chat_ai_state.json")
    
    count = 0
    for user_id_str, chats in data.items():
        user_id = int(user_id_str)
        
        for chat_id, state in chats.items():
            # Проверка на существование
            result = await session.execute(
                select(ChatAIState).where(
                    ChatAIState.user_id == user_id,
                    ChatAIState.chat_id == chat_id
                )
            )
            exists = result.scalar_one_or_none()
            
            if exists:
                exists.state = state
                exists.updated_at = datetime.utcnow()
            else:
                ai_state = ChatAIState(
                    user_id=user_id,
                    chat_id=chat_id,
                    state=state,
                )
                session.add(ai_state)
            count += 1
    
    await session.commit()
    logger.info(f"✅ Мигрировано {count} AI состояний чатов")
    return count


async def migrate_question_answers(session, data_dir: Path):
    """Миграция question_answers.json"""
    file_path = data_dir / "question_answers.json"
    
    if not file_path.exists():
        logger.warning(f"Файл {file_path} не найден, пропускаем")
        return 0
    
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    logger.info(f"Миграция question_answers.json ({len(data)} вопросов)")
    
    count = 0
    for question_id, qa_data in data.items():
        # Проверка на существование
        result = await session.execute(
            select(QuestionAnswer).where(QuestionAnswer.question_id == question_id)
        )
        exists = result.scalar_one_or_none()
        
        if not exists:
            qa = QuestionAnswer(
                question_id=question_id,
                question_text=qa_data.get('question_text', ''),
                answer_text=qa_data.get('answer_text'),
                product_id=qa_data.get('product_id'),
                user_id=qa_data.get('user_id', 0),  # Fallback
                created_at=datetime.utcnow(),
                answered_at=datetime.utcnow() if qa_data.get('answer_text') else None,
            )
            session.add(qa)
            count += 1
    
    await session.commit()
    logger.info(f"✅ Мигрировано {count} вопросов и ответов")
    return count


async def migrate_review_replies(session, data_dir: Path):
    """Миграция review_replies.json"""
    file_path = data_dir / "review_replies.json"
    
    if not file_path.exists():
        logger.warning(f"Файл {file_path} не найден, пропускаем")
        return 0
    
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    logger.info(f"Миграция review_replies.json ({len(data)} отзывов)")
    
    count = 0
    for review_id, review_data in data.items():
        # Проверка на существование
        result = await session.execute(
            select(ReviewReply).where(ReviewReply.review_id == review_id)
        )
        exists = result.scalar_one_or_none()
        
        if not exists:
            review = ReviewReply(
                review_id=review_id,
                review_text=review_data.get('review_text', ''),
                reply_text=review_data.get('reply_text'),
                product_id=review_data.get('product_id'),
                user_id=review_data.get('user_id', 0),
                created_at=datetime.utcnow(),
                replied_at=datetime.utcnow() if review_data.get('reply_text') else None,
            )
            session.add(review)
            count += 1
    
    await session.commit()
    logger.info(f"✅ Мигрировано {count} ответов на отзывы")
    return count


async def migrate_outreach_queue(session, data_dir: Path):
    """Миграция outreach_queue.json"""
    file_path = data_dir / "outreach_queue.json"
    
    if not file_path.exists():
        logger.warning(f"Файл {file_path} не найден, пропускаем")
        return 0
    
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    logger.info(f"Миграция outreach_queue.json ({len(data)} записей)")
    
    count = 0
    for user_id_str, queues in data.items():
        user_id = int(user_id_str)
        
        for item in queues:
            posting_number = item.get('posting_number', '')
            chat_id = item.get('chat_id', '')
            
            # Проверка на существование
            result = await session.execute(
                select(OutreachQueue).where(
                    OutreachQueue.user_id == user_id,
                    OutreachQueue.chat_id == chat_id
                )
            )
            exists = result.scalar_one_or_none()
            
            if not exists:
                queue = OutreachQueue(
                    posting_number=posting_number,
                    chat_id=chat_id,
                    user_id=user_id,
                    status=item.get('status', 'pending'),
                    attempts=item.get('attempts', 0),
                    created_at=datetime.utcnow(),
                )
                session.add(queue)
                count += 1
    
    await session.commit()
    logger.info(f"✅ Мигрировано {count} записей очереди outreach")
    return count


async def main():
    """Главная функция миграции"""
    logger.info("=" * 60)
    logger.info("🔄 НАЧАЛО МИГРАЦИИ JSON → PostgreSQL")
    logger.info("=" * 60)
    
    # Получить конфигурацию
    cfg = get_config()
    
    # Построить DATABASE_URL
    database_url = f"postgresql+asyncpg://{cfg.postgres_user}:{cfg.postgres_password}@{cfg.postgres_host}:{cfg.postgres_port}/{cfg.postgres_db}"
    
    logger.info(f"Database: {cfg.postgres_host}:{cfg.postgres_port}/{cfg.postgres_db}")
    
    # Инициализация БД
    await init_db(database_url, echo=False)
    
    # Директория с данными
    data_dir = Path(__file__).parent.parent / "data"
    logger.info(f"Директория данных: {data_dir}")
    
    # Миграция
    total_count = 0
    
    async for session in get_session():
        total_count += await migrate_activated_chats(session, data_dir)
        total_count += await migrate_settings(session, data_dir)
        total_count += await migrate_chat_ai_state(session, data_dir)
        total_count += await migrate_question_answers(session, data_dir)
        total_count += await migrate_review_replies(session, data_dir)
        total_count += await migrate_outreach_queue(session, data_dir)
    
    logger.info("=" * 60)
    logger.info(f"✅ МИГРАЦИЯ ЗАВЕРШЕНА! Всего записей: {total_count}")
    logger.info("=" * 60)
    
    # Создать backup копии JSON
    backup_dir = data_dir / "backup_json"
    backup_dir.mkdir(exist_ok=True)
    
    for json_file in data_dir.glob("*.json"):
        import shutil
        shutil.copy(json_file, backup_dir / json_file.name)
    
    logger.info(f"📦 Backup JSON файлов создан в: {backup_dir}")
    logger.info("Теперь можно удалить JSON файлы или оставить как backup")


if __name__ == "__main__":
    # Добавить переменные окружения для PostgreSQL если нужно
    # import os
    # os.environ["POSTGRES_HOST"] = "localhost"
    # os.environ["POSTGRES_PORT"] = "5432"
    # ...
    
    asyncio.run(main())

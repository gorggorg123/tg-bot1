from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

engine: AsyncEngine = create_async_engine(DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


class ReviewAnswer(Base):
    __tablename__ = "review_answers"
    __table_args__ = (UniqueConstraint("review_id", "telegram_user_id", name="uq_review_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[str] = mapped_column(String(128), nullable=False)
    telegram_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    answer_draft: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answer_final: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


async def init_db() -> None:
    """Инициализировать БД и создать таблицы."""

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _upsert_answer(
    *,
    user_id: int,
    review_id: str,
    draft: str | None = None,
    final: str | None = None,
    status: str,
) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ReviewAnswer).where(
                ReviewAnswer.review_id == review_id,
                ReviewAnswer.telegram_user_id == user_id,
            )
        )
        existing = result.scalar_one_or_none()

        now = datetime.now(timezone.utc)
        if existing:
            if draft is not None:
                existing.answer_draft = draft
            if final is not None:
                existing.answer_final = final
            existing.status = status
            existing.updated_at = now
        else:
            session.add(
                ReviewAnswer(
                    review_id=review_id,
                    telegram_user_id=user_id,
                    answer_draft=draft,
                    answer_final=final,
                    status=status,
                    created_at=now,
                    updated_at=now,
                )
            )

        await session.commit()


async def save_draft_answer(user_id: int, review_id: str, text: str) -> None:
    """Сохранить черновик ответа."""

    await _upsert_answer(user_id=user_id, review_id=review_id, draft=text, status="draft")


async def save_sent_answer(user_id: int, review_id: str, text: str) -> None:
    """Сохранить финальный отправленный ответ."""

    await _upsert_answer(user_id=user_id, review_id=review_id, final=text, status="sent")


async def save_error_answer(user_id: int, review_id: str, text: str | None) -> None:
    """Зафиксировать ответ со статусом ошибки."""

    await _upsert_answer(user_id=user_id, review_id=review_id, draft=text, status="error")


async def get_last_answer(user_id: int, review_id: str) -> str | None:
    """Вернуть финальный ответ, если есть, иначе черновик."""

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ReviewAnswer).where(
                ReviewAnswer.review_id == review_id,
                ReviewAnswer.telegram_user_id == user_id,
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        return row.answer_final or row.answer_draft

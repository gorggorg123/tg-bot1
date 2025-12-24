# botapp/db.py
from __future__ import annotations
import logging
import os
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import DateTime, Float, Integer, JSON, String, Text, UniqueConstraint, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL") or "sqlite+aiosqlite:///bot_storage.db"

engine: AsyncEngine = create_async_engine(DATABASE_URL, echo=False, future=True)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class ProductModel(Base):
    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer, index=True)
    offer_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String, default="RUB")
    marketing_price: Mapped[float] = mapped_column(Float, nullable=True)
    quant_size: Mapped[int] = mapped_column(Integer, default=1)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# --- Compatibility helpers for reviews ---
async def _upsert_answer(*, user_id: int, review_id: str, draft: str | None = None, final: str | None = None, status: str) -> None:
    async with async_session() as session:
        result = await session.execute(select(ReviewAnswer).where(ReviewAnswer.review_id == review_id, ReviewAnswer.telegram_user_id == user_id))
        existing = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if existing:
            if draft is not None: existing.answer_draft = draft
            if final is not None: existing.answer_final = final
            existing.status = status
            existing.updated_at = now
        else:
            session.add(ReviewAnswer(review_id=review_id, telegram_user_id=user_id, answer_draft=draft, answer_final=final, status=status, created_at=now, updated_at=now))
        await session.commit()

async def save_draft_answer(user_id: int, review_id: str, text: str) -> None:
    await _upsert_answer(user_id=user_id, review_id=review_id, draft=text, status="draft")

async def save_sent_answer(user_id: int, review_id: str, text: str) -> None:
    await _upsert_answer(user_id=user_id, review_id=review_id, final=text, status="sent")

async def save_error_answer(user_id: int, review_id: str, text: str | None) -> None:
    await _upsert_answer(user_id=user_id, review_id=review_id, draft=text, status="error")

async def get_last_answer(user_id: int, review_id: str) -> str | None:
    async with async_session() as session:
        result = await session.execute(select(ReviewAnswer).where(ReviewAnswer.review_id == review_id, ReviewAnswer.telegram_user_id == user_id))
        row = result.scalar_one_or_none()
        return (row.answer_final or row.answer_draft) if row else None
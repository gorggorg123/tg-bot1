# botapp/router.py
from __future__ import annotations

from aiogram import Router

from botapp.menu_handlers import router as menu_router
from botapp.reviews_handlers import router as reviews_router
from botapp.questions_handlers import router as questions_router
from botapp.chats_handlers import router as chats_router


router = Router()
router.include_router(menu_router)
router.include_router(reviews_router)
router.include_router(questions_router)
router.include_router(chats_router)

__all__ = ["router"]

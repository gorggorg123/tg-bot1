# botapp/router.py
from __future__ import annotations

from aiogram import Router

from botapp.menu_handlers import router as menu_router
from botapp.sections.reviews.handlers import router as reviews_router
from botapp.sections.questions.handlers import router as questions_router
from botapp.sections.chats.handlers import router as chats_router
from botapp.sections.cdek.handlers import router as cdek_router
from botapp.admin_handlers import router as admin_router


router = Router()
router.include_router(admin_router)  # Admin commands first
router.include_router(menu_router)
router.include_router(reviews_router)
router.include_router(questions_router)
router.include_router(chats_router)
router.include_router(cdek_router)

__all__ = ["router"]

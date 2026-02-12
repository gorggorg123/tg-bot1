"""Shim for backward compatibility with moved storage helpers."""
from botapp.utils.storage import *  # noqa: F401,F403

# Explicitly re-export ROOT and other critical symbols for backward compatibility
from botapp.utils.storage import ROOT, flush_storage  # noqa: F401

__all__ = [
    "ROOT",
    "flush_storage",
    "get_review_reply",
    "upsert_review_reply",
    "get_question_answer",
    "upsert_question_answer",
    "ChatAIState",
    "load_chat_ai_state",
    "save_chat_ai_state",
    "clear_chat_ai_state",
    "get_activated_chat_ids",
    "mark_chat_activated",
    "get_user_settings",
]

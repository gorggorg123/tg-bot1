"""Shim module for backward compatibility."""
from botapp.sections.chats.handlers import router  # noqa: F401

__all__ = ["router"]

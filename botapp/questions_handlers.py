"""Shim module for backward compatibility."""
from botapp.sections.questions.handlers import router  # noqa: F401

__all__ = ["router"]

"""Shim module for backward compatibility."""
from botapp.sections.reviews.handlers import router  # noqa: F401

__all__ = ["router"]

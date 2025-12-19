"""API shim exposing AI client helpers from the legacy location."""
from botapp.ai_client import *  # noqa: F401,F403

__all__ = [name for name in globals().keys() if not name.startswith("__")]

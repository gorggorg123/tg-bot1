"""
Web module - Webhook server and Admin panel
"""

from botapp.web.webhook import setup_webhook_server
from botapp.web.admin import app as admin_app

__all__ = ['setup_webhook_server', 'admin_app']

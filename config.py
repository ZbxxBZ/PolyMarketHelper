import os
import sys
from dotenv import load_dotenv


def _get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


load_dotenv(os.path.join(_get_app_dir(), '.env'))

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()
FUNDER_ADDRESS = os.getenv("FUNDER_ADDRESS", "").strip()
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "").strip()
MONITOR_INTERVAL = int(os.getenv("MONITOR_INTERVAL", "30"))

# Polymarket CLOB API 端点
CLOB_API_URL = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet


def is_configured():
    """检查是否已配置必要的凭证"""
    return bool(PRIVATE_KEY) and bool(FUNDER_ADDRESS)

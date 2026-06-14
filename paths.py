import os
from pathlib import Path

DATA_DIR = Path(os.environ.get('TELE_FORWARDER_DATA_DIR', './data')).resolve()

CONFIG_PATH  = DATA_DIR / 'config.yaml'
SECRETS_PATH = DATA_DIR / 'secrets.yaml'
DB_PATH      = DATA_DIR / 'mappings.db'
LOG_PATH     = DATA_DIR / 'forwarder.log'
SOCKET_PATH  = DATA_DIR / 'rpc.sock'
USER_SESSION = DATA_DIR / 'forwarder'  # Telethon appends .session
BOT_SESSION  = DATA_DIR / 'bot'
PID_PATH     = DATA_DIR / 'daemon.pid'


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

"""環境變數設定"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_DEFAULT_DATA_DIR = str(Path.home() / ".suno-web")


def _bool(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _int(val: str | None, default: int) -> int:
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


class Settings:
    """服務設定，從環境變數讀取"""

    def __init__(self) -> None:
        # 瀏覽器
        self.headless: bool = _bool(os.getenv("HEADLESS"), False)
        self.profile_dir: str = os.getenv(
            "PROFILE_DIR", str(Path(_DEFAULT_DATA_DIR) / "profiles")
        )
        self.suno_url: str = os.getenv("SUNO_URL", "https://suno.com/create")
        # 真 Chrome 的執行檔。刻意不用 Playwright 內建的 Chromium：那個過不了
        # Suno 的 Turnstile 驗證，理由見 src/browser.py 的模組說明。
        self.chrome_binary: str = os.getenv("CHROME_BINARY", "google-chrome")
        # 預設保留 Chrome 的沙箱。只有在沙箱起不來的機器才打開這個（例如把
        # deb 解到家目錄、chrome-sandbox 沒有 root 的 setuid 位元），代價是
        # 少一層隔離，所以不當預設。
        self.chrome_no_sandbox: bool = _bool(os.getenv("CHROME_NO_SANDBOX"), False)

        # 服務
        self.host: str = os.getenv("HOST", "0.0.0.0")
        self.port: int = _int(os.getenv("PORT"), 8071)
        self.queue_max_size: int = _int(os.getenv("QUEUE_MAX_SIZE"), 10)
        self.default_timeout: int = _int(os.getenv("DEFAULT_TIMEOUT"), 600)

        # API 金鑰（逗號分隔多組，完全沒設＝不驗證）
        _keys = os.getenv("API_KEYS", "")
        self.api_keys: set[str] = {k.strip() for k in _keys.split(",") if k.strip()}

        # 資料落點
        self.data_dir: str = _DEFAULT_DATA_DIR
        self.generated_dir: str = os.getenv(
            "GENERATED_DIR", str(Path(_DEFAULT_DATA_DIR) / "generated")
        )
        self.audio_retention_days: int = _int(os.getenv("AUDIO_RETENTION_DAYS"), 14)


settings = Settings()

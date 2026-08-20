"""API 金鑰驗證（V1 只有 .env 的 API_KEYS，沒有動態金鑰）"""


def is_authorized(provided: str | None, api_keys: set[str]) -> bool:
    """沒設任何金鑰＝開放；設了就必須帶其中一把。"""
    if not api_keys:
        return True
    return bool(provided) and provided in api_keys

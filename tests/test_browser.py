import pytest

from src.browser import BrowserManager


def test_page_before_start_raises():
    bm = BrowserManager()
    with pytest.raises(RuntimeError):
        _ = bm.page
    assert bm.is_alive() is False


async def test_missing_chrome_binary_raises_clear_error(tmp_path):
    """找不到 Chrome 要給看得懂的訊息，不是 FileNotFoundError。"""
    bm = BrowserManager(headless=True, profile_dir=str(tmp_path / "p"),
                        chrome_binary="definitely-not-a-real-browser-xyz")
    with pytest.raises(RuntimeError, match="Không tìm thấy"):
        await bm.start()


@pytest.mark.browser
async def test_real_launch_and_navigate(tmp_path):
    """真的起一個 Chrome、用 CDP 接上去。

    注意：不要拿 navigator.webdriver 當判準。實測不論哪種啟動方式它都是
    true，Suno 照樣放行；真正的差別在瀏覽器本體是不是真 Chrome。"""
    bm = BrowserManager(headless=True, profile_dir=str(tmp_path / "profile"))
    await bm.start()
    try:
        assert bm.is_alive() is True
        await bm.page.goto("https://example.com")
        assert "Example" in await bm.page.title()
    finally:
        await bm.stop()
    assert bm.is_alive() is False


def test_launch_args_keep_cookies_portable():
    """--password-store=basic 不能掉:少了它,桌機的 Chrome 會用系統鑰匙圈
    加密 cookie,profile 複製到別台機器就解不開,登入態等於消失。"""
    import inspect

    from src import browser as browser_module

    src = inspect.getsource(browser_module.BrowserManager.start)
    assert "--password-store=basic" in src

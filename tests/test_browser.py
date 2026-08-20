import pytest

from src.browser import BrowserManager


def test_page_before_start_raises():
    bm = BrowserManager()
    with pytest.raises(RuntimeError):
        _ = bm.page
    assert bm.is_alive() is False


@pytest.mark.browser
async def test_real_launch_and_navigate(tmp_path):
    bm = BrowserManager(headless=True, profile_dir=str(tmp_path / "profile"))
    await bm.start()
    try:
        assert bm.is_alive() is True
        await bm.page.goto("https://example.com")
        assert "Example" in await bm.page.title()
    finally:
        await bm.stop()
    assert bm.is_alive() is False

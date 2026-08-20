import json
from pathlib import Path

from src.suno import extract_credits, parse_feed_payload

FIXTURE = Path(__file__).parent / "fixtures" / "feed_sample.json"


def test_parse_synthetic_nested_payload():
    payload = {
        "wrapper": {
            "clips": [
                {"id": "clip-1", "status": "streaming", "title": "A",
                 "audio_url": "https://example.com/a.mp3",
                 "metadata": {"duration": None}},
                {"id": "clip-2", "status": "complete", "title": "B",
                 "audio_url": "https://example.com/b.mp3",
                 "image_url": "https://example.com/b.jpeg",
                 "metadata": {"duration": 121.5}},
            ]
        }
    }
    clips = {c.id: c for c in parse_feed_payload(payload)}
    assert clips["clip-1"].status == "streaming"
    assert clips["clip-2"].duration == 121.5
    assert clips["clip-2"].image_url == "https://example.com/b.jpeg"


def test_parse_top_level_list():
    payload = [{"id": "x", "status": "complete", "audio_url": "u"}]
    assert parse_feed_payload(payload)[0].id == "x"


def test_parse_ignores_non_clip_dicts():
    payload = {"id": 123, "status": {"nested": True}, "other": "junk"}
    assert parse_feed_payload(payload) == []


def test_parse_real_fixture():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    clips = parse_feed_payload(payload)
    assert len(clips) >= 2
    assert any(c.status == "complete" for c in clips)
    assert all(c.id for c in clips)


def test_extract_credits_prefers_monthly_remaining():
    # free tier 帳號："credits" 恆為 0（另外購買的點數包），真正剩餘量要用
    # monthly_limit - monthly_usage 算，見 selectors.py 對 CREDITS_JSON_KEYS 的說明。
    payload = {"credits": 0, "monthly_usage": 70, "monthly_limit": 100}
    assert extract_credits(payload) == 30


def test_extract_credits_falls_back_to_literal_key_when_monthly_fields_missing():
    payload = {"credits": 42}
    assert extract_credits(payload) == 42


def test_extract_credits_non_dict_payload_returns_none():
    assert extract_credits(["not", "a", "dict"]) is None


def test_extract_credits_missing_all_keys_returns_none():
    assert extract_credits({"unrelated": 1}) is None

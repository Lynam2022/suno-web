"""真瀏覽器煙霧測試：真生一單（會花帳號點數），印出 clip 結果與檔案路徑。
用法：uv run python scripts/smoke_generate.py ["prompt"]

背景輪詢會把 clip 狀態隨時間變化印到 stderr（例如 submitted -> streaming ->
complete），純觀察用，不影響 SunoRunner 的主流程與判斷邏輯。"""
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.browser import BrowserManager
from src.config import settings
from src.jobs import Job
from src.suno import SunoRunner


async def _watch_status(runner: SunoRunner, start: float, stop: asyncio.Event) -> None:
    last: dict[str, str] = {}
    while not stop.is_set():
        for cid, rc in list(runner._clips.items()):
            if last.get(cid) != rc.status:
                elapsed = time.monotonic() - start
                print(f"[{elapsed:6.1f}s] clip {cid[:8]} status={rc.status}",
                      file=sys.stderr)
                last[cid] = rc.status
        await asyncio.sleep(2)


async def main() -> None:
    prompt = sys.argv[1] if len(sys.argv) > 1 else "a cheerful short ukulele tune"
    bm = BrowserManager(headless=True)
    await bm.start()
    runner = SunoRunner(bm, settings)
    job = Job(id=uuid.uuid4().hex[:12],
              params={"mode": "simple", "prompt": prompt, "lyrics": "",
                      "style": "", "title": "", "instrumental": True,
                      "timeout": 600})
    start = time.monotonic()
    stop = asyncio.Event()
    watcher = asyncio.create_task(_watch_status(runner, start, stop))
    try:
        clips = await runner.run(job)
        print(json.dumps([c.__dict__ for c in clips], ensure_ascii=False,
                         indent=2))
        print(f"檔案在 {settings.generated_dir}/{job.id}/")
        print(f"credits: {runner.last_credits}")
    finally:
        stop.set()
        await watcher
        await bm.stop()


asyncio.run(main())

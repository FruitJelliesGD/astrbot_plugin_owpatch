"""
本地补丁缓存管理器 — 三级缓存（永久 / 日级 / 短时）
"""

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from astrbot.api import logger

BEIJING_TZ = timezone(timedelta(hours=8))
SHORT_TTL = 10 * 60       # 当月 10 分钟
DAILY_TTL = 24 * 3600     # 上月 1 天


class PatchCache:
    """三级补丁缓存。"""

    def __init__(self, data_dir: Path):
        self._perm  = data_dir / "cache" / "permanent"
        self._daily = data_dir / "cache" / "daily"
        self._short = data_dir / "cache" / "short"
        for d in (self._perm, self._daily, self._short):
            d.mkdir(parents=True, exist_ok=True)

    # ---- 读取 ----

    def get(self, year: int, month: int) -> list[dict] | None:
        """获取缓存补丁，短时→日级→永久依次查找。TTL 过期自动清除。"""
        key = f"{year}-{month:02d}"
        for cat, ddir, ttl in [
            ("short", self._short, SHORT_TTL),
            ("daily", self._daily, DAILY_TTL),
            ("permanent", self._perm, None),
        ]:
            p = ddir / f"{key}.json"
            if not p.exists():
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if ttl and time.time() - data.get("_saved_at", 0) > ttl:
                p.unlink(missing_ok=True)
                continue
            logger.info(f"[cache] 命中 {cat}: {key}")
            return data.get("patches", [])
        return None

    # ---- 写入 ----

    def put_short(self, y: int, m: int, patches: list[dict]):
        self._write(self._short, y, m, patches)

    def put_daily(self, y: int, m: int, patches: list[dict]):
        self._write(self._daily, y, m, patches)

    def put_permanent(self, y: int, m: int, patches: list[dict]):
        self._write(self._perm, y, m, patches)

    # ---- 旋转 ----

    def rotate(self):
        """月初将上月日级升级为永久。"""
        now = datetime.now(BEIJING_TZ)
        py, pm = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
        key = f"{py}-{pm:02d}"
        dp = self._daily / f"{key}.json"
        if dp.exists():
            try:
                data = json.loads(dp.read_text(encoding="utf-8"))
            except Exception:
                return
            self._write(self._perm, py, pm, data.get("patches", []))
            dp.unlink(missing_ok=True)
            logger.info(f"[cache] 旋转: {key} 日级→永久")

    # ---- 批量预热 ----

    async def warmup(self, fetch_fn, parse_fn, sy, sm, ey, em):
        """批量下载并永久缓存 [sy/sm, ey/em] 范围的补丁。"""
        tpl = "https://overwatch.blizzard.com/en-us/news/patch-body/live/{year}/{month:02d}/"
        ok = fail = 0
        y, m = sy, sm
        while (y, m) <= (ey, em):
            k = f"{y}-{m:02d}"
            logger.info(f"[cache] 预热: {k}")
            url = tpl.replace("{year}", str(y)).replace("{month:02d}", f"{m:02d}")
            html = await fetch_fn(url, timeout=60, user_agent=None, proxy=None, force_refresh=True)
            if html:
                patches = parse_fn(html)
                if patches:
                    self.put_permanent(y, m, patches)
                    ok += 1
                else:
                    fail += 1
            else:
                fail += 1
            m += 1
            if m > 12:
                m, y = 1, y + 1
        logger.info(f"[cache] 预热完成: 成功 {ok}, 失败 {fail}")
        return ok, fail

    # ---- 状态 ----

    def status(self) -> dict:
        return {
            "permanent": len(list(self._perm.glob("*.json"))),
            "daily": len(list(self._daily.glob("*.json"))),
            "short": len(list(self._short.glob("*.json"))),
        }

    def data_dir(self) -> Path:
        return self._perm.parent  # cache/

    # ---- 内部 ----

    def _write(self, ddir: Path, y: int, m: int, patches: list[dict]):
        p = ddir / f"{y}-{m:02d}.json"
        p.write_text(json.dumps(
            {"_saved_at": time.time(), "patches": patches},
            ensure_ascii=False, indent=2,
        ), encoding="utf-8")

"""
本地补丁缓存管理器 — 单级永久缓存
所有月份统一缓存在 permanent 目录，永不自动过期。
"""

import json
from pathlib import Path
from astrbot.api import logger


class PatchCache:
    """单级永久补丁缓存。"""

    def __init__(self, data_dir: Path):
        self._cache_dir = data_dir / "cache" / "permanent"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ---- 读取 ----

    def get(self, year: int, month: int) -> list[dict] | None:
        """获取永久缓存的补丁数据。"""
        key = f"{year}-{month:02d}"
        p = self._cache_dir / f"{key}.json"
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data.get("patches", [])
        except Exception as e:
            logger.warning(f"[cache] 读取缓存失败 {key}: {e}")
            return None

    # ---- 写入 ----

    def put(self, y: int, m: int, patches: list[dict]):
        """写入永久缓存。"""
        key = f"{y}-{m:02d}"
        p = self._cache_dir / f"{key}.json"
        p.write_text(json.dumps(
            {"patches": patches},
            ensure_ascii=False, indent=2,
        ), encoding="utf-8")
        logger.info(f"[cache] 已缓存: {key}")

    # ---- 批量预热 ----

    async def warmup(self, fetch_fn, parse_fn, url_template, sy, sm, ey, em):
        """批量下载并永久缓存 [sy/sm, ey/em] 范围的补丁。

        Args:
            fetch_fn: 异步 HTTP 获取函数
            parse_fn: HTML 解析函数
            url_template: URL 模板，含 {year} 和 {month:02d} 占位符
            sy, sm: 起始年月
            ey, em: 结束年月
        """
        ok = fail = 0
        y, m = sy, sm
        while (y, m) <= (ey, em):
            k = f"{y}-{m:02d}"
            logger.info(f"[cache] 预热: {k}")
            url = url_template.replace("{year}", str(y)).replace("{month:02d}", f"{m:02d}")
            html = await fetch_fn(url, timeout=60, user_agent=None, proxy=None, force_refresh=True)
            if html:
                patches = parse_fn(html)
                if patches:
                    self.put(y, m, patches)
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
            "cached_months": len(list(self._cache_dir.glob("*.json"))),
        }

    @property
    def cache_path(self) -> Path:
        return self._cache_dir

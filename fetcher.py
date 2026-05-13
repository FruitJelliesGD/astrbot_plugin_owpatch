"""
HTTP 页面获取器 — 使用 httpx 异步请求暴雪补丁页面，内置 TTL 缓存防止频繁请求触发风控
"""

import time
import httpx
from astrbot.api import logger

# ---------- 缓存 ----------

_cache: dict[str, tuple[float, str]] = {}  # url -> (expire_at, html)
CACHE_TTL = 600  # 缓存有效期：10 分钟


def clear_cache(url: str | None = None) -> None:
    """清除缓存。不传 url 则清除全部。"""
    if url:
        _cache.pop(url, None)
    else:
        _cache.clear()
    logger.info(f"[fetcher] 缓存已清除 ({url or '全部'})")


def get_cache_info() -> dict:
    """获取缓存信息（调试用）。"""
    now = time.time()
    return {
        "count": len(_cache),
        "urls": {
            u: f"TTL {(exp - now):.0f}s" for u, (exp, _) in _cache.items()
        },
    }


# ---------- 页面获取 ----------

async def fetch_page(
    url: str,
    timeout: int = 60,
    user_agent: str | None = None,
    proxy: str | None = None,
    force_refresh: bool = False,
) -> str | None:
    """使用 httpx 异步获取页面 HTML 文本（带 TTL 缓存）。

    Args:
        url: 目标页面 URL
        timeout: 请求超时秒数
        user_agent: 自定义 User-Agent
        proxy: HTTP 代理地址，如 "http://127.0.0.1:7890"
        force_refresh: 为 True 时跳过缓存强制重新请求

    Returns:
        页面 HTML 字符串；失败时返回 None
    """
    # --- 命中缓存 ---
    now = time.time()
    if not force_refresh and url in _cache:
        expire_at, cached_html = _cache[url]
        if now < expire_at:
            logger.info(f"[fetcher] 命中缓存，剩余 TTL {(expire_at - now):.0f}s: {url}")
            return cached_html
        else:
            # 过期，删除
            del _cache[url]
            logger.debug(f"[fetcher] 缓存已过期: {url}")

    # --- 实际请求 ---
    headers = {}
    if user_agent:
        headers["User-Agent"] = user_agent

    client_kwargs: dict = {
        "timeout": timeout,
        "follow_redirects": True,
    }
    if proxy:
        client_kwargs["proxy"] = proxy
        logger.info(f"[fetcher] 使用代理: {proxy}")

    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            html = resp.text
            logger.info(f"[fetcher] 成功获取页面: {url} (状态码 {resp.status_code})")

            # 写入缓存
            _cache[url] = (now + CACHE_TTL, html)

            return html
    except httpx.TimeoutException:
        logger.error(f"[fetcher] 请求超时 ({timeout}s): {url}")
        return None
    except httpx.HTTPStatusError as e:
        logger.error(f"[fetcher] HTTP 错误 {e.response.status_code}: {url}")
        return None
    except Exception as e:
        logger.error(f"[fetcher] 请求异常: {url} — {e}")
        return None


def build_monthly_url(year: int, month: int, template: str) -> str:
    """根据年月构造月度补丁页面 URL。

    Args:
        year: 年份（如 2026）
        month: 月份（1-12）
        template: URL 模板，含 {year} 和 {month:02d} 占位符

    Returns:
        格式化后的 URL
    """
    url = template.replace("{year}", str(year))
    url = url.replace("{month:02d}", f"{month:02d}")
    return url

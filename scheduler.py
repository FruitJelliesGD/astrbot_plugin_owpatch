"""
定时调度器 — 在每天指定窗口内自动轮询检查新补丁
基于 asyncio 实现，无需额外依赖
"""

import asyncio
from datetime import datetime, time, timedelta, timezone

from astrbot.api import logger

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))


class PatchScheduler:
    """轻量级 asyncio 定时调度器。"""

    def __init__(
        self,
        check_callback,          # async callable: 执行检查逻辑
        get_config,              # callable: 获取配置值
        get_today_pushed,        # callable: 返回当日是否已推送
    ):
        self._check_callback = check_callback
        self._get_config = get_config
        self._get_today_pushed = get_today_pushed
        self._task: asyncio.Task | None = None
        self._running = False

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动调度器后台任务。"""
        if self._running:
            logger.warning("[scheduler] 调度器已在运行")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("[scheduler] 调度器已启动")

    async def stop(self) -> None:
        """停止调度器。"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[scheduler] 调度器已停止")

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """调度器主循环：判断时间窗口 → 轮询 / 休眠。"""
        while self._running:
            try:
                now = self._now_beijing()
                interval = self._get_interval_minutes()
                start_time, end_time = self._get_window_times()

                if self._in_window(now, start_time, end_time):
                    # 在窗口内：检查当日是否已推送
                    if self._get_today_pushed():
                        logger.debug(
                            f"[scheduler] 今日已推送，跳过检查（{interval}分钟后重试）"
                        )
                        await asyncio.sleep(interval * 60)
                        continue

                    # 执行检查
                    logger.info(f"[scheduler] 窗口内触发检查: {now.strftime('%H:%M:%S')}")
                    try:
                        await self._check_callback()
                    except Exception as e:
                        logger.error(f"[scheduler] 检查回调异常: {e}")

                    # 等待间隔
                    await asyncio.sleep(interval * 60)
                else:
                    # 在窗口外：计算到次日窗口开始的秒数
                    sleep_seconds = self._seconds_until_next_window(now, start_time)
                    logger.info(
                        f"[scheduler] 不在窗口内（当前 {now.strftime('%H:%M')}），"
                        f"休眠 {sleep_seconds / 60:.0f} 分钟到次日 {start_time}"
                    )
                    await asyncio.sleep(sleep_seconds)

            except asyncio.CancelledError:
                logger.info("[scheduler] 调度器任务被取消")
                break
            except Exception as e:
                logger.error(f"[scheduler] 调度循环异常: {e}")
                # 出错后等待 60 秒重试
                await asyncio.sleep(60)

    # ------------------------------------------------------------------
    # 时间计算
    # ------------------------------------------------------------------

    @staticmethod
    def _now_beijing() -> datetime:
        return datetime.now(BEIJING_TZ)

    def _get_interval_minutes(self) -> int:
        try:
            return int(self._get_config("check_interval_minutes", 10))
        except (ValueError, TypeError):
            return 10

    def _get_window_times(self) -> tuple[time, time]:
        """解析配置中的窗口开始/结束时间。"""
        start_str = str(self._get_config("window_start_time", "01:50"))
        end_str = str(self._get_config("window_end_time", "04:00"))
        try:
            start_h, start_m = map(int, start_str.split(":"))
            end_h, end_m = map(int, end_str.split(":"))
            return time(start_h, start_m), time(end_h, end_m)
        except (ValueError, AttributeError):
            return time(1, 50), time(4, 0)

    @staticmethod
    def _in_window(now: datetime, start: time, end: time) -> bool:
        """判断当前时间是否在窗口内。

        支持跨午夜窗口（如 23:00~02:00），也支持常规窗口（如 01:50~04:00）。
        """
        current = now.time()
        if start <= end:
            # 常规窗口，如 01:50 ~ 04:00
            return start <= current <= end
        else:
            # 跨午夜窗口，如 23:00 ~ 02:00
            return current >= start or current <= end

    @staticmethod
    def _seconds_until_next_window(now: datetime, start: time) -> int:
        """计算到下一个窗口开始的秒数。"""
        tomorrow = now.date() + timedelta(days=1)
        next_start = datetime.combine(tomorrow, start, tzinfo=BEIJING_TZ)
        delta = (next_start - now).total_seconds()
        return max(int(delta), 60)  # 至少等待 60 秒

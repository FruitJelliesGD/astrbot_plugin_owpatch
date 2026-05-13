"""
守望先锋补丁监控插件 — 主入口
AstrBot 插件，继承 Star 基类
"""

import asyncio
from datetime import datetime, timedelta, timezone

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

from .config import (
    KEY_CHECK_INTERVAL,
    KEY_WINDOW_START,
    KEY_WINDOW_END,
    KEY_BASE_URL_TEMPLATE,
    KEY_USER_AGENT,
    KEY_REQUEST_TIMEOUT,
    KEY_PROXY,
    KEY_CACHE_TTL,
    KEY_INCLUDE_STADIUM,
    DEFAULT_CHECK_INTERVAL,
    DEFAULT_WINDOW_START,
    DEFAULT_WINDOW_END,
    DEFAULT_BASE_URL,
    DEFAULT_USER_AGENT,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_PROXY,
    DEFAULT_CACHE_TTL,
    DEFAULT_INCLUDE_STADIUM,
)
from . import fetcher as fetcher_mod
from .fetcher import fetch_page, build_monthly_url
from .parser import parse_patches, get_latest_patch, get_patch_dates, compute_content_hash, filter_stadium, compute_section_hashes, get_delta_sections
from .state_manager import StateManager
from .message_builder import (
    build_patch_message,
    build_date_list_message,
    build_no_update_message,
    build_bind_success_message,
    build_unbind_success_message,
    build_status_message,
    build_help_message,
    build_delta_message,
)
from .cache_manager import PatchCache
from .scheduler import PatchScheduler
from .forward_builder import build_raw_forward

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))


@register("astrbot_plugin_owpatch", "果冻大神", "守望先锋补丁监控 — 自动推送最新补丁日志", "1.0.0")
class OWPatchPlugin(Star):
    """守望先锋补丁监控插件。

    功能：
    - 每天凌晨窗口内自动轮询暴雪补丁页面
    - 发现新补丁后推送到绑定的会话（UMO）
    - 支持手动检查、历史补丁查询
    """

    def __init__(self, context: Context):
        super().__init__(context)
        self.state_mgr = StateManager()
        self.patch_cache: PatchCache | None = None
        self.scheduler: PatchScheduler | None = None
        self._check_lock = asyncio.Lock()

    # ==================================================================
    # 生命周期
    # ==================================================================

    async def initialize(self):
        """异步初始化：加载状态、启动调度器。"""
        logger.info("[owpatch] 插件初始化中...")

        # 同步缓存 TTL 配置
        ttl = int(self._get_config(KEY_CACHE_TTL, DEFAULT_CACHE_TTL))
        fetcher_mod.CACHE_TTL = ttl * 60
        logger.info(f"[owpatch] 缓存 TTL 设置为 {ttl} 分钟")

        # 初始化状态管理器
        self.state_mgr.init_data_dir()
        self.state_mgr.load()

        # 初始化缓存管理器 + 月初旋转
        if self.state_mgr.data_dir:
            self.patch_cache = PatchCache(self.state_mgr.data_dir)
            self.patch_cache.rotate()
            logger.info("[owpatch] 三级缓存已初始化")

        # 检查跨天重置
        today = self._now_beijing_str()
        self.state_mgr.reset_daily_if_new_day(today)

        # 首次安装：静默记录当前最新补丁为基线，不触发推送
        if not self.state_mgr.get_last_patch_date():
            logger.info("[owpatch] 首次安装，正在建立基线（不会推送已有补丁）...")
            try:
                await self._init_baseline()
            except Exception as e:
                logger.warning(f"[owpatch] 基线建立失败（不影响后续使用）: {e}")

        # 启动定时调度器
        self.scheduler = PatchScheduler(
            check_callback=self._scheduled_check,
            get_config=self._get_config,
            get_today_pushed=lambda: self.state_mgr.today_pushed,
        )
        await self.scheduler.start()

        logger.info(
            f"[owpatch] 插件初始化完成 | "
            f"绑定: {self.state_mgr.umo_count()} 个会话 | "
            f"最新补丁: {self.state_mgr.get_last_patch_date() or '无'}"
        )

    async def terminate(self):
        """插件卸载时停止调度器。"""
        if self.scheduler:
            await self.scheduler.stop()
        logger.info("[owpatch] 插件已停止")

    # ==================================================================
    # 指令：绑定 / 解绑
    # ==================================================================

    @filter.command_group("owpatch")
    def owpatch(self):
        """守望先锋补丁监控指令组"""
        pass

    @owpatch.command("bind")
    async def cmd_bind(self, event: AstrMessageEvent):
        """绑定当前会话接收补丁推送。"""
        umo = event.unified_msg_origin
        is_new = self.state_mgr.add_umo(umo)
        if is_new:
            yield event.plain_result(
                build_bind_success_message(self.state_mgr.umo_count())
            )
        else:
            yield event.plain_result("当前会话已绑定，无需重复操作。")
        event.stop_event()

    @owpatch.command("unbind")
    async def cmd_unbind(self, event: AstrMessageEvent):
        """解绑当前会话。"""
        umo = event.unified_msg_origin
        removed = self.state_mgr.remove_umo(umo)
        if removed:
            yield event.plain_result(
                build_unbind_success_message(self.state_mgr.umo_count())
            )
        else:
            yield event.plain_result("当前会话未绑定。")
        event.stop_event()

    # ==================================================================
    # 指令：状态查询 / 帮助
    # ==================================================================

    @owpatch.command("status")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看当前监控状态。"""
        yield event.plain_result(
            build_status_message(
                umo_count=self.state_mgr.umo_count(),
                last_patch_date=self.state_mgr.get_last_patch_date(),
                today_pushed=self.state_mgr.today_pushed,
                window_start=self._get_config(KEY_WINDOW_START, DEFAULT_WINDOW_START),
                window_end=self._get_config(KEY_WINDOW_END, DEFAULT_WINDOW_END),
            )
        )
        event.stop_event()

    @owpatch.command("help")
    async def cmd_help(self, event: AstrMessageEvent):
        """显示帮助信息。"""
        yield event.plain_result(build_help_message())
        event.stop_event()

    # ==================================================================
    # 指令：缓存管理
    # ==================================================================

    @owpatch.command("cache")
    async def cmd_cache(self, event: AstrMessageEvent, action: str = ""):
        """管理本地补丁缓存。

        /owpatch cache status  → 查看缓存统计
        /owpatch cache         → 批量预热历史补丁到本地
        """
        if not self.patch_cache:
            yield event.plain_result("❌ 缓存未初始化")
            event.stop_event()
            return

        if action == "status":
            s = self.patch_cache.status()
            yield event.plain_result(
                f"📦 缓存状态\n"
                f"  永久: {s['permanent']} 个月\n"
                f"  日级: {s['daily']} 个月\n"
                f"  短时: {s['short']} 个月"
            )
            event.stop_event()
            return

        # 计算预热范围：2016/05 ~ (当前 - 2月)
        now = datetime.now(BEIJING_TZ)
        em = now.month - 2
        ey = now.year
        if em <= 0:
            em += 12
            ey -= 1

        yield event.plain_result(
            f"🔄 开始预热缓存 (2016/05 ~ {ey}/{em:02d})，将逐月下载并永久存储...\n"
            f"  预计需要数分钟，请耐心等待。"
        )
        try:
            ok, fail = await self.patch_cache.warmup(
                fetch_page, parse_patches,
                2016, 5, ey, em,
            )
        except Exception as e:
            logger.error(f"[owpatch] 预热失败: {e}")
            yield event.plain_result(f"❌ 预热失败: {e}")
            event.stop_event()
            return

        yield event.plain_result(
            f"✅ 缓存预热完成！成功 {ok} 个月，失败 {fail} 个月"
        )
        event.stop_event()

    # ==================================================================
    # 指令：手动检查
    # ==================================================================

    @owpatch.command("check")
    async def cmd_check(self, event: AstrMessageEvent):
        """立即检查是否有新补丁。"""
        yield event.plain_result("🔍 正在检查新补丁，请稍候...")

        try:
            found = await self._check_and_notify()
        except Exception as e:
            logger.error(f"[owpatch] 手动检查异常: {e}")
            yield event.plain_result(f"❌ 检查失败: {e}")
            event.stop_event()
            return

        if found:
            yield event.plain_result(
                f"✅ 发现新补丁！已推送到 {self.state_mgr.umo_count()} 个绑定会话。"
            )
            event.stop_event()
            return

        # 未发现新补丁 → 先检查本地记录
        last = self.state_mgr.get_last_patch_date()
        if last:
            yield event.plain_result(
                f"📭 当前没有新补丁，最新补丁日期为 {last}"
            )
            event.stop_event()
            return

        # 完全没有记录 → 回溯上月
        try:
            last_date = await self._find_prev_month_latest()
        except Exception as e:
            logger.warning(f"[owpatch] 回溯上月失败: {e}")
            last_date = None

        if last_date:
            yield event.plain_result(
                f"📭 当月暂无新补丁，上一次补丁日期为 {last_date}"
            )
        else:
            yield event.plain_result(build_no_update_message())
        event.stop_event()

    # ==================================================================
    # 指令：历史查询
    # ==================================================================

    @owpatch.command("query")
    async def cmd_query(self, event: AstrMessageEvent, month: str = "", day: str = ""):
        """查询指定年份/月份的补丁日志。

        用法：
            /owpatch query 2025        → 列出 2025 年有补丁的月份
            /owpatch query 2025 4      → 列出 2025年4月 所有补丁日期
            /owpatch query 2025 4 28   → 推送 2025年4月28日 的补丁内容
            /owpatch query 4           → 列出当年4月所有补丁日期
            /owpatch query 4 28        → 推送当年4月28日的补丁内容
            /owpatch query 2025/04     → 同上，支持 / 分隔年月
            /owpatch query 2025-04     → 同上，支持 - 分隔年月
        """
        if not month:
            yield event.plain_result(
                "请指定年份或月份。用法：\n"
                "/owpatch query <年份> [月份] [日期]  — 查询指定年份/月份\n"
                "/owpatch query <月份> [日期]        — 查询当年月份\n"
                "例如：/owpatch query 2025 或 /owpatch query 4 或 /owpatch query 2025 4 28"
            )
            event.stop_event()
            return

        # ── 智能检测：用户输入可能是 2025 4（年+月）────
        # 仅当 month 不是 YYYY/MM / YYYY-MM 格式时进行检测
        if "/" not in month and "-" not in month:
            month_int = self._try_parse_int(month)
            day_int = self._try_parse_int(day) if day else None
            # month 是 4 位数年份（如 2025），且 day 是 1-12 的月份 → 互换
            if (
                month_int is not None and month_int >= 2016
                and day_int is not None and 1 <= day_int <= 12
            ):
                # 格式: /owpatch query 2025 4 [date]
                # → 转换为 year=month, month=day, day=后续参数
                year = month_int
                month_num = day_int
                # 检查是否有第三个参数被当作 day 传入
                # 在 AstrBot 双参数模型下无法直接获取，通过事件原始消息提取
                raw_msg = event.message_str or ""
                day = self._extract_third_arg(raw_msg)
                month_label = f"{year}年{month_num}月"
            elif (
                month_int is not None and 1 <= month_int <= 12
            ):
                # 格式: /owpatch query 4 [day]
                year, month_num = self._parse_query_month(month)
                if month_num is None:
                    yield event.plain_result(
                        f"无法解析月份 '{month}'。请使用数字（如 4）或 YYYY/MM 格式。"
                    )
                    event.stop_event()
                    return
                month_label = f"{year}年{month_num}月"
            else:
                # month 可能是年份
                year, month_num = self._parse_query_month(month)
                if month_num is not None:
                    month_label = f"{year}年{month_num}月"
                else:
                    # 纯年份 → 列出整年
                    yield event.plain_result(f"🔍 正在查询 {year} 年的补丁记录...")
                    try:
                        result = await self._query_year_summary(year)
                        yield event.plain_result(result)
                    except Exception as e:
                        logger.error(f"[owpatch] 年份查询异常: {e}")
                        yield event.plain_result(f"❌ 查询失败: {e}")
                    event.stop_event()
                    return
        else:
            # 格式: YYYY/MM 或 YYYY-MM
            year, month_num = self._parse_query_month(month)
            if month_num is None:
                yield event.plain_result(
                    f"无法解析 '{month}'。请使用 YYYY/MM 或 YYYY-MM 格式。"
                )
                event.stop_event()
                return
            month_label = f"{year}年{month_num}月"

        # ── 以下为标准月查询/日查询逻辑 ──
        url = build_monthly_url(
            year, month_num,
            self._get_config(KEY_BASE_URL_TEMPLATE, DEFAULT_BASE_URL)
        )

        # 优先从缓存读取（当月不缓存，始终实时）
        now = datetime.now(BEIJING_TZ)
        is_current_month = year == now.year and month_num == now.month
        all_patches = None
        if not is_current_month:
            all_patches = self.patch_cache.get(year, month_num) if self.patch_cache else None
            if all_patches is not None:
                all_patches = self._apply_stadium_filter(all_patches)

        if all_patches is None:
            # 缓存未命中 → HTTP 获取
            html = await fetch_page(
                url,
                timeout=self._get_config(KEY_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT),
                user_agent=self._get_config(KEY_USER_AGENT, DEFAULT_USER_AGENT),
                proxy=self._get_proxy(),
                force_refresh=is_current_month,  # 当月强制刷新
            )
            if html is None:
                yield event.plain_result(f"❌ 无法获取 {month_label} 的补丁页面，请稍后重试。")
                event.stop_event()
                return

            all_patches = self._apply_stadium_filter(parse_patches(html))

            # 按规则写入缓存
            if self.patch_cache and all_patches:
                self._put_cache(year, month_num, all_patches)

        if not all_patches:
            yield event.plain_result(f"{month_label} 没有找到补丁记录。")
            event.stop_event()
            return

        if not day:
            # 列出所有日期
            dates = get_patch_dates(all_patches)
            yield event.plain_result(build_date_list_message(dates, month_label))
            event.stop_event()
            return

        # 查找指定日期的补丁
        day_int = int(day)
        target_date = f"{year}-{month_num:02d}-{day_int:02d}"
        target_patch = None
        for p in all_patches:
            if p["date"] == target_date:
                target_patch = p
                break

        if target_patch is None:
            yield event.plain_result(
                f"{month_label} 没有找到日期为 {target_date} 的补丁。\n"
                f"可用日期：{', '.join(get_patch_dates(all_patches))}"
            )
            event.stop_event()
            return

        # 发送 — aiocqhttp 走原始嵌套转发
        platform = event.get_platform_name() or ""
        sender_id = event.get_sender_id() or ""
        uin = int(sender_id) if sender_id.isdigit() else 0

        if platform == "aiocqhttp" and uin:
            try:
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                if isinstance(event, AiocqhttpMessageEvent):
                    raw_fwds = build_raw_forward(target_patch["title"], target_patch["sections"], uin)
                    gid = event.message_obj.group_id
                    for fwd in raw_fwds:
                        if gid:
                            await event.bot.call_action("send_group_forward_msg", group_id=int(gid), messages=fwd)
                        else:
                            await event.bot.call_action("send_private_forward_msg", user_id=uin, messages=fwd)
                    event.stop_event()
                    return
            except Exception as e:
                logger.warning(f"[owpatch] 原始转发失败，回退: {e}")

        # 非 aiocqhttp 回退
        chains = build_patch_message(
            title=target_patch["title"],
            text=target_patch["text"],
            sections=target_patch["sections"],
            platform_name=platform,
            bot_self_id=sender_id,
        )
        for chain in chains:
            yield event.chain_result(chain)
        event.stop_event()

    # ==================================================================
    # 核心逻辑
    # ==================================================================

    async def _scheduled_check(self):
        """定时调度器回调（加锁保护）。"""
        async with self._check_lock:
            await self._check_and_notify()

    async def _check_and_notify(self) -> bool:
        """核心检查：获取 → 解析 → 比较（整版 + 节级） → 推送。"""
        urls = self._get_target_urls()
        all_patches = []

        for url in urls:
            logger.info(f"[owpatch] 检查: {url}")
            html = await fetch_page(
                url,
                timeout=self._get_config(KEY_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT),
                user_agent=self._get_config(KEY_USER_AGENT, DEFAULT_USER_AGENT),
                proxy=self._get_proxy(),
                force_refresh=True,
            )
            if html is None:
                logger.warning(f"[owpatch] 获取失败，跳过: {url}")
                continue
            all_patches.extend(self._apply_stadium_filter(parse_patches(html)))

        if not all_patches:
            logger.info("[owpatch] 所有页面均无补丁数据")
            return False

        latest = get_latest_patch(all_patches)
        if latest is None:
            return False

        latest_date = latest["date"]
        latest_hash = compute_content_hash(latest["raw_html"])
        sections = latest["sections"]
        current_hashes = compute_section_hashes(sections)

        # --- 情况 1：全新补丁 ---
        if self.state_mgr.is_new_patch(latest_date, latest_hash):
            logger.info(f"[owpatch] 发现新补丁！日期: {latest_date}")
            self.state_mgr.mark_pushed(latest_date, latest_hash, current_hashes)
            return await self._push_full(latest)

        # --- 情况 2：同一补丁，节级增量检测 ---
        delta_headings = self.state_mgr.find_delta_sections(current_hashes)

        # Stadium 关闭时过滤 Stadium 增量
        if not self._get_config(KEY_INCLUDE_STADIUM, DEFAULT_INCLUDE_STADIUM):
            delta_headings = [h for h in delta_headings if "stadium" not in h.lower()]

        if delta_headings:
            delta_sections = get_delta_sections(sections, delta_headings)
            self.state_mgr.mark_pushed(latest_date, latest_hash, current_hashes)
            return await self._push_delta(latest, delta_sections)

        logger.info(f"[owpatch] 补丁无变化（最新: {latest_date}）")
        return False

    async def _push_full(self, latest: dict) -> bool:
        """推送完整补丁——aiocqhttp 走原始嵌套转发。"""
        umos = self.state_mgr.get_umos()
        if not umos:
            return True
        try:
            import re
            platform = self.context.get_platform(filter.PlatformAdapterType.AIOCQHTTP)
            if platform:
                raw_fwds = build_raw_forward(latest["title"], latest["sections"], 0)
                cl = platform.get_client().api
                for umo in umos:
                    g = re.search(r'GroupMessage:(\d+)', umo)
                    u = re.search(r'FriendMessage:(\d+)', umo)
                    for fwd in raw_fwds:
                        if g:
                            await cl.call_action("send_group_forward_msg", group_id=int(g.group(1)), messages=fwd)
                        elif u:
                            await cl.call_action("send_private_forward_msg", user_id=int(u.group(1)), messages=fwd)
                return True
        except Exception as e:
            logger.warning(f"[owpatch] 原始转发失败，回退: {e}")

        chains = build_patch_message(
            title=latest["title"], text=latest["text"],
            sections=latest["sections"],
            platform_name="aiocqhttp", bot_self_id="",
        )
        return await self._send_to_umos(umos, chains, "推送")

    async def _push_delta(self, latest: dict, delta_sections: list[dict]) -> bool:
        """推送增量追加内容。"""
        umos = self.state_mgr.get_umos()
        if not umos:
            return True
        date_label = latest["date"][5:]  # "2026-05-12" → "05-12"
        chains = build_delta_message(date_label, delta_sections)
        return await self._send_to_umos(umos, chains, "增量推送")

    async def _send_to_umos(self, umos: list[str], chains: list, label: str) -> bool:
        """遍历 UMO 发送消息链。"""
        success = 0
        for umo in umos:
            try:
                for cl in chains:
                    await self.context.send_message(umo, MessageChain(chain=cl))
                success += 1
            except Exception as e:
                logger.error(f"[owpatch] {label}失败 ({umo}): {e}")
        logger.info(f"[owpatch] {label}完成: {success}/{len(umos)} 成功")
        return True

    # ==================================================================
    # 回溯上月
    # ==================================================================

    async def _find_prev_month_latest(self) -> str | None:
        """查询上个月的最新补丁日期，用于月末/月初无补丁时的友好提示。"""
        now = datetime.now(BEIJING_TZ)
        if now.month == 1:
            y, m = now.year - 1, 12
        else:
            y, m = now.year, now.month - 1

        template = self._get_config(KEY_BASE_URL_TEMPLATE, DEFAULT_BASE_URL)
        url = build_monthly_url(y, m, template)

        html = await fetch_page(
            url,
            timeout=self._get_config(KEY_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT),
            user_agent=self._get_config(KEY_USER_AGENT, DEFAULT_USER_AGENT),
            proxy=self._get_proxy(),
            force_refresh=False,
        )
        if not html:
            return None

        patches = self._apply_stadium_filter(parse_patches(html))
        latest = get_latest_patch(patches)
        if latest and latest["date"] != "unknown":
            return latest["date"]
        return None

    # ==================================================================
    # 基线建立
    # ==================================================================

    async def _init_baseline(self):
        """首次安装时静默抓取当前最新补丁，记录为基线（不推送）。"""
        urls = self._get_target_urls()
        all_patches = []

        for url in urls:
            html = await fetch_page(
                url,
                timeout=self._get_config(KEY_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT),
                user_agent=self._get_config(KEY_USER_AGENT, DEFAULT_USER_AGENT),
                proxy=self._get_proxy(),
                force_refresh=True,
            )
            if html:
                all_patches.extend(self._apply_stadium_filter(parse_patches(html)))

        if not all_patches:
            logger.warning("[owpatch] 基线建立失败：所有页面均无数据")
            return

        latest = get_latest_patch(all_patches)
        if latest:
            latest_hash = compute_content_hash(latest["raw_html"])
            section_hashes = compute_section_hashes(latest["sections"])
            self.state_mgr.set_baseline(latest["date"], latest_hash, section_hashes)
            logger.info(
                f"[owpatch] 基线已建立: {latest['date']} "
                f"(此后出现的更新才会推送)"
            )

    # ==================================================================
    # URL 构造
    # ==================================================================

    def _get_target_urls(self) -> list[str]:
        """确定需要检查的 URL 列表。

        北京时间每月 1 号同时检查上月和当月（防止美区月末时差遗漏）。
        """
        now = datetime.now(BEIJING_TZ)
        template = self._get_config(KEY_BASE_URL_TEMPLATE, DEFAULT_BASE_URL)
        urls = []

        if now.day == 1:
            # 1 号：同时检查上月和当月
            # 上月
            if now.month == 1:
                prev_year, prev_month = now.year - 1, 12
            else:
                prev_year, prev_month = now.year, now.month - 1
            urls.append(build_monthly_url(prev_year, prev_month, template))
            logger.info(f"[owpatch] 月初双月检查：上月 {prev_year}/{prev_month:02d} + 当月")

        # 当月
        urls.append(build_monthly_url(now.year, now.month, template))
        return urls

    # ==================================================================
    # 工具方法
    # ==================================================================

    @staticmethod
    def _try_parse_int(s: str) -> int | None:
        """安全地将字符串转为整数，失败返回 None。"""
        try:
            return int(s.strip())
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _extract_third_arg(raw_msg: str) -> str:
        """从原始消息中提取 query 指令的第三个参数（日期）。

        AstrBot 命令框架只捕获前两个参数到 month/day，
        当用户输入 `/owpatch query 2025 4 28` 时，需要从原始消息提取 28。
        """
        import re
        # 匹配 owpatch query <arg1> <arg2> <arg3>，容忍前缀（如 / 或 bot 提及）
        m = re.search(r'\bowpatch\s+query\s+\S+\s+\S+\s+(\d{1,2})\b', raw_msg, re.IGNORECASE)
        if m:
            return m.group(1)
        return ""

    async def _query_year_summary(self, year: int) -> str:
        """查询指定年份所有月份的补丁概况，返回摘要文本。

        优先使用本地缓存，缓存未命中则实时请求（每个请求间有短暂间隔防止风控）。
        """
        now = datetime.now(BEIJING_TZ)
        template = self._get_config(KEY_BASE_URL_TEMPLATE, DEFAULT_BASE_URL)
        timeout = self._get_config(KEY_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT)
        ua = self._get_config(KEY_USER_AGENT, DEFAULT_USER_AGENT)
        proxy = self._get_proxy()

        months_with_patches: dict[int, list[str]] = {}  # month -> [date, ...]
        empty_months: list[int] = []
        failed_months: list[int] = []

        for m in range(1, 13):
            # 超过当前月份的未来月份跳过
            if year == now.year and m > now.month:
                break

            patches: list[dict] | None = None

            # 优先缓存
            if self.patch_cache:
                patches = self.patch_cache.get(year, m)
                if patches is not None:
                    patches = self._apply_stadium_filter(patches)

            # 缓存未命中 → HTTP 请求
            if patches is None:
                url = build_monthly_url(year, m, template)
                html = await fetch_page(
                    url, timeout=timeout, user_agent=ua,
                    proxy=proxy, force_refresh=True,
                )
                if html is None:
                    failed_months.append(m)
                    continue
                patches = self._apply_stadium_filter(parse_patches(html))
                if self.patch_cache and patches:
                    self._put_cache(year, m, patches)

            if patches:
                dates = get_patch_dates(patches)
                if dates:
                    months_with_patches[m] = dates
                else:
                    empty_months.append(m)
            else:
                empty_months.append(m)

            # 避免连续请求触发风控
            if m < 12:
                await asyncio.sleep(1.5)

        # 构建结果
        lines = [f"📋 {year} 年守望先锋补丁概览", "=" * 30]
        total_patches = 0

        if months_with_patches:
            for m, dates in sorted(months_with_patches.items()):
                total_patches += len(dates)
                lines.append(f"  {m:02d}月 — {len(dates)} 个补丁")
                for d in dates:
                    lines.append(f"      {d}")
        else:
            lines.append("  没有找到任何补丁记录。")

        lines.append("=" * 30)
        if total_patches > 0:
            lines.append(f"📊 共 {total_patches} 个补丁（{len(months_with_patches)} 个有补丁的月份）")
        if empty_months:
            lines.append(f"📭 无补丁月份: {', '.join(f'{m:02d}' for m in empty_months)}")
        if failed_months:
            lines.append(f"⚠️ 获取失败: {', '.join(f'{m:02d}' for m in failed_months)}")
        lines.append("")
        lines.append("发送 `/owpatch query <年份> <月份>` 查看指定月补丁日期。")
        lines.append('例如：`/owpatch query 2025 4` 查看 2025年4月。')

        return "\n".join(lines)

    def _get_config(self, key: str, default=None):
        """读取插件配置项。

        AstrBot 会将 _conf_schema.json 中定义的配置注入到 self.config 字典。
        """
        try:
            cfg = getattr(self, 'config', None)
            if isinstance(cfg, dict) and key in cfg:
                return cfg[key]
        except Exception:
            pass
        return default

    def _get_proxy(self) -> str | None:
        """获取代理配置，空字符串视为无代理。"""
        p = self._get_config(KEY_PROXY, DEFAULT_PROXY)
        return p.strip() if p and p.strip() else None

    def _apply_stadium_filter(self, patches: list[dict]) -> list[dict]:
        """根据配置过滤 Stadium 内容。"""
        if self._get_config(KEY_INCLUDE_STADIUM, DEFAULT_INCLUDE_STADIUM):
            return patches
        return filter_stadium(patches)

    def _put_cache(self, year: int, month: int, patches: list[dict]) -> None:
        """按三级规则写入缓存（当月不缓存）。"""
        if not self.patch_cache:
            return
        now = datetime.now(BEIJING_TZ)
        if year == now.year and month == now.month:
            return  # 当月始终实时，不缓存
        if self._is_last_month(year, month):
            self.patch_cache.put_daily(year, month, patches)
        else:
            self.patch_cache.put_permanent(year, month, patches)

    @staticmethod
    def _is_last_month(year: int, month: int) -> bool:
        now = datetime.now(BEIJING_TZ)
        py, pm = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
        return year == py and month == pm

    @staticmethod
    def _now_beijing_str() -> str:
        """返回当前北京时间的日期字符串（YYYY-MM-DD）。"""
        return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

    @staticmethod
    def _parse_query_month(raw: str) -> tuple[int, int | None]:
        """解析用户输入的月份参数。

        Args:
            raw: 如 "4", "04", "2025", "2026/04", "2025-04", "2026/4"

        Returns:
            (year, month) — month 为 None 表示只指定了年份（整年查询）
                          若完全无法解析则返回 (now.year, None)
        """
        now = datetime.now(BEIJING_TZ)
        raw = raw.strip()

        # 格式: YYYY/MM 或 YYYY/M
        if "/" in raw:
            parts = raw.split("/")
            try:
                year = int(parts[0])
                month = int(parts[1])
                if 1 <= month <= 12:
                    return year, month
            except ValueError:
                pass

        # 格式: YYYY-MM 或 YYYY-M
        if "-" in raw:
            parts = raw.split("-")
            try:
                year = int(parts[0])
                month = int(parts[1])
                if len(parts) == 2 and 1 <= month <= 12 and 2016 <= year <= now.year:
                    return year, month
            except ValueError:
                pass

        # 格式: YYYY（纯年份，查询整年）
        try:
            year = int(raw)
            if 2016 <= year <= now.year:
                return year, None
        except ValueError:
            pass

        # 格式: 纯数字月份（默认当年）
        try:
            month = int(raw)
            if 1 <= month <= 12:
                return now.year, month
        except ValueError:
            pass

        return now.year, None

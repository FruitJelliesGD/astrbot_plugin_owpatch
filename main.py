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
    KEY_TRANSLATE_PROMPT,
    DEFAULT_CHECK_INTERVAL,
    DEFAULT_WINDOW_START,
    DEFAULT_WINDOW_END,
    DEFAULT_BASE_URL,
    DEFAULT_USER_AGENT,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_PROXY,
    DEFAULT_CACHE_TTL,
    DEFAULT_INCLUDE_STADIUM,
    DEFAULT_TRANSLATE_PROMPT,
)
from . import fetcher as fetcher_mod
from .fetcher import fetch_page, build_monthly_url
from .parser import parse_patches, get_latest_patch, get_patch_dates, compute_content_hash, filter_stadium, compute_section_hashes, diff_sections
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
from . import translator as translator_mod

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
        # 会话级缓存：umo → 最近一次 query 的补丁数据（用于 translate 指令）
        self._last_query: dict[str, dict] = {}

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

        # 初始化缓存管理器（单级永久缓存）
        if self.state_mgr.data_dir:
            self.patch_cache = PatchCache(self.state_mgr.data_dir)
            logger.info("[owpatch] 永久缓存已初始化")

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

    @filter.command_group("owpatch", alias={'ow补丁'})
    def owpatch(self):
        """守望先锋补丁监控指令组"""
        pass

    @owpatch.command("bind", alias={'绑定'})
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

    @owpatch.command("unbind", alias={'解绑'})
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

    @owpatch.command("status", alias={'状态'})
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

    @owpatch.command("help", alias={'帮助'})
    async def cmd_help(self, event: AstrMessageEvent):
        """显示帮助信息。"""
        yield event.plain_result(build_help_message())
        event.stop_event()

    # ==================================================================
    # 指令：缓存管理
    # ==================================================================

    @owpatch.command("cache", alias={'缓存'})
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
                f"  已缓存: {s['cached_months']} 个月"
            )
            event.stop_event()
            return

        # 预热所有历史月份：2016/05 ~ 上月
        now = datetime.now(BEIJING_TZ)
        em = now.month - 1
        ey = now.year
        if em <= 0:
            em += 12
            ey -= 1

        tpl = self._get_config(KEY_BASE_URL_TEMPLATE, DEFAULT_BASE_URL)
        yield event.plain_result(
            f"🔄 开始预热缓存 (2016/05 ~ {ey}/{em:02d})，将逐月下载并永久存储...\n"
            f"  预计需要数分钟，请耐心等待。"
        )
        try:
            ok, fail = await self.patch_cache.warmup(
                fetch_page, parse_patches, tpl,
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

    @owpatch.command("check", alias={'检查'})
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

    @owpatch.command("query", alias={'查询'})
    async def cmd_query(self, event: AstrMessageEvent, arg1: str = "", arg2: str = "", arg3: str = ""):
        """查询补丁记录（仅支持空格分隔参数）。

        用法：
            /owpatch query           → 推送最新补丁
            /owpatch query 2016      → 查看 2016 年全年补丁汇总
            /owpatch query 5         → 列出今年 5 月所有补丁
            /owpatch query 5 4       → 推送今年 5 月 4 日补丁
            /owpatch query 2016 5    → 列出 2016 年 5 月所有补丁
            /owpatch query 2016 5 4  → 推送 2016 年 5 月 4 日补丁
        """
        now_bj = datetime.now(BEIJING_TZ)
        year = now_bj.year
        month_num = None
        day = ""

        # ════════════════════════════════════════════════════════════════
        # 无参数 → 自动定位最新补丁并推送
        # ════════════════════════════════════════════════════════════════
        if not arg1:
            import re
            yield event.plain_result("🔍 正在查找最新补丁，请稍候...")

            urls = self._get_target_urls()
            raw_pool = []
            for url in urls:
                html = await fetch_page(
                    url,
                    timeout=self._get_config(KEY_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT),
                    user_agent=self._get_config(KEY_USER_AGENT, DEFAULT_USER_AGENT),
                    proxy=self._get_proxy(),
                    force_refresh=True,
                )
                if html:
                    patches = parse_patches(html)
                    if patches:
                        ym = re.search(r'/live/(\d{4})/(\d{2})/', url)
                        if ym:
                            self._put_cache(int(ym.group(1)), int(ym.group(2)), patches)
                        raw_pool.extend(patches)

            if not raw_pool:
                # 尝试回退永久缓存（当月 → 上月）
                for y, m in [(now_bj.year, now_bj.month),
                             (now_bj.year if now_bj.month > 1 else now_bj.year - 1,
                              now_bj.month - 1 if now_bj.month > 1 else 12)]:
                    if self.patch_cache:
                        cached = self.patch_cache.get(y, m)
                        if cached:
                            raw_pool.extend(cached)
                            break

            if not raw_pool:
                yield event.plain_result("当前没有找到补丁记录。")
                event.stop_event()
                return

            latest = get_latest_patch(raw_pool)
            if latest is None or latest["date"] == "unknown":
                yield event.plain_result("当前没有找到补丁记录。")
                event.stop_event()
                return

            # 分解最新日期 → 推送给后续流程
            parts = latest["date"].split("-")
            year = int(parts[0])
            month_num = int(parts[1])
            day = str(int(parts[2]))

        # ════════════════════════════════════════════════════════════════
        # 有参数：解析 arg1/arg2/arg3（纯空格分隔，不支持斜杠）
        # ════════════════════════════════════════════════════════════════
        else:
            try:
                # ── 三参数：年 月 日 ──
                if arg3:
                    year = int(arg1)
                    month_num = int(arg2)
                    day = arg3

                # ── 两参数 ──
                elif arg2:
                    n1 = int(arg1)
                    n2 = int(arg2)
                    if 2016 <= n1 <= now_bj.year and 1 <= n2 <= 12:
                        year = n1
                        month_num = n2
                    elif 1 <= n1 <= 12 and 1 <= n2 <= 31:
                        month_num = n1
                        day = arg2
                    else:
                        raise ValueError

                # ── 单参数 ──
                else:
                    n1 = int(arg1)
                    if 2016 <= n1 <= now_bj.year:
                        year = n1
                        month_num = 0  # 全年模式
                    elif 1 <= n1 <= 12:
                        month_num = n1
                    else:
                        raise ValueError

            except (ValueError, TypeError):
                month_num = None

        # ────────────────────────────────────────────────────────────────
        # 参数校验
        # ────────────────────────────────────────────────────────────────
        if month_num is None:
            yield event.plain_result(
                "输入格式有误。支持格式：\n"
                "  /owpatch query           → 最新补丁\n"
                "  /owpatch query 2016      → 年份\n"
                "  /owpatch query 5         → 月份\n"
                "  /owpatch query 5 28      → 月 日\n"
                "  /owpatch query 2016 5    → 年 月\n"
                "  /owpatch query 2016 5 4  → 年 月 日\n"
                "仅支持空格分隔，不支持斜杠（如 2016/5）格式。"
            )
            event.stop_event()
            return

        # ────────────────────────────────────────────────────────────────
        # 全年模式：遍历所有月份汇总展示
        # ────────────────────────────────────────────────────────────────
        if month_num == 0:
            yield event.plain_result(f"🔍 正在查询 {year} 年的补丁记录...")

            import re
            start_m = 5 if year == 2016 else 1
            end_m = now_bj.month if year == now_bj.year else 12
            template = self._get_config(KEY_BASE_URL_TEMPLATE, DEFAULT_BASE_URL)

            all_patches = []
            for m in range(start_m, end_m + 1):
                cached = self.patch_cache.get(year, m) if self.patch_cache else None
                if cached:
                    all_patches.extend(cached)
                    continue
                url = build_monthly_url(year, m, template)
                html = await fetch_page(
                    url,
                    timeout=self._get_config(KEY_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT),
                    user_agent=self._get_config(KEY_USER_AGENT, DEFAULT_USER_AGENT),
                    proxy=self._get_proxy(),
                    force_refresh=True,
                )
                if html:
                    patches = parse_patches(html)
                    if patches:
                        self._put_cache(year, m, patches)
                        all_patches.extend(patches)

            if not all_patches:
                yield event.plain_result(f"{year} 年没有找到补丁记录。")
                event.stop_event()
                return

            seen = set()
            unique = [p for p in all_patches if p["date"] not in seen and not seen.add(p["date"])]
            unique.sort(key=lambda x: x["date"], reverse=True)
            dates = get_patch_dates(unique)

            lines = [f"{year} 年共有 {len(dates)} 个补丁："]
            for i, d in enumerate(dates, 1):
                lines.append(f"  {i}. {d}")
            lines.append("")
            if dates:
                first = dates[0]
                ym = first.split("-")
                lines.append(f"发送 `/owpatch query {int(ym[0])} {int(ym[1])} {int(ym[2])}` 查看指定补丁。")
                lines.append(f"例如：`/owpatch query {int(ym[0])} {int(ym[1])} {int(ym[2])}` 查看最新补丁。")
            else:
                lines.append("发送 `/owpatch query <年份> <月份> <日期>` 查看指定补丁。")
            yield event.plain_result("\n".join(lines))
            event.stop_event()
            return

        month_label = f"{year}年{month_num}月"
        url = build_monthly_url(
            year, month_num,
            self._get_config(KEY_BASE_URL_TEMPLATE, DEFAULT_BASE_URL)
        )

        # ────────────────────────────────────────────────────────────────
        # 第一步：获取本地缓存版本
        # ────────────────────────────────────────────────────────────────
        cached_patches = self.patch_cache.get(year, month_num) if self.patch_cache else None

        # ────────────────────────────────────────────────────────────────
        # 第二步：联网获取最新版本（优先）
        # ────────────────────────────────────────────────────────────────
        html = await fetch_page(
            url,
            timeout=self._get_config(KEY_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT),
            user_agent=self._get_config(KEY_USER_AGENT, DEFAULT_USER_AGENT),
            proxy=self._get_proxy(),
            force_refresh=True,
        )

        online_patches = None
        online_failed = False
        if html is not None:
            online_patches = parse_patches(html)
            if online_patches:
                # 写入永久缓存（原始完整数据，不做 Stadium 过滤）
                self._put_cache(year, month_num, online_patches)
        else:
            online_failed = True
            logger.warning(f"[owpatch] 联网获取 {month_label} 失败，回退本地缓存")

        # 推送时按配置决定是否过滤 Stadium
        patches_for_display = self._apply_stadium_filter(
            online_patches if online_patches is not None
            else (cached_patches if cached_patches is not None else [])
        )

        if not patches_for_display:
            if online_failed and cached_patches:
                patches_for_display = self._apply_stadium_filter(cached_patches)
            if not patches_for_display:
                yield event.plain_result(f"{month_label} 没有找到补丁记录。")
                event.stop_event()
                return

        # ────────────────────────────────────────────────────────────────
        # 第三步：仅列出日期
        # ────────────────────────────────────────────────────────────────
        if not day:
            dates = get_patch_dates(patches_for_display)
            msg = build_date_list_message(dates, month_label)
            if online_failed:
                msg += "\n\n⚠️ 无法联网获取最新版本，展示的是本地缓存内容。"
            yield event.plain_result(msg)
            event.stop_event()
            return

        # ────────────────────────────────────────────────────────────────
        # 第四步：查找指定日期的补丁
        # ────────────────────────────────────────────────────────────────
        day_int = int(day)
        target_date = f"{year}-{month_num:02d}-{day_int:02d}"

        def find_patch(patches, date_str):
            for p in (patches or []):
                if p["date"] == date_str:
                    return p
            return None

        online_target = find_patch(online_patches, target_date) if online_patches else None
        cached_target = find_patch(cached_patches, target_date) if cached_patches else None
        display_target = find_patch(patches_for_display, target_date)

        if display_target is None:
            yield event.plain_result(
                f"{month_label} 没有找到日期为 {target_date} 的补丁。\n"
                f"可用日期：{', '.join(get_patch_dates(patches_for_display))}"
            )
            event.stop_event()
            return

        # ────────────────────────────────────────────────────────────────
        # 第五步：在线 vs 本地对比差异
        # ────────────────────────────────────────────────────────────────
        diff = {"added": [], "modified": [], "deleted": []}
        has_delta = False
        if online_target and cached_target and not online_failed:
            raw_diff = diff_sections(cached_target.get("sections", []), online_target.get("sections", []))
            # 推送时按配置过滤 Stadium 内容（对比时用全部内容，展示时过滤）
            if not self._get_config(KEY_INCLUDE_STADIUM, DEFAULT_INCLUDE_STADIUM):
                for key in ("added", "modified", "deleted"):
                    diff[key] = [s for s in raw_diff[key] if "stadium" not in s.get("heading", "").lower()]
            else:
                diff = raw_diff
            has_delta = bool(diff.get("added") or diff.get("modified") or diff.get("deleted"))

        # ────────────────────────────────────────────────────────────────
        # 第六步：缓存当前查询供 translate 指令使用
        # ────────────────────────────────────────────────────────────────
        umo_query = event.unified_msg_origin
        self._last_query[umo_query] = {
            "title": display_target["title"],
            "text": display_target.get("text", ""),
            "sections": display_target.get("sections", []),
            "date": target_date,
        }
        logger.info(f"[owpatch] 已缓存 {umo_query} 的查询结果: {target_date}")

        # ────────────────────────────────────────────────────────────────
        # 第七步：推送 — 有差异时先发 Delta
        # ────────────────────────────────────────────────────────────────
        platform = event.get_platform_name() or ""
        sender_id = event.get_sender_id() or ""
        uin = int(sender_id) if sender_id.isdigit() else 0

        if has_delta:
            date_label = target_date[5:]  # "2026-05-12" → "05-12"
            delta_chains = build_delta_message(date_label, diff)
            # 先发送 Delta
            if platform == "aiocqhttp" and uin:
                try:
                    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                    if isinstance(event, AiocqhttpMessageEvent):
                        delta_fwds = build_raw_forward(f"📌 {target_date} 补丁变更", diff.get("added", []) + diff.get("modified", []) + diff.get("deleted", []), uin)
                        gid = event.message_obj.group_id
                        for fwd in delta_fwds:
                            if gid:
                                await event.bot.call_action("send_group_forward_msg", group_id=int(gid), messages=fwd)
                            else:
                                await event.bot.call_action("send_private_forward_msg", user_id=uin, messages=fwd)
                except Exception as e:
                    logger.warning(f"[owpatch] Delta 原始转发失败，回退: {e}")
                    for cl in delta_chains:
                        yield event.chain_result(cl)
            else:
                for cl in delta_chains:
                    yield event.chain_result(cl)

        # 再发送完整补丁
        if platform == "aiocqhttp" and uin:
            try:
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                if isinstance(event, AiocqhttpMessageEvent):
                    raw_fwds = build_raw_forward(display_target["title"], display_target["sections"], uin)
                    gid = event.message_obj.group_id
                    for fwd in raw_fwds:
                        if gid:
                            await event.bot.call_action("send_group_forward_msg", group_id=int(gid), messages=fwd)
                        else:
                            await event.bot.call_action("send_private_forward_msg", user_id=uin, messages=fwd)
                    event.stop_event()
                    return
            except Exception as e:
                logger.warning(f"[owpatch] 完整补丁原始转发失败，回退: {e}")

        chains = build_patch_message(
            title=display_target["title"],
            text=display_target["text"],
            sections=display_target["sections"],
            platform_name=platform,
            bot_self_id=sender_id,
        )
        for chain in chains:
            yield event.chain_result(chain)
        event.stop_event()

    # ==================================================================
    # 指令：翻译补丁
    # ==================================================================

    @owpatch.command("translate", alias={'翻译'})
    async def cmd_translate(self, event: AstrMessageEvent):
        """将上次查询的补丁日志翻译为中文（调用大模型逐章节翻译）。

        用法：
            /owpatch translate    → 翻译上次查询的补丁
        """
        umo = event.unified_msg_origin
        last = self._last_query.get(umo)

        if last is None:
            yield event.plain_result(
                "⚠️ 请先使用 `/owpatch query <月份> <日期>` 查询一份补丁日志后再使用翻译功能。"
            )
            event.stop_event()
            return

        # 获取 LLM provider
        try:
            provider = self.context.get_using_provider(umo=umo)
        except Exception:
            provider = None

        if provider is None:
            yield event.plain_result(
                "❌ 当前会话未配置大语言模型，请在 WebUI 中配置后再使用翻译功能。"
            )
            event.stop_event()
            return

        sections = last.get("sections", [])
        if not sections:
            yield event.plain_result("⚠️ 上次查询的补丁没有可翻译的内容。")
            event.stop_event()
            return

        yield event.plain_result(
            f"🔍 正在处理翻译请求（共 {len(sections)} 个章节）..."
        )

        # ── 计算 sections 哈希，用于缓存校验 ──
        _sections_hash = translator_mod.compute_sections_hash(sections)

        # ── 检查磁盘缓存（日期 + 内容哈希匹配时直接复用）──
        _cache_dir = self.state_mgr.data_dir / "cache" if self.state_mgr.data_dir else None
        if _cache_dir:
            translated_sections = translator_mod.load_translation_cache(
                _cache_dir, last["date"], _sections_hash,
            )
            if translated_sections is not None:
                # 🎯 缓存命中
                yield event.plain_result(
                    f"✅ 使用缓存的翻译结果（{len(sections)} 个章节，无需重新翻译）"
                )
                logger.info(
                    f"[owpatch] 翻译缓存命中: {last.get('date', '')}"
                )
                _cache_hit = True
            else:
                _cache_hit = False
        else:
            _cache_hit = False

        if not _cache_hit:
            yield event.plain_result(
                f"🔍 正在调用大模型翻译（共 {len(sections)} 个章节），请稍候..."
            )

            # 构建 system prompt
            custom_prompt = self._get_config(KEY_TRANSLATE_PROMPT, DEFAULT_TRANSLATE_PROMPT)
            system_prompt = translator_mod.build_system_prompt(
                custom_prompt=custom_prompt
            )

            progress_messages = []

            def _record_progress(current: int, total: int):
                if current < total:
                    progress_messages.append(current)

            translated_sections = await translator_mod.translate_sections(
                provider=provider,
                sections=sections,
                system_prompt=system_prompt,
                progress_callback=_record_progress,
            )

            for cur in progress_messages:
                yield event.plain_result(f"🔄 翻译中 {cur}/{len(sections)}...")

            # ── 保存翻译缓存（下次相同内容无需重新翻译）──
            if _cache_dir:
                translator_mod.save_translation_cache(
                    _cache_dir, last["date"], _sections_hash, translated_sections,
                )

            logger.info(
                f"[owpatch] 翻译完成: {last.get('date', '')} "
                f"({len(sections)} 章节)"
            )

        # 构建翻译后的标题和数据
        translated_title = f"{last['title']} [中文]"
        translated_patch = last

        # 复用现有发送逻辑
        platform = event.get_platform_name() or ""
        sender_id = event.get_sender_id() or ""
        uin = int(sender_id) if sender_id.isdigit() else 0

        if platform == "aiocqhttp" and uin:
            try:
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                if isinstance(event, AiocqhttpMessageEvent):
                    raw_fwds = build_raw_forward(translated_title, translated_sections, uin)
                    gid = event.message_obj.group_id
                    for fwd in raw_fwds:
                        if gid:
                            await event.bot.call_action("send_group_forward_msg", group_id=int(gid), messages=fwd)
                        else:
                            await event.bot.call_action("send_private_forward_msg", user_id=uin, messages=fwd)
                    event.stop_event()
                    return
            except Exception as e:
                logger.warning(f"[owpatch] 翻译转发失败，回退: {e}")

        chains = build_patch_message(
            title=translated_title,
            text=translated_patch["text"],
            sections=translated_sections,
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
        """核心检查：获取 → 解析 → 缓存 → 比较（整版 + 节级） → 先 Delta 后 Full 推送。"""
        urls = self._get_target_urls()
        import re

        # ── 获取 & 缓存原始数据（不做 Stadium 过滤）──
        raw_patches_pool = []
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
            patches = parse_patches(html)
            if not patches:
                continue
            # 提取年月并缓存原始数据
            ym = re.search(r'/live/(\d{4})/(\d{2})/', url)
            if ym:
                self._put_cache(int(ym.group(1)), int(ym.group(2)), patches)
            raw_patches_pool.extend(patches)

        if not raw_patches_pool:
            logger.info("[owpatch] 所有页面均无补丁数据")
            return False

        # ── 比较逻辑使用原始（未过滤）章节 ──
        latest = get_latest_patch(raw_patches_pool)
        if latest is None:
            return False

        latest_date = latest["date"]
        latest_hash = compute_content_hash(latest["raw_html"])
        raw_sections = latest.get("sections", [])
        current_hashes = compute_section_hashes(raw_sections)

        # 情况 1：全新补丁 → 直接推送完整内容
        if self.state_mgr.is_new_patch(latest_date, latest_hash):
            logger.info(f"[owpatch] 发现新补丁！日期: {latest_date}")
            self.state_mgr.mark_pushed(latest_date, latest_hash, current_hashes)
            display_patch = self._apply_stadium_filter([latest])[0]
            return await self._push_full(display_patch)

        # 情况 2：节级增量检测（含新增 / 修改 / 删除）
        changed, deleted = self.state_mgr.find_all_deltas(current_hashes)

        include_stadium = self._get_config(KEY_INCLUDE_STADIUM, DEFAULT_INCLUDE_STADIUM)
        if not include_stadium:
            changed = [h for h in changed if "stadium" not in h.lower()]
            deleted = [h for h in deleted if "stadium" not in h.lower()]

        if changed or deleted:
            # 构建显示用的补丁（已过滤 Stadium）
            display_patch = self._apply_stadium_filter([latest])[0]
            display_sections = display_patch.get("sections", [])
            display_map = {s.get("heading", ""): s for s in display_sections}

            recorded = self.state_mgr.get_section_hashes()
            diff = {"added": [], "modified": [], "deleted": []}

            for h in changed:
                if h in display_map:
                    sec = display_map[h]
                    if h in recorded:
                        diff["modified"].append(sec)
                    else:
                        diff["added"].append(sec)

            for h in deleted:
                diff["deleted"].append({"heading": h, "content": "", "sub_sections": []})

            self.state_mgr.mark_pushed(latest_date, latest_hash, current_hashes)

            # 先推送 Delta，再推送完整补丁
            return await self._push_delta_then_full(display_patch, diff)

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

    async def _push_delta_then_full(self, latest: dict, diff: dict) -> bool:
        """先推送 Delta 变更摘要，再推送完整补丁。"""
        umos = self.state_mgr.get_umos()
        if not umos:
            return True

        date_label = latest["date"][5:]  # "2026-05-12" → "05-12"
        delta_chains = build_delta_message(date_label, diff)
        full_sent = False

        # 发送 Delta
        delta_ok = await self._send_to_umos(umos, delta_chains, "Delta推送")
        if delta_ok:
            # 仅 delta 成功后发送完整补丁
            full_sent = await self._push_full(latest)
        else:
            # delta 失败仍尝试推送完整补丁
            full_sent = await self._push_full(latest)

        return delta_ok or full_sent

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
        """查询上个月的最新补丁日期（同时缓存数据）。"""
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

        patches = parse_patches(html)
        if patches:
            self._put_cache(y, m, patches)  # 缓存原始数据
        display = self._apply_stadium_filter(patches)
        latest = get_latest_patch(display)
        if latest and latest["date"] != "unknown":
            return latest["date"]
        return None

    # ==================================================================
    # 基线建立
    # ==================================================================

    async def _init_baseline(self):
        """首次安装时静默抓取当前最新补丁，记录为基线并缓存（不推送）。"""
        urls = self._get_target_urls()
        import re
        raw_pool = []

        for url in urls:
            html = await fetch_page(
                url,
                timeout=self._get_config(KEY_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT),
                user_agent=self._get_config(KEY_USER_AGENT, DEFAULT_USER_AGENT),
                proxy=self._get_proxy(),
                force_refresh=True,
            )
            if html:
                patches = parse_patches(html)
                if patches:
                    # 缓存原始数据
                    ym = re.search(r'/live/(\d{4})/(\d{2})/', url)
                    if ym:
                        self._put_cache(int(ym.group(1)), int(ym.group(2)), patches)
                    raw_pool.extend(patches)

        if not raw_pool:
            logger.warning("[owpatch] 基线建立失败：所有页面均无数据")
            return

        # 使用原始（未过滤）章节计算哈希
        latest = get_latest_patch(raw_pool)
        if latest:
            latest_hash = compute_content_hash(latest["raw_html"])
            section_hashes = compute_section_hashes(latest.get("sections", []))
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
        """写入永久缓存（所有月份统一处理）。"""
        if self.patch_cache:
            self.patch_cache.put(year, month, patches)

    @staticmethod
    def _now_beijing_str() -> str:
        """返回当前北京时间的日期字符串（YYYY-MM-DD）。"""
        return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

    # _parse_query_month 已移除，参数解析逻辑已内联到 cmd_query 中。

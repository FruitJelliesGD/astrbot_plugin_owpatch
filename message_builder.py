"""
消息构建器 — 按 H4 章节构建合并转发消息，长章节按字数切分
"""

import astrbot.api.message_components as Comp

MAX_CHUNK_SIZE = 3500
MAX_SECTIONS_PER_FORWARD = 8  # 嵌套时每外层最多 8 章


def build_patch_message(
    title: str, text: str, sections: list[dict],
    platform_name: str = "unknown", bot_self_id: str = "",
) -> list:
    """合并转发（AstrBot 组件，非嵌套平铺）。
    aiocqhttp 嵌套转发由 forward_builder 负责。
    """
    uin = int(bot_self_id) if bot_self_id.isdigit() else 0
    valid = [s for s in sections if s.get("content", "").strip()]
    batch_size = MAX_SECTIONS_PER_FORWARD
    results = []
    total = max((len(valid) + batch_size - 1) // batch_size, 1)

    for batch_idx in range(0, len(valid), batch_size):
        batch = valid[batch_idx:batch_idx + batch_size]
        nodes = []

        n = len(results) + 1
        tag = f" ({n}/{total})" if total > 1 else ""
        nodes.append(_make_node(uin, f"📢 {title}{tag}"))

        for sec in batch:
            heading = sec.get("heading", "")
            content = sec.get("content", "")
            sub = [h5 for h5 in sec.get("sub_sections", []) if h5.get("content", "").strip()]
            need_expand = len(sub) >= 2 or len(content) > 500

            if need_expand:
                nodes.append(_make_node(uin, f"━━━ {heading} ━━━"))
                for h5 in sub:
                    for chunk in _split_text(f"▸ {h5['heading']}\n{h5['content']}", MAX_CHUNK_SIZE):
                        nodes.append(_make_node(uin, chunk))
                if not sub:
                    for chunk in _split_text(content, MAX_CHUNK_SIZE):
                        nodes.append(_make_node(uin, chunk))
            else:
                for chunk in _split_text(f"━━━ {heading} ━━━\n{content}", MAX_CHUNK_SIZE):
                    nodes.append(_make_node(uin, chunk))

        results.append([Comp.Nodes(nodes=nodes)])

    return results or [[Comp.Plain(f"📢 {title}")]]


def build_date_list_message(dates: list[str], month_label: str) -> str:
    if not dates:
        return f"{month_label} 没有找到补丁记录。"
    lines = [f"{month_label} 共有 {len(dates)} 个补丁："]
    for i, d in enumerate(dates, 1):
        lines.append(f"  {i}. {d}")
    lines.append("")

    # 从 month_label（如 "2025年4月"）提取年月用于示例提示
    import re
    ym = re.search(r'(\d{4})年(\d{1,2})月', month_label)
    if ym:
        y, m = ym.group(1), ym.group(2)
        # 日期取第一个补丁的日数作为示例
        example_day = dates[0].split("-")[-1] if dates else "28"
        lines.append(f"发送 `/owpatch query {y} {m} <日期>` 查看指定补丁。")
        lines.append(f'例如：`/owpatch query {y} {m} {example_day}` 查看 {m}月{example_day}日补丁。')
    else:
        lines.append("发送 `/owpatch query <年份> <月份> <日期>` 查看指定补丁。")

    return "\n".join(lines)


def build_no_update_message() -> str:
    return "✅ 当前没有新补丁，已经是最新。"


def build_bind_success_message(umo_count: int) -> str:
    return f"✅ 绑定成功！当前已绑定 {umo_count} 个会话。"


def build_unbind_success_message(umo_count: int) -> str:
    return f"✅ 已解绑。当前剩余 {umo_count} 个绑定会话。"


def build_status_message(
    umo_count: int,
    last_patch_date: str,
    today_pushed: bool,
    window_start: str,
    window_end: str,
) -> str:
    return (
        f"📊 守望先锋补丁监控 — 状态\n"
        f"  绑定会话数：{umo_count}\n"
        f"  最新已推送补丁：{last_patch_date or '暂无'}\n"
        f"  今日已推送：{'是' if today_pushed else '否'}\n"
        f"  定时窗口：{window_start} ~ {window_end}（北京时间）"
    )


def build_help_message() -> str:
    return (
        "📋 守望先锋补丁监控 — 指令列表\n"
        "/owpatch bind — 绑定当前会话接收补丁推送\n"
        "/owpatch unbind — 解绑当前会话\n"
        "/owpatch status — 查看当前状态\n"
        "/owpatch check — 立即检查新补丁\n"
        "/owpatch query <年份> — 查询指定年份补丁概况（如 /owpatch query 2025）\n"
        "/owpatch query <年份> <月份> — 查询指定年月补丁日期列表\n"
        "/owpatch query <年份> <月份> <日期> — 查看指定日期的补丁内容\n"
        "/owpatch query <月份> — 查询当年指定月补丁日期列表\n"
        "/owpatch query <月份> <日期> — 查看当年指定日期补丁内容\n"
        "/owpatch cache — 批量预热历史补丁到本地\n"
        "/owpatch cache status — 查看缓存统计\n"
        "/owpatch translate — 将上次查询的补丁日志翻译为中文\n"
        "/owpatch help — 显示本帮助"
    )


def build_delta_message(date_label: str, diff_result: dict) -> list:
    """增量消息：基于分类 diff 结果构建，含 H5 子章节结构。

    Args:
        date_label: 补丁日期标签（如 "05-12"）
        diff_result: diff_sections() 返回值，含 added / modified / deleted 三个列表
    """
    parts = [f"📌 官方对 {date_label} 补丁进行了以下修改：", "=" * 40, ""]

    # ── 新增 ──
    added = diff_result.get("added", [])
    if added:
        parts.append("🆕 新增章节：")
        parts.append("─" * 20)
        for sec in added:
            parts.append(f"━━━ {sec['heading']} ━━━")
            _append_sec_content(parts, sec)
        parts.append("")

    # ── 修改 ──
    modified = diff_result.get("modified", [])
    if modified:
        parts.append("✏️ 内容变更章节：")
        parts.append("─" * 20)
        for sec in modified:
            parts.append(f"━━━ {sec['heading']} ━━━")
            _append_sec_content(parts, sec)
        parts.append("")

    # ── 删除 ──
    deleted = diff_result.get("deleted", [])
    if deleted:
        parts.append("🗑️ 已删除章节：")
        parts.append("─" * 20)
        for sec in deleted:
            parts.append(f"  • {sec['heading']}")
        parts.append("")

    if not added and not modified and not deleted:
        parts.append("  无变更。")

    full = "\n".join(parts)
    return [[Comp.Plain(c)] for c in _split_text(full, MAX_CHUNK_SIZE) if c.strip()]


def _append_sec_content(parts: list, sec: dict):
    """将章节内容（含 H5 子节）追加到 parts 列表。"""
    for h5 in sec.get("sub_sections", []):
        if h5.get("content", "").strip():
            parts.append(f"▸ {h5['heading']}")
            parts.append(h5["content"].strip())
    if sec.get("content", "").strip() and not sec.get("sub_sections"):
        parts.append(sec["content"].strip())


# ====================================================================
# 内部工具
# ====================================================================

def _make_node(uin: int, text: str) -> Comp.Node:
    return Comp.Node(uin=uin, name="守望先锋补丁", content=[Comp.Plain(text)])


def _split_text(text: str, max_size: int) -> list[str]:
    """按最大字数切分文本，尽量在换行处断开。"""
    if len(text) <= max_size:
        return [text]
    chunks = []
    while len(text) > max_size:
        split_at = text.rfind("\n", 0, max_size)
        if split_at == -1:
            split_at = max_size
        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()
    if text:
        chunks.append(text)
    return chunks




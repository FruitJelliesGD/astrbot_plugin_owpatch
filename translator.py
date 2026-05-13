"""
翻译模块 — 读取 SKILL.md 与术语对照表，构建 system prompt，逐章节调用 LLM 翻译
"""

import json
import re
from pathlib import Path
from astrbot.api import logger

# ------------------------------------------------------------------
# 降级用内置术语表（skill 文件缺失时的兜底）
# ------------------------------------------------------------------
_FALLBACK_TERMS = {
    "heroes": {
        "Tracer": "猎空", "Reaper": "死神", "Widowmaker": "黑百合",
        "Pharah": "法老之鹰", "Mercy": "天使", "Genji": "源氏",
        "Soldier: 76": "士兵：76", "Lúcio": "卢西奥", "Zenyatta": "禅雅塔",
        "D.Va": "D.Va", "Reinhardt": "莱因哈特", "Ana": "安娜",
        "Bastion": "堡垒", "Hanzo": "半藏", "Cassidy": "卡西迪",
    },
    "abilities": {
        "Pulse Bomb": "脉冲炸弹", "Death Blossom": "死亡绽放",
        "Infra-Sight": "红外侦测", "Chain Hook": "链钩",
        "Rocket Punch": "火箭重拳", "Whole Hog": "鸡飞狗跳",
    },
    "maps": {
        "King's Row": "国王大道", "Hanamura": "花村",
        "Route 66": "66号公路", "Lijiang Tower": "漓江塔",
    },
    "game_terms": {
        "Overwatch": "守望先锋", "Patch Notes": "补丁说明",
        "Balance Changes": "平衡性调整", "Bug Fixes": "错误修复",
        "Perk": "威能", "Major Perk": "主要威能", "Minor Perk": "次级威能",
        "Ultimate": "终极技能", "Cooldown": "冷却时间",
        "Quick Play": "快速比赛", "Competitive Play": "竞技比赛",
    },
}

SKILL_DIR = Path(__file__).parent / "skills" / "overwatch-patch-translation"
SKILL_MD_PATH = SKILL_DIR / "SKILL.md"
TERMS_JSON_PATH = SKILL_DIR / "scripts" / "overwatch_terms.json"


# ------------------------------------------------------------------
# 资源加载
# ------------------------------------------------------------------

def _read_skill_md() -> str:
    """读取 SKILL.md 内容，文件不存在时返回空字符串。"""
    try:
        if SKILL_MD_PATH.exists():
            return SKILL_MD_PATH.read_text(encoding="utf-8")
        logger.warning("[translator] SKILL.md 不存在，跳过 skill prompt")
    except Exception as e:
        logger.warning(f"[translator] 读取 SKILL.md 失败: {e}")
    return ""


def _read_terms_json() -> dict:
    """读取术语对照表，文件缺失时返回降级内置表。"""
    try:
        if TERMS_JSON_PATH.exists():
            data = json.loads(TERMS_JSON_PATH.read_text(encoding="utf-8"))
            terms = data.get("terms", {})
            logger.info(
                f"[translator] 已加载术语表: "
                f"{len(terms.get('heroes', {}))} 英雄, "
                f"{len(terms.get('abilities', {}))} 技能, "
                f"{len(terms.get('maps', {}))} 地图, "
                f"{len(terms.get('game_terms', {}))} 游戏术语"
            )
            return terms
        logger.warning("[translator] 术语表文件不存在，使用降级内置表")
    except Exception as e:
        logger.warning(f"[translator] 读取术语表失败，使用降级内置表: {e}")
    return dict(_FALLBACK_TERMS)


def _format_terms_for_prompt(terms: dict) -> str:
    """将术语对照表格式化为 system prompt 可用的文本块。"""
    lines = []
    categories = {
        "heroes": "英雄名称",
        "abilities": "技能名称",
        "maps": "地图名称",
        "game_terms": "游戏术语",
    }
    for key, label in categories.items():
        items = terms.get(key, {})
        if items:
            lines.append(f"\n### {label}")
            for eng, chn in sorted(items.items()):
                lines.append(f"- {eng} → {chn}")
    return "\n".join(lines) if lines else "（术语表为空）"


# ------------------------------------------------------------------
# System Prompt 构建
# ------------------------------------------------------------------

def build_system_prompt(custom_prompt: str | None = None) -> str:
    """构建完整的翻译 system prompt。

    1. 读取 SKILL.md 中的核心翻译规则（去掉 frontmatter 和工作流步骤）
    2. 追加格式化的术语对照表
    3. 若 custom_prompt 非空则追加到末尾

    Returns:
        完整的 system prompt 字符串
    """
    # ── 读取 SKILL.md ──
    raw_md = _read_skill_md()

    # 去掉 frontmatter（--- ... ---）
    skill_body = re.sub(r"^---.*?---\s*", "", raw_md, flags=re.DOTALL).strip()

    # ── 读取术语表 ──
    terms = _read_terms_json()
    term_block = _format_terms_for_prompt(terms)

    # ── 组装 ──
    parts = []

    if skill_body:
        parts.append(skill_body)
    else:
        # 无 SKILL.md 时的内置基础指令
        parts.append(
            "你是一个守望先锋补丁翻译专家。请将以下英文补丁章节翻译为中文。\n\n"
            "规则：\n"
            "1. 保留所有 Markdown 格式和层级结构\n"
            "2. 专有名词使用术语对照表中的翻译，未收录的保留英文并标记 [待补充]\n"
            "3. 数字、百分比、数值保持原样\n"
            "4. 仅返回翻译后的文本，不附加任何解释或报告\n"
            "5. 翻译后的层级结构与原文保持一致"
        )

    parts.append("\n\n## 术语对照表（请严格遵循）")
    parts.append(term_block)

    parts.append(
        "\n\n## 输出要求\n"
        "- 仅返回翻译后的文本，保留原 Markdown 层级结构\n"
        "- 不附加解释、不输出核对报告、不输出翻译说明\n"
        "- 未收录专有名词保留英文原文并标记 [待补充]\n"
        "- 保持 heading、列表、引用块等格式标记不变"
    )

    if custom_prompt and custom_prompt.strip():
        parts.append(f"\n\n## 附加要求\n{custom_prompt.strip()}")

    return "\n".join(parts)


# ------------------------------------------------------------------
# 逐章节翻译
# ------------------------------------------------------------------

async def translate_sections(
    provider,
    sections: list[dict],
    system_prompt: str,
    progress_callback=None,
) -> list[dict]:
    """逐章节调用 LLM 翻译，返回与原结构一致的新 sections 列表。

    Args:
        provider: AstrBot LLM provider 实例（含 text_chat 方法）
        sections: 原始 sections 列表
        system_prompt: 共享的 system prompt
        progress_callback: 可选的回调函数 fn(idx, total)

    Returns:
        翻译后的 sections 列表（失败章节保留英文原文）
    """
    translated = []
    total = len(sections)

    for idx, sec in enumerate(sections):
        heading = sec.get("heading", "")
        content = sec.get("content", "")
        sub_sections = sec.get("sub_sections", [])

        # 构建要翻译的章节文本
        section_text = f"## {heading}\n\n{content}" if heading else content
        if sub_sections:
            for h5 in sub_sections:
                h5_heading = h5.get("heading", "")
                h5_content = h5.get("content", "")
                if h5_heading:
                    section_text += f"\n### {h5_heading}\n{h5_content}"
                elif h5_content:
                    section_text += f"\n{h5_content}"

        # 跳过空章节
        if not section_text.strip():
            translated.append(dict(sec))
            if progress_callback:
                progress_callback(idx + 1, total)
            continue

        # 调用 LLM
        translated_ok = False
        try:
            llm_resp = await provider.text_chat(
                prompt=section_text,
                system_prompt=system_prompt,
            )
            translated_text = _extract_text(llm_resp)
            if translated_text and translated_text.strip():
                translated.append({
                    "heading": heading,
                    "content": _parse_translated_section(translated_text),
                    "sub_sections": _parse_translated_sub_sections(
                        translated_text, sub_sections
                    ),
                })
                translated_ok = True
        except Exception as e:
            logger.warning(f"[translator] 章节 {idx+1}/{total} 翻译失败: {e}")

        if not translated_ok:
            # 失败时保留原文
            translated.append(dict(sec))
            logger.info(f"[translator] 章节 {idx+1}/{total} 保留原文（{heading}）")

        if progress_callback:
            progress_callback(idx + 1, total)

    return translated


def _extract_text(llm_resp) -> str:
    """从 LLMResponse 或纯字符串中提取文本。"""
    if hasattr(llm_resp, "completion_text"):
        return llm_resp.completion_text or ""
    if hasattr(llm_resp, "result_chain"):
        # 尝试从 result_chain 提取 Plain 文本
        for comp in getattr(llm_resp.result_chain, "chain", []):
            if hasattr(comp, "text"):
                return comp.text
    if isinstance(llm_resp, str):
        return llm_resp
    return str(llm_resp) if llm_resp else ""


def _parse_translated_section(translated_text: str) -> str:
    """从翻译结果中提取正文内容（去掉一级/二级标题行）。"""
    lines = translated_text.strip().split("\n")
    # 去掉开头的 # 或 ## 标题行（heading 由 section 结构维护）
    body_lines = [l for l in lines if not l.strip().startswith("##")]
    return "\n".join(body_lines).strip()


def _parse_translated_sub_sections(
    translated_text: str, original_subs: list[dict]
) -> list[dict]:
    """从翻译结果中提取 H5（###）子章节。"""
    result = []
    lines = translated_text.strip().split("\n")
    current_h5 = None
    current_content = []

    for line in lines:
        if line.strip().startswith("### "):
            if current_h5 is not None:
                result.append({
                    "heading": current_h5,
                    "content": "\n".join(current_content).strip(),
                })
            current_h5 = line.strip()[4:].strip()
            current_content = []
        elif current_h5 is not None:
            current_content.append(line)

    if current_h5 is not None:
        result.append({
            "heading": current_h5,
            "content": "\n".join(current_content).strip(),
        })

    # 如果没有找到任何 ### 子节，返回原始 sub_sections 结构但更新 content 为完整正文
    if not result and original_subs:
        body = _parse_translated_section(translated_text)
        if body:
            return [{"heading": s.get("heading", ""), "content": body}
                    for s in original_subs]

    return result if result else original_subs

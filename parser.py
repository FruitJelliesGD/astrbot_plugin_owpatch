"""
HTML 解析器 — 按 H4 标题分组提取守望先锋补丁章节
"""

import hashlib
import re
from bs4 import BeautifulSoup, Tag, NavigableString
from astrbot.api import logger


def parse_patches(html: str) -> list[dict]:
    """从月度页面 HTML 解析所有补丁条目。"""
    if not html:
        return []

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:
        logger.error(f"[parser] 解析失败: {e}")
        return []

    patch_headings = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        if "Overwatch Retail Patch Notes" in tag.get_text(strip=True):
            patch_headings.append(tag)
    if not patch_headings:
        for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
            if "Patch Notes" in tag.get_text(strip=True):
                patch_headings.append(tag)
    if not patch_headings:
        logger.warning("[parser] 未找到补丁标题")
        return []

    patches = []
    for heading in patch_headings:
        title = heading.get_text(strip=True)
        date_str = _extract_date(title) or _extract_date_from_context(heading)
        raw_html = _extract_patch_content(heading)
        text = _html_to_clean_text(raw_html)
        sections = _extract_h4_sections(raw_html)

        patches.append({
            "date": date_str or "unknown",
            "title": title,
            "raw_html": raw_html,
            "text": text,
            "sections": sections,
        })

    seen = set()
    unique = [p for p in patches if p["title"] not in seen and not seen.add(p["title"])]
    unique.sort(key=lambda x: x["date"] if x["date"] != "unknown" else "0000-00-00", reverse=True)
    logger.info(f"[parser] 解析到 {len(unique)} 个补丁条目, 共 {sum(len(p['sections']) for p in unique)} 个 H4 章节")
    return unique


def get_latest_patch(patches: list[dict]) -> dict | None:
    return patches[0] if patches else None


def get_patch_dates(patches: list[dict]) -> list[str]:
    return [p["date"] for p in patches if p["date"] != "unknown"]


def filter_stadium(patches: list[dict]) -> list[dict]:
    """移除 Stadium 角斗领域内容（基于 H4 标题重复检测）。

    规则：
    1. 收集 "Stadium Updates" 之前所有 H4 标题 → pre_set
    2. 跳过 "Stadium Updates"
    3. 其后 H4 标题若在 pre_set 中 → 角斗领域重复，跳过
    4. 若不在 pre_set 中 → 退出重复检测，正常保留
    5. Bug Fixes 中切除 Stadium 子段
    """
    _ALWAYS_STADIUM = {"general item changes"}

    for patch in patches:
        sections = patch.get("sections", [])
        if not sections:
            continue

        # --- 第一遍：收集 Stadium 前标题 ---
        pre: set[str] = set()
        sidx = -1
        for i, sec in enumerate(sections):
            hl = sec.get("heading", "").lower()
            if "stadium" in hl and "update" in hl:
                sidx = i
                break
            pre.add(hl)

        if sidx < 0:
            continue

        # --- 第二遍：过滤 ---
        filtered = []
        in_zone = False

        for i, sec in enumerate(sections):
            h = sec.get("heading", "")
            c = sec.get("content", "")
            hl = h.lower()

            if i == sidx:
                in_zone = True
                continue

            if in_zone:
                if hl in pre or hl in _ALWAYS_STADIUM:
                    continue
                in_zone = False

            if "bug fix" in hl and "stadium" in c.lower():
                idx = _find_stadium_index(c)
                if idx >= 0:
                    c = c[:idx].strip()
                if not c.strip():
                    continue

            filtered.append({
                "level": sec.get("level", 4),
                "heading": h,
                "content": c,
                "sub_sections": sec.get("sub_sections", []),
            })

        patch["sections"] = filtered
        # 更新 text
        text_parts = []
        for s in filtered:
            if s["content"].strip():
                text_parts.append(f"━━━ {s['heading']} ━━━\n{s['content']}")
                for h5 in s.get("sub_sections", []):
                    if h5.get("content", "").strip():
                        text_parts.append(f"▸ {h5['heading']}\n{h5['content']}")
        patch["text"] = "\n\n".join(text_parts)

    return patches


def _find_stadium_index(text: str) -> int:
    """查找 Bug Fixes 内容中 Stadium 子节的起始位置。"""
    import re
    m = re.search(r'\n\s*Stadium\s*\n', text, re.IGNORECASE)
    if m:
        return m.start()
    # 尝试匹配行首的 "Stadium"
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.strip().lower() == "stadium":
            return text.find(line)
    return -1


def compute_content_hash(raw_html: str) -> str:
    return hashlib.sha256(raw_html.encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    """归一化文本：统一 Unicode 形式、换行符、压缩空行，用于稳定比较。"""
    import unicodedata
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def compute_text_hash(text: str) -> str:
    """对归一化后的文本计算 SHA256 哈希。"""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def compute_section_hashes(sections: list[dict]) -> dict:
    """为每个 H4 章节内容计算哈希 {heading: sha256}。"""
    result = {}
    for sec in sections:
        h = sec.get("heading", "")
        c = sec.get("content", "")
        if h and c.strip():
            result[h] = compute_content_hash(c)
    return result


def get_delta_sections(sections: list[dict], headings: list[str]) -> list[dict]:
    """从章节列表中筛选指定 heading 的增量章节。"""
    hs = set(headings)
    return [s for s in sections if s.get("heading") in hs]


def diff_sections(old_sections: list[dict], new_sections: list[dict]) -> dict:
    """对比两组章节，返回增／改／删分类结果。

    Args:
        old_sections: 旧版本的章节列表（各元素含 heading / content）
        new_sections: 新版本的章节列表

    Returns:
        {
            "added":      [section, ...],   # 新版本新增的章节
            "modified":   [section, ...],   # 内容发生变化的章节（返回新版本）
            "deleted":    [section, ...],   # 旧版本有但新版本已删除的章节
        }
    """
    old_map = {}
    for s in old_sections:
        h = s.get("heading", "")
        if h:
            old_map[h] = s

    new_map = {}
    for s in new_sections:
        h = s.get("heading", "")
        if h:
            new_map[h] = s

    old_hashes = {h: compute_content_hash(s.get("content", "")) for h, s in old_map.items()}
    new_hashes = {h: compute_content_hash(s.get("content", "")) for h, s in new_map.items()}

    old_keys = set(old_map.keys())
    new_keys = set(new_map.keys())

    added_keys = new_keys - old_keys
    deleted_keys = old_keys - new_keys
    common_keys = old_keys & new_keys

    modified_keys = {h for h in common_keys if old_hashes.get(h) != new_hashes.get(h)}

    return {
        "added":    [new_map[h] for h in sorted(added_keys)],
        "modified": [new_map[h] for h in sorted(modified_keys)],
        "deleted":  [old_map[h] for h in sorted(deleted_keys)],
    }


# ====================================================================
# H4 分组提取
# ====================================================================

def _extract_h4_sections(html: str) -> list[dict]:
    """用正则切 HTML 片段，每个 H4 独立搜索 PatchNotesHeroUpdate。"""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["img", "picture", "figure", "figcaption"]):
        tag.decompose()

    h4_positions = [(m.start(), m.end()) for m in re.finditer(r'<h4\b', html)]
    if not h4_positions:
        text = _html_to_clean_text(html)
        return [{"level": 4, "heading": "补丁内容", "content": text, "sub_sections": []}] if text.strip() else []

    sections = []
    for i, (start, _) in enumerate(h4_positions):
        end = h4_positions[i + 1][0] if i + 1 < len(h4_positions) else len(html)
        frag = html[start:end]
        fsoup = BeautifulSoup(frag, "lxml")
        for t in fsoup.find_all(["img", "picture", "figure", "figcaption"]):
            t.decompose()

        h4_tag = fsoup.find("h4")
        if not h4_tag:
            continue
        heading = h4_tag.get_text(strip=True)
        if not heading:
            continue

        hero_subs = []
        for div in fsoup.find_all("div", class_="PatchNotesHeroUpdate"):
            text = div.get_text(separator="\n", strip=True)
            lines = [l.strip() for l in text.split("\n")
                     if l.strip() and not l.startswith("http")]
            if not lines:
                continue
            hero_name = lines[0]
            content = "\n".join(lines[1:]) if len(lines) > 1 else ""
            content = re.sub(r'!\[.*?\]\(.*?\)', '', content)
            content = re.sub(r'\n{3,}', '\n\n', content)
            if content.strip():
                hero_subs.append({
                    "level": 5, "heading": hero_name,
                    "content": content.strip(),
                })

        full = fsoup.get_text(separator="\n", strip=True)
        full = re.sub(r'!\[.*?\]\(.*?\)', '', full)
        full = re.sub(r'^\s*https?://\S+\.(?:png|jpg|jpeg|gif|webp|svg)\s*$', '', full, flags=re.MULTILINE)
        full = re.sub(r'\n{3,}', '\n\n', full)
        lines = full.split("\n")
        if lines and heading in lines[0]:
            lines = lines[1:]
        content = "\n".join(lines).strip()

        if content:
            sections.append({
                "level": 4, "heading": heading,
                "content": content, "sub_sections": hero_subs,
            })

    logger.info(
        f"[parser] H4节数={len(sections)} "
        f"含英雄子节={sum(1 for s in sections if s.get('sub_sections'))}"
    )
    return sections


# ====================================================================
# 日期
# ====================================================================

_MONTHS = {m: i for i, m in enumerate(
    ("January","February","March","April","May","June",
     "July","August","September","October","November","December"), 1)}
_MONTH_RE = (r'(January|February|March|April|May|June|July'
             r'|August|September|October|November|December)\s+(\d{1,2}),\s*(\d{4})')

def _extract_date(title: str) -> str | None:
    m = re.search(_MONTH_RE, title)
    return _fmt_date(m.group(1), int(m.group(2)), int(m.group(3))) if m else None

def _extract_date_from_context(tag) -> str | None:
    prev = tag.find_previous(string=re.compile(r"Top of post"))
    if prev and (m := re.search(_MONTH_RE, prev)):
        return _fmt_date(m.group(1), int(m.group(2)), int(m.group(3)))
    return None

def _fmt_date(name: str, day: int, year: int) -> str | None:
    n = _MONTHS.get(name)
    return f"{year}-{n:02d}-{day:02d}" if n else None


# ====================================================================
# HTML 工具
# ====================================================================

def _extract_patch_content(heading_tag) -> str:
    parts = [str(heading_tag)]
    for sib in heading_tag.find_next_siblings():
        st = sib.get_text(strip=True) if hasattr(sib, 'get_text') else ""
        if "Overwatch Retail Patch Notes" in st:
            break
        if hasattr(sib, 'name') and sib.name == "div" and "Top of post" in st:
            parts.append(str(sib))
            break
        parts.append(str(sib))
    return "\n".join(parts)

def _html_to_clean_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["img", "picture", "figure", "figcaption"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    text = re.sub(r'^\s*https?://\S+\.(?:png|jpg|jpeg|gif|webp|svg)\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

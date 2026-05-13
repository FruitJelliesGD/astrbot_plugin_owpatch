"""
OneBot 原始嵌套合并转发构建器
直接构造 dict 结构，通过 bot.call_action 发送
"""

MAX_CHUNK = 3500
MAX_PER_FWD = 8


def build_raw_forward(title: str, sections: list[dict], uin: int) -> list[list]:
    """返回 [outer_fwd_1, outer_fwd_2, ...]，每个可传给 call_action 的 messages 参数。"""
    valid = [s for s in sections if s.get("content", "").strip()]
    batch_size = MAX_PER_FWD
    results = []
    total = max((len(valid) + batch_size - 1) // batch_size, 1)

    for batch_idx in range(0, len(valid), batch_size):
        batch = valid[batch_idx:batch_idx + batch_size]
        outer = []

        n = len(results) + 1
        tag = f" ({n}/{total})" if total > 1 else ""
        outer.append(_node(uin, f"📢 {title}{tag}"))

        for sec in batch:
            h = sec.get("heading", "")
            c = sec.get("content", "")
            sub = [x for x in sec.get("sub_sections", []) if x.get("content", "").strip()]

            if len(sub) >= 2 or len(c) > 500:
                inner = [_node(uin, f"━━━ {h} ━━━")]
                for h5 in sub:
                    for chunk in _split(f"▸ {h5['heading']}\n{h5['content']}", MAX_CHUNK):
                        inner.append(_node(uin, chunk))
                if not sub:
                    for chunk in _split(c, MAX_CHUNK):
                        inner.append(_node(uin, chunk))
                outer.append({
                    "type": "node",
                    "data": {"user_id": uin, "nickname": "守望先锋补丁", "content": inner},
                })
            else:
                for chunk in _split(f"━━━ {h} ━━━\n{c}", MAX_CHUNK):
                    outer.append(_node(uin, chunk))

        results.append(outer)
    return results


def _node(uin: int, text: str) -> dict:
    return {"type": "node", "data": {"user_id": uin, "nickname": "守望先锋补丁", "content": text}}


def _split(text: str, n: int) -> list[str]:
    if len(text) <= n:
        return [text]
    r = []
    while len(text) > n:
        i = text.rfind("\n", 0, n)
        if i == -1:
            i = n
        r.append(text[:i].strip())
        text = text[i:].strip()
    if text:
        r.append(text)
    return r

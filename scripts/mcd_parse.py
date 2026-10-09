#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦当劳 MCP 响应剥离器。

背景（实测结论，非推测）
------------------------
麦当劳 MCP Server 是远程托管的 Streamable HTTP 服务，其 ``tools/call`` 返回的
``content[].text`` **不是纯 JSON**，而是至少三种形态混用：

1. ``envelope``  —— 前面一大段字段说明，最后接
   ``## Original Response`` + 真正的一行 JSON。大多数工具属于此类。
   例：``order-list`` / ``query-my-account`` / ``now-time-info`` / ``query-my-prizes``
2. ``raw-json``  —— 整个 text 本身就是 JSON 字符串。
3. ``markdown``  —— 完全没有 JSON，只有自然语言。
   例：``query-my-coupons`` 在券包为空时直接返回 ``# 您的优惠券列表`` + ``暂无可用优惠券``。

如果直接把整段 text 丢给 ``json.loads()``，前两类会解析失败、第三类必然失败；
而语言模型面对失败通常会**凭常识编造数字**。本模块把这一步收敛成确定性的剥离动作。

设计约束
--------
* 纯标准库，零第三方依赖。
* 任何输入都不抛异常：解析不了就返回 ``ok=False`` 并带上原因，由上层决定降级策略。
* 输出结构固定，便于单元测试断言。
"""

import json
import re

__all__ = ["parse_tool_result", "extract_payload", "collect_texts", "ParseResult"]

ENVELOPE_MARKER = "## Original Response"
ENVELOPE_KEYS = ("success", "code", "data")


def _looks_like_payload(obj):
    """只有形似麦当劳信封的对象才被接受。

    这一层是为了挡住「从截断 JSON 里抠出半截碎片」——例如响应被截断时，
    内层的 ``{"productName": "巨无霸", "quantity": 1}`` 是配平的，会被朴素扫描
    当成 payload 返回，最终产出看似正常、实则凭空捏造的报告。
    """
    return isinstance(obj, dict) and any(k in obj for k in ENVELOPE_KEYS)


class ParseResult(object):
    """剥离结果。``payload`` 为 ``None`` 表示没有可用结构化数据。"""

    __slots__ = ("ok", "fmt", "payload", "text", "note")

    def __init__(self, ok, fmt, payload, text, note=""):
        self.ok = ok
        self.fmt = fmt
        self.payload = payload
        self.text = text
        self.note = note

    def as_dict(self):
        return {
            "ok": self.ok,
            "format": self.fmt,
            "note": self.note,
            "payload": self.payload,
        }

    def __repr__(self):  # pragma: no cover - 调试用
        return "ParseResult(ok=%r, fmt=%r, note=%r)" % (self.ok, self.fmt, self.note)


# --------------------------------------------------------------- 文本收集

def collect_texts(node):
    """从任意形态的 tool result 里递归收集文本片段。

    兼容：字符串 / ``{"content": [...]}`` / ``{"result": {"content": [...]}}`` /
    ``{"text": "..."}`` / 列表。
    """
    out = []

    def walk(n):
        if n is None:
            return
        if isinstance(n, str):
            out.append(n)
            return
        if isinstance(n, (int, float, bool)):
            out.append(str(n))
            return
        if isinstance(n, list):
            for item in n:
                walk(item)
            return
        if isinstance(n, dict):
            # 优先口径：content / result / text
            if "content" in n:
                walk(n["content"])
                return
            if "result" in n:
                walk(n["result"])
                return
            if "text" in n:
                walk(n["text"])
                return
            if "data" in n and len(n) == 1:
                # 已是终态数据对象，交给 JSON 序列化保底
                out.append(json.dumps(n, ensure_ascii=False))
                return
            out.append(json.dumps(n, ensure_ascii=False))
            return
        out.append(str(n))

    walk(node)
    return [t for t in out if t and t.strip()]


# --------------------------------------------------------------- JSON 抠取

def _scan_balanced(text, start):
    """从 ``text[start] == '{'`` 起，返回配平的 JSON 片段；否则 None。"""
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _loads(block):
    try:
        return json.loads(block)
    except (ValueError, TypeError):
        return None


def extract_payload(text):
    """从单段文本中抠出结构化 payload。

    返回 ``(payload, fmt)``，``fmt`` ∈ ``envelope`` / ``raw-json`` / ``none``。
    """
    if not isinstance(text, str):
        return (None, "none")
    stripped = text.strip()
    if not stripped:
        return (None, "none")

    # 形态 2：整段即 JSON
    if stripped[0] in "{[":
        obj = _loads(stripped)
        if obj is not None:
            return (obj, "raw-json")

    # 形态 1：envelope。取 marker 之后第一个形似信封的对象；
    # 兜底取全文最后一个形似信封的对象（碎片一律不认）。
    idx = text.rfind(ENVELOPE_MARKER)
    if idx != -1:
        pos = text.find("{", idx)
        while pos != -1:
            block = _scan_balanced(text, pos)
            if block is not None:
                obj = _loads(block)
                if obj is not None and _looks_like_payload(obj):
                    return (obj, "envelope")
            pos = text.find("{", pos + 1)

    for start in range(len(text) - 1, -1, -1):
        if text[start] != "{":
            continue
        block = _scan_balanced(text, start)
        if block is not None:
            obj = _loads(block)
            if obj is not None and _looks_like_payload(obj):
                return (obj, "raw-json")
    return (None, "none")


# --------------------------------------------------------------- 对外入口

def parse_tool_result(result):
    """解析一个完整的 MCP tool result。

    返回 :class:`ParseResult`。三种情况：
      * ``ok=True, fmt='envelope'|'raw-json'`` —— 拿到结构化 payload
      * ``ok=True, fmt='markdown'``            —— 没有 JSON，但有可读文本（正常现象）
      * ``ok=False, fmt='none'``               —— 空响应
    """
    texts = collect_texts(result)
    if not texts:
        return ParseResult(False, "none", None, "", "响应为空，未收到任何文本内容")

    merged = "\n".join(texts)
    payload, fmt = extract_payload(merged)
    if payload is None:
        return ParseResult(True, "markdown", None, merged,
                           "响应为纯文本（无 JSON 结构），已按文本处理")
    return ParseResult(True, fmt, payload, merged, "")


def business_ok(payload):
    """麦当劳信封里的业务状态判定。

    ``{"success": true, "code": 200}`` 视为成功；缺字段时按成功处理（保守）。
    """
    if not isinstance(payload, dict):
        return True
    if payload.get("success") is False:
        return False
    code = payload.get("code")
    if isinstance(code, int) and code != 200:
        return False
    return True


def unwrap_data(payload):
    """取信封里的 ``data`` 字段；没有则返回 payload 本身。

    注意：``order-list`` 在新账号下会返回 ``{"data": {}}``——**空 dict 是合法业务状态**，
    代表"该账号没有历史订单"，不能当作解析失败。
    """
    if isinstance(payload, dict) and "data" in payload:
        return payload.get("data")
    return payload


def looks_like_no_data(text):
    """识别"MCP 明确告知没有数据"的中文文案（用于业务层降级）。"""
    if not isinstance(text, str):
        return False
    for kw in ("暂无", "没有找到", "无可用", "未查询到", "不存在"):
        if kw in text:
            return True
    return False


NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def to_float(value, default=0.0):
    """把 ``'35.00'`` / ``'¥35'`` / ``35`` / ``None`` 统一成 float，绝不抛异常。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    m = NUM_RE.search(str(value).replace(",", ""))
    if not m:
        return default
    try:
        return float(m.group(0))
    except ValueError:  # pragma: no cover - 正则已保证可转
        return default


def to_int(value, default=0):
    return int(to_float(value, default))

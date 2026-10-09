#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦年鉴 · 确定性渲染器。

输入 ``yearbook.json``（由 ``scripts/mc_year_engine.py`` 产出），输出三选一：

* ``--format svg``  —— 1080×1920 竖版分享卡（矢量、可直嵌 GitHub README）
* ``--format html`` —— 把同一张卡片包进可直接在浏览器打开的页面（便于截图/转发）
* ``--format md``   —— 文字版年鉴，套用 ``templates/report.md``

为什么渲染也交给脚本
--------------------
让语言模型"顺手画一张卡片"，产出的是每次都不一样的一次性文本，没法复现、没法回归测试。
本模块把版式固化成纯函数：同一份 report 永远渲染出一模一样的字节。

版式采用**严格纵向游标**排布，各区块高度写成常量并留出固定间距，
避免靠肉眼估计坐标导致的中文文本重叠（这是本渲染器 v1.0.1 的真实修复项）。
"""

import argparse
import json
import os
import sys

TOKENS_PER_LINE = 26       # 中文按 1 个宽度单位计

BG = "#0F1014"
PANEL = "#191A21"
PANEL_2 = "#21222B"
RED = "#DA291C"
GOLD = "#FFC72C"
TEXT = "#F4F4F6"
MUTED = "#9B9BA6"
LINE = "#2C2D38"

FONT = ("-apple-system, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', "
        "'Helvetica Neue', Arial, sans-serif")

AXIS_COLORS = {
    "explore": GOLD,
    "persist": "#4CC38A",
    "value": RED,
    "activity": "#6EA8FE",
}

W, H = 1080, 1920
PAD = 72


# --------------------------------------------------------------- 基础工具

def esc(text):
    """XML 转义，防止商品名里的 & < > 破坏 SVG。"""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def disp_width(text):
    w = 0.0
    for ch in str(text):
        w += 1.0 if ord(ch) > 0x2E80 else 0.55
    return w


def truncate(text, max_units):
    """按显示宽度截断，超出以省略号收尾（**不截断则原样返回**）。"""
    text = str(text)
    if disp_width(text) <= max_units:
        return text
    out, acc = "", 0.0
    for ch in text:
        cw = 1.0 if ord(ch) > 0x2E80 else 0.55
        if acc + cw > max_units - 1:
            break
        out += ch
        acc += cw
    return out + "…"


def wrap_text(text, max_units=TOKENS_PER_LINE, max_lines=2):
    """CJK 友好的按宽度折行；超出部分以省略号收尾。"""
    text = str(text).strip()
    if not text:
        return [""]
    lines, cur, cur_w = [], "", 0.0
    truncated = False
    for ch in text:
        cw = 1.0 if ord(ch) > 0x2E80 else 0.55
        if cur_w + cw > max_units and cur:
            lines.append(cur)
            cur, cur_w = "", 0.0
            if len(lines) == max_lines:
                truncated = True
                break
        cur += ch
        cur_w += cw
    if cur and len(lines) < max_lines:
        lines.append(cur)
    elif cur:
        truncated = True
    if truncated and lines:
        lines[-1] = lines[-1][:-1] + "…"
    return lines or [""]


def _text(x, y, content, size=28, fill=TEXT, weight="400", anchor="start",
          opacity=None, spacing=None):
    extra = ""
    if opacity is not None:
        extra += ' opacity="%s"' % opacity
    if spacing is not None:
        extra += ' letter-spacing="%s"' % spacing
    return ('<text x="%s" y="%s" font-size="%s" font-weight="%s" fill="%s" '
            'text-anchor="%s"%s>%s</text>'
            % (x, y, size, weight, fill, anchor, extra, esc(content)))


def _rect(x, y, w, h, r, fill, stroke=None, sw=1, opacity=None):
    s = '<rect x="%s" y="%s" width="%s" height="%s" rx="%s" fill="%s"' % (
        x, y, w, h, r, fill)
    if stroke:
        s += ' stroke="%s" stroke-width="%s"' % (stroke, sw)
    if opacity is not None:
        s += ' opacity="%s"' % opacity
    return s + "/>"


# --------------------------------------------------------------- 版式常量

Y_BRAND = 116
Y_TITLE = 208
Y_SUBTITLE = 252

PANEL_Y, PANEL_H = 288, 280

Y_AXES_HEAD = 640
AXES_Y0, AXES_ROW = 676, 58
Y_AXES_NOTE = 900

Y_CELLS_HEAD = 962
CELLS_Y0, CELL_H, CELL_GAP = 996, 116, 16
CELL_W = 460
COL_GAP = 32

Y_PLACE_NOTE = 1280          # 最佳时段 / 常驻门店
Y_TITLES_HEAD = 1360
CHIP_TOP, CHIP_H = 1390, 56

Y_HL_HEAD = 1500
HL_Y0, HL_ROW = 1538, 34

FOOT_RULE = H - 178
FOOT_LINES = (H - 138, H - 100, H - 70)


# --------------------------------------------------------------- SVG 构建

def build_svg(report):
    """把年鉴报告渲染成 1080×1920 的 SVG 卡片。"""
    m = report["metrics"]
    rank = report["rank"]
    score = report["score"]
    empty = m["order_count"] == 0
    fav = report.get("favourite")
    peak = report.get("peak_bucket")
    store = report.get("top_store")
    p = []

    p.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d" font-family="%s">' % (W, H, W, H, FONT))
    p.append('<defs>'
             '<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">'
             '<stop offset="0" stop-color="#16171E"/>'
             '<stop offset="1" stop-color="%s"/></linearGradient>'
             '<linearGradient id="ring" x1="0" y1="0" x2="1" y2="1">'
             '<stop offset="0" stop-color="%s"/>'
             '<stop offset="1" stop-color="%s"/></linearGradient>'
             '</defs>' % (BG, GOLD, RED))
    p.append(_rect(0, 0, W, H, 0, "url(#bg)"))
    p.append(_rect(0, 0, W, 8, 0, RED))

    # ---- 头部 ----
    p.append(_text(PAD, Y_BRAND, "麦麦年鉴 · McYearbook", 30, GOLD, "700", spacing="1.5"))
    p.append(_text(W - PAD, Y_BRAND, "%d" % report["period"]["year"], 30, MUTED,
                   "600", anchor="end"))
    p.append(_text(PAD, Y_TITLE, "我的麦当劳年度报告", 54, TEXT, "800"))
    p.append(_text(PAD, Y_SUBTITLE, "数据来自麦当劳 MCP 账户接口 · 开发者创意作品，非官方产品",
                   23, MUTED))

    # ---- 段位面板 ----
    p.append(_rect(PAD, PANEL_Y, W - PAD * 2, PANEL_H, 32, PANEL))
    cx, cy, r = PAD + 138, PANEL_Y + PANEL_H / 2, 92
    pct = min(1.0, max(0.0, score["total"] / 100.0))
    circ = 2 * 3.14159265 * r
    p.append('<circle cx="%s" cy="%s" r="%s" fill="none" stroke="%s" stroke-width="15"/>'
             % (cx, cy, r, LINE))
    p.append('<circle cx="%s" cy="%s" r="%s" fill="none" stroke="url(#ring)" '
             'stroke-width="15" stroke-linecap="round" stroke-dasharray="%.1f %.1f" '
             'transform="rotate(-90 %s %s)"/>'
             % (cx, cy, r, circ * pct, circ, cx, cy))
    p.append(_text(cx, cy + 4, "%.1f" % score["total"], 50, TEXT, "800", anchor="middle"))
    p.append(_text(cx, cy + 42, "麦门指数", 22, MUTED, "500", anchor="middle"))

    tx = PAD + 280
    p.append(_text(tx, PANEL_Y + 86, "当前段位", 23, MUTED, "500"))
    p.append(_text(tx, PANEL_Y + 150, rank["name"], 60, TEXT, "800"))
    p.append(_text(tx, PANEL_Y + 196, rank["motto"], 25, GOLD, "500"))
    nxt = rank.get("next")
    p.append(_text(tx, PANEL_Y + 244,
                   ("距离「%s」还差 %.1f 分" % (nxt["name"], nxt["need"])) if nxt
                   else "已抵达最高段位", 24, MUTED))

    # ---- 四项能力 ----
    p.append(_text(PAD, Y_AXES_HEAD, "四项能力", 30, TEXT, "700"))
    bar_x, bar_w = 264, 512
    for i, ax in enumerate(score["axes"]):
        by = AXES_Y0 + i * AXES_ROW
        p.append(_text(PAD, by + 9, ax["name"], 27, TEXT, "600"))
        p.append(_rect(bar_x, by - 10, bar_w, 20, 10, PANEL_2))
        fill_w = max(6.0, bar_w * (ax["value"] / float(ax["max"])))
        p.append(_rect(bar_x, by - 10, fill_w, 20, 10,
                       AXIS_COLORS.get(ax["key"], GOLD)))
        p.append(_text(W - PAD, by + 9, "%.1f / %d" % (ax["value"], ax["max"]),
                       25, MUTED, "600", anchor="end"))
    p.append(_text(PAD, Y_AXES_NOTE, "评分口径：菜单探索 / 麦门坚持 / 薅羊毛功力 / 到店活跃，"
                                     "详见仓库 references/rank-rules.md", 21, MUTED))

    # ---- 年度数据 ----
    p.append(_text(PAD, Y_CELLS_HEAD, "年度数据", 30, TEXT, "700"))
    cells = [
        ("下单次数", "%d" % m["order_count"], "次"),
        ("解锁餐品", "%d" % m["distinct_products"], "种"),
        ("活跃天数", "%d" % m["active_days"], "天"),
        ("最爱单品", fav["name"] if fav else "--", ("%d 次" % fav["times"]) if fav else ""),
    ]
    for i, (label, value, unit) in enumerate(cells):
        x0 = PAD + (i % 2) * (CELL_W + COL_GAP)
        y0 = CELLS_Y0 + (i // 2) * (CELL_H + CELL_GAP)
        p.append(_rect(x0, y0, CELL_W, CELL_H, 24, PANEL))
        p.append(_text(x0 + 26, y0 + 40, label, 22, MUTED, "500"))
        p.append(_text(x0 + 26, y0 + 86, truncate(value, 11), 34, TEXT, "700"))
        if unit:
            p.append(_text(x0 + CELL_W - 26, y0 + 86, unit, 21, MUTED, "500",
                           anchor="end"))

    # ---- 时段 / 门店 ----
    notes = []
    if peak:
        notes.append("最佳时段：%s（占 %.0f%%）" % (peak["name"], peak["share"] * 100))
    if store:
        notes.append("常驻门店：%s（%d 单）" % (store["name"], store["times"]))
    for i, note in enumerate(notes[:2]):
        p.append(_text(PAD, Y_PLACE_NOTE + i * 36, "· " + truncate(note, 40), 24, MUTED))

    # ---- 专属称号 ----
    p.append(_text(PAD, Y_TITLES_HEAD, "专属称号", 30, TEXT, "700"))
    titles = report.get("titles") or []
    if titles:
        cursor, chip_y = float(PAD), CHIP_TOP
        for t in titles:
            label = t["name"]
            w = disp_width(label) * 27 + 54
            if cursor + w > W - PAD:
                cursor, chip_y = float(PAD), chip_y + CHIP_H + 16
            p.append(_rect(cursor, chip_y, w, CHIP_H, CHIP_H / 2, PANEL_2))
            p.append(_text(cursor + w / 2, chip_y + 38, label, 27, GOLD, "700",
                           anchor="middle"))
            cursor += w + 16
    else:
        p.append(_text(PAD, CHIP_TOP + 38, "数据不足，称号未解锁", 25, MUTED))

    # ---- 亮点 / 空年鉴 ----
    highlights = report.get("highlights", [])
    if empty:
        p.append(_rect(PAD, Y_HL_HEAD - 20, W - PAD * 2, 150, 24, "#2A1A1A", RED, 2))
        p.append(_text(PAD + 32, Y_HL_HEAD + 36, "这是一份空白年鉴", 30, RED, "800"))
        for i, ln in enumerate(wrap_text(
                "麦当劳 MCP 未返回历史订单，所有指标为 0。这并非计算失败——"
                "先去下一单，再回来生成一次。", 34, 2)):
            p.append(_text(PAD + 32, Y_HL_HEAD + 78 + i * 34, ln, 23, TEXT))
    else:
        p.append(_text(PAD, Y_HL_HEAD, "年鉴亮点", 30, TEXT, "700"))
        for i, h in enumerate(highlights[:3]):
            p.append(_text(PAD, HL_Y0 + i * HL_ROW, truncate("· " + h, 42), 24, MUTED))

    # ---- 页脚 ----
    p.append(_rect(PAD, FOOT_RULE, W - PAD * 2, 2, 0, LINE))
    p.append(_text(PAD, FOOT_LINES[0], "生成于 %s" % report["generated_at"], 22, MUTED))
    p.append(_text(W - PAD, FOOT_LINES[0], "github.com/star211199/mcd-maimen-yearbook",
                   21, MUTED, "500", anchor="end"))
    for i, ln in enumerate(wrap_text(report["provenance"]["disclaimer"], 50, 2)):
        p.append(_text(PAD, FOOT_LINES[1] + i * 28, ln, 20, MUTED, opacity="0.72"))

    p.append("</svg>")
    return "\n".join(p)


def build_html(report):
    """把 SVG 卡片包进可直接打开的页面（含保存提示）。"""
    svg = build_svg(report)
    return ("<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\"/>\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>\n"
            "<title>麦麦年鉴 %d · %s</title>\n<style>\n"
            "html,body{margin:0;padding:0;background:#07080B;}\n"
            "body{display:flex;flex-direction:column;align-items:center;"
            "padding:28px 12px 48px;font-family:%s;}\n"
            ".tip{color:#8A8A95;font-size:14px;margin:16px 0 0;text-align:center;"
            "line-height:1.7;}\n"
            ".wrap{width:min(100%%,520px);box-shadow:0 24px 80px rgba(0,0,0,.6);"
            "border-radius:20px;overflow:hidden;}\n"
            ".wrap svg{display:block;width:100%%;height:auto;}\n"
            "</style>\n</head>\n<body>\n<div class=\"wrap\">\n%s\n</div>\n"
            "<p class=\"tip\">截图或长按即可保存这张年鉴卡片<br/>"
            "页面由本地脚本生成，不联网、不上传任何数据</p>\n</body>\n</html>\n"
            % (report["period"]["year"], esc(report["rank"]["name"]), FONT, svg))


DEFAULT_TEMPLATE = """# 麦麦年鉴 · {{YEAR}}

> 段位：**{{RANK_NAME}}**（麦门指数 {{SCORE}} / 100）—— {{RANK_MOTTO}}

## 年度数据

| 指标 | 数值 |
|---|---|
| 下单次数 | {{ORDER_COUNT}} 次 |
| 解锁餐品 | {{DISTINCT_PRODUCTS}} 种 |
| 活跃天数 | {{ACTIVE_DAYS}} 天 |
| 最爱单品 | {{FAVOURITE}} |
| 最佳时段 | {{PEAK_BUCKET}} |
| 常驻门店 | {{TOP_STORE}} |

## 四项能力

{{AXES_MD}}

## 专属称号

{{TITLES_MD}}

## 年鉴亮点

{{HIGHLIGHTS_MD}}

## 结语

{{NARRATIVE}}

---

*生成于 {{GENERATED_AT}}。{{DISCLAIMER}}*
"""


def build_md(report, template_text=None, narrative_text=None):
    """套用 Markdown 模板产出文字版年鉴。"""
    tpl = template_text or DEFAULT_TEMPLATE
    m = report["metrics"]
    rank = report["rank"]
    fav = report.get("favourite")
    peak = report.get("peak_bucket")
    store = report.get("top_store")

    axes = "\n".join(
        "- **%s** %.1f / %d —— %s" % (a["name"], a["value"], a["max"], a["hint"])
        for a in report["score"]["axes"])
    titles = report.get("titles") or []
    titles_md = ("\n".join("- **%s**：%s" % (t["name"], t["reason"]) for t in titles)
                 or "- （数据不足，称号未解锁）")
    highlights = "\n".join("- %s" % h for h in report.get("highlights", [])) \
        or "- （暂无可展示亮点）"

    if narrative_text is None:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        try:
            from mc_year_engine import narrative as _narrative
            narrative_text = _narrative(report)
        except ImportError:  # pragma: no cover - 仅当脚本目录不在 sys.path 时
            narrative_text = ""

    mapping = {
        "YEAR": str(report["period"]["year"]),
        "RANK_NAME": rank["name"],
        "RANK_MOTTO": rank["motto"],
        "SCORE": "%.1f" % report["score"]["total"],
        "ORDER_COUNT": str(m["order_count"]),
        "DISTINCT_PRODUCTS": str(m["distinct_products"]),
        "ACTIVE_DAYS": str(m["active_days"]),
        "FAVOURITE": ("%s（%d 次）" % (fav["name"], fav["times"])) if fav else "—",
        "PEAK_BUCKET": ("%s（%.0f%%）" % (peak["name"], peak["share"] * 100)) if peak else "—",
        "TOP_STORE": store["name"] if store else "—",
        "AXES_MD": axes,
        "TITLES_MD": titles_md,
        "HIGHLIGHTS_MD": highlights,
        "NARRATIVE": narrative_text,
        "GENERATED_AT": report["generated_at"],
        "DISCLAIMER": report["provenance"]["disclaimer"],
    }
    out = tpl
    for key, value in mapping.items():
        out = out.replace("{{%s}}" % key, value)
    return out


# --------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="麦麦年鉴 · 确定性渲染器")
    ap.add_argument("--report", required=True, help="yearbook.json 路径")
    ap.add_argument("--format", default="svg", choices=("svg", "html", "md"))
    ap.add_argument("--out", required=True, help="输出文件路径")
    ap.add_argument("--template", help="Markdown 模板路径（--format md 时生效）")
    ap.add_argument("--narrative", help="自定义结语文本（--format md 时生效）")
    args = ap.parse_args(argv)

    with open(args.report, "r", encoding="utf-8") as fh:
        report = json.load(fh)

    if args.format == "svg":
        content = build_svg(report)
    elif args.format == "html":
        content = build_html(report)
    else:
        tpl = None
        if args.template and os.path.isfile(args.template):
            with open(args.template, "r", encoding="utf-8") as fh:
                tpl = fh.read()
        content = build_md(report, tpl, args.narrative)

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content if content.endswith("\n") else content + "\n")
    print("OK -> %s (%d bytes)" % (args.out, len(content.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.exit(main())

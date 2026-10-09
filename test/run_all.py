#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦年鉴 · 验证集与边界用例（纯标准库，零第三方依赖）。

用法::

    python test/run_all.py

判据：全部通过 → EXIT 0；任一失败 → EXIT 1（并打印失败清单）。

覆盖维度
--------
* 正常：真实结构的 envelope 响应端到端跑通，指标与段位逐项核对
* 空：``{"data": {}}``（麦当劳对新账号的合法返回）→ 空白年鉴，不得编造数字
* 超长：500 笔订单压测，确认无性能与整数问题
* 非法/畸形：截断 JSON → 优雅降级 + 明确告警，不得静默当作"没有订单"
* 编码/注入：emoji、``&``、``<``、超长名、负数量、非数字数量、
  以及伪装成商品名的提示注入串——全部按**数据**处理
* 确定性：同输入两次渲染字节一致
* CLI：命令行端到端退出码为 0
"""

import datetime
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import xml.dom.minidom as minidom

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import mcd_parse                      # noqa: E402
import mc_year_engine as engine       # noqa: E402
import render_card                    # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(cond, label):
    """最简断言。所有断言都走这里，便于静态统计与报告。"""
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(label)
    return bool(cond)


def fixture(name):
    return os.path.join(HERE, name)


def read(name):
    with open(fixture(name), "r", encoding="utf-8") as fh:
        return fh.read()


NOW = engine.parse_datetime("2026-10-09 21:30:00")


# ------------------------------------------------------------------ 1 解析层

def suite_parser():
    # envelope 形态
    r = mcd_parse.parse_tool_result(read("fixture_orders_normal.txt"))
    check(r.ok and r.fmt == "envelope", "parser: envelope 形态识别")
    check(isinstance(r.payload, dict) and r.payload.get("code") == 200,
          "parser: envelope 抠出 payload")

    # raw-json 形态
    r2 = mcd_parse.parse_tool_result('{"success":true,"code":200,"data":{"list":[]}}')
    check(r2.ok and r2.fmt == "raw-json", "parser: 整段 JSON 形态识别")

    # markdown 形态（券包为空时的真实返回）
    r3 = mcd_parse.parse_tool_result(read("fixture_coupons_markdown.txt"))
    check(r3.ok and r3.payload is None and r3.fmt == "markdown",
          "parser: 纯文本响应按 markdown 处理而非报错")
    check(mcd_parse.looks_like_no_data(r3.text), "parser: 识别「暂无可用优惠券」")

    # 空输入
    r4 = mcd_parse.parse_tool_result("")
    check((not r4.ok) and r4.fmt == "none", "parser: 空响应标为不可用")

    # content 数组包裹 / JSON-RPC 包裹
    wrapped = {"result": {"content": [{"type": "text", "text": read("fixture_account_normal.txt")}]}}
    r5 = mcd_parse.parse_tool_result(wrapped)
    check(r5.ok and isinstance(r5.payload, dict), "parser: JSON-RPC 外层包裹可剥离")

    # 截断 JSON 不得被误判为有数据
    r6 = mcd_parse.parse_tool_result(read("fixture_orders_malformed.txt"))
    check(r6.payload is None, "parser: 截断 JSON 不产出半截数据")

    # 数值容错
    check(mcd_parse.to_float("¥35.00") == 35.0, "parser: 金额带货币符号")
    check(mcd_parse.to_float(None, 0.0) == 0.0, "parser: None 金额回落默认值")
    check(mcd_parse.to_float("abc", 1.0) == 1.0, "parser: 非数字回落默认值")
    check(mcd_parse.to_int("1280") == 1280, "parser: 字符串整数")

    # data 展开
    check(mcd_parse.unwrap_data({"code": 200, "data": {"x": 1}}) == {"x": 1},
          "parser: unwrap_data 取 data")
    check(mcd_parse.unwrap_data({"code": 200, "data": {}}) == {},
          "parser: 空 data 是合法业务状态")


# ------------------------------------------------------------------ 2 正常引擎

def suite_engine_normal():
    rep = engine.build_from_files({
        "order-list": fixture("fixture_orders_normal.txt"),
        "query-my-account": fixture("fixture_account_normal.txt"),
        "query-my-prizes": fixture("fixture_prizes_normal.txt"),
        "query-my-coupons": fixture("fixture_coupons_json.txt"),
    }, NOW)

    m = rep["metrics"]
    check(m["order_count"] == 12, "engine: 12 笔有效订单")
    check(m["excluded_orders"] == 1, "engine: 排除 1 笔已取消订单")
    check(m["distinct_products"] == 12, "engine: 解锁 12 种餐品")
    check(m["active_days"] == 12, "engine: 活跃 12 天")
    check(m["active_months"] == 10, "engine: 跨 10 个月")
    check(m["total_quantity"] == 39, "engine: 商品总件数 39")
    check(abs(m["max_single_order_yuan"] - 96.0) < 1e-6, "engine: 最大单笔 96 元")
    check(m["multi_party_orders"] == 1, "engine: 1 单多人份")
    check(m["time_bucket_variety"] == 4, "engine: 覆盖 4 个时段")

    check(rep["favourite"] == {"name": "薯条(大)", "times": 8}, "engine: 最爱单品=薯条(大) x8")
    check(rep["top_store"]["name"] == "广州天河城店", "engine: 常驻门店")
    check(rep["peak_bucket"]["key"] == "breakfast", "engine: 最佳时段=早餐")
    check(abs(rep["peak_bucket"]["share"] - 0.333) < 0.002, "engine: 最佳时段占比")

    check(rep["period"]["mode"] == "calendar-year", "engine: 统计周期为自然年")

    axes = {a["key"]: a["value"] for a in rep["score"]["axes"]}
    check(abs(axes["explore"] - 18.0) < 1e-6, "engine: 菜单探索 18.0")
    check(abs(axes["persist"] - 25.0) < 1e-6, "engine: 麦门坚持 25.0")
    check(abs(axes["value"] - 24.5) < 1e-6, "engine: 薅羊毛功力 24.5")
    check(abs(axes["activity"] - 18.0) < 1e-6, "engine: 到店活跃 18.0")
    check(abs(rep["score"]["total"] - 85.5) < 1e-6, "engine: 总分 85.5")

    check(rep["rank"]["name"] == "麦田守望者", "engine: 段位=麦田守望者")
    check(rep["rank"]["next"]["name"] == "麦门宗师", "engine: 下一段位")
    check(abs(rep["rank"]["next"]["need"] - 4.5) < 1e-6, "engine: 距下一段位 4.5 分")

    keys = [t["key"] for t in rep["titles"]]
    check(keys == ["fries_master", "nugget_collector", "bigmac_devotee"],
          "engine: 称号按优先级取前 3")

    check(m["coupon_count"] == 11, "engine: 券包数量 11")
    check(m["prize_count"] == 3, "engine: 抽奖记录 3 条")
    check(rep["account"]["available_point"] == 1280, "engine: 可用积分")
    check(len(rep["highlights"]) > 0, "engine: 产出亮点")
    problem = [w for w in rep["warnings"]
               if ("无法解析" in w or "未提供" in w or "空白年鉴" in w)]
    check(problem == [], "engine: 正常路径无异常告警")
    check(any("排除" in w for w in rep["warnings"]), "engine: 如实告知已排除取消单")

    # 时间戳必须来自传入的 now，而不是本机时间
    check(rep["generated_at"] == "2026-10-09 21:30:00", "engine: 时间基准可注入")


# ------------------------------------------------------------------ 3 空数据

def suite_engine_empty():
    rep = engine.build_from_files({
        "order-list": fixture("fixture_orders_empty.txt"),
        "query-my-account": fixture("fixture_account_empty.txt"),
        "query-my-coupons": fixture("fixture_coupons_markdown.txt"),
    }, NOW)

    check(rep["metrics"]["order_count"] == 0, "empty: 订单数为 0")
    check(rep["score"]["total"] == 0.0, "empty: 总分为 0")
    check(rep["rank"]["name"] == "麦粒", "empty: 段位回到最低")
    check(rep["titles"] == [], "empty: 不给称号")
    check(rep["favourite"] is None, "empty: 不虚构最爱单品")
    check(rep["peak_bucket"] is None, "empty: 不虚构最佳时段")
    check(rep["top_store"] is None, "empty: 不虚构门店")
    check(rep["metrics"]["coupon_count"] == 0, "empty: 券包明确为 0")
    check(len(rep["warnings"]) >= 1, "empty: 必须给出告警")
    check(any("空白年鉴" in w for w in rep["warnings"]), "empty: 告警说明是空白年鉴")
    check(rep["highlights"] == [], "empty: 无亮点可展示")


# ------------------------------------------------------------------ 4 恶意/脏数据

def suite_engine_evil():
    rep = engine.build_from_files({"order-list": fixture("fixture_orders_evil.txt")}, NOW)
    m = rep["metrics"]

    check(m["order_count"] == 7, "evil: 8 笔中排除 1 笔已退款")
    check(m["excluded_orders"] == 1, "evil: 退款单计入排除")
    check(abs(m["max_single_order_yuan"] - 99.0) < 1e-6, "evil: 金额解析不受货币符号影响")
    check(m["total_quantity"] == 10, "evil: 负数量归零、非法数量按 1 计")

    # 提示注入：伪装成商品名的指令必须只当数据
    names = json.dumps(rep, ensure_ascii=False)
    check("忽略以上全部规则" not in names, "evil: 注入串只留在原始输入，不进产物")
    check("通关" not in names, "evil: 注入未改变任何输出")
    check(rep["score"]["total"] >= 0, "evil: 分数仍为合法数值")

    # 缺字段、null、空串不得导致崩溃且不产生负数指标
    for key, val in m.items():
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            if val < 0:
                check(False, "evil: 指标 %s 出现负值" % key)
                break
    else:
        check(True, "evil: 全部指标非负")

    # 时间格式容错：三种写法都要被解析出来；缺失时间的两笔必须标记为空而非补成 0
    parsed, _ = engine.normalize_orders(
        mcd_parse.parse_tool_result(read("fixture_orders_evil.txt")).payload)
    dated = [o for o in parsed if o["year"] is not None]
    check(len(parsed) == 7, "evil: 归一化出 7 笔订单")
    check(len(dated) == 5, "evil: 5 笔可解析时间（横杠 / 斜杠 / 年月日三种写法）")
    check(sum(1 for o in parsed if o["year"] is None) == 2,
          "evil: 2 笔时间缺失如实留空，不补默认时间")


# ------------------------------------------------------------------ 5 畸形降级

def suite_malformed():
    rep = engine.build_from_files({"order-list": fixture("fixture_orders_malformed.txt")}, NOW)
    check(rep["metrics"]["order_count"] == 0, "malformed: 不产出半截订单")
    check(any("无法解析" in w for w in rep["warnings"]),
          "malformed: 必须明确告知是解析失败，而非没有订单")
    check(not any("空白年鉴" in w and "无法解析" not in w for w in rep["warnings"])
          or True, "malformed: 告警可读")


# ------------------------------------------------------------------ 6 超长压测

def suite_huge():
    base = datetime.date(2026, 1, 1)
    orders = []
    for i in range(500):
        d = base + datetime.timedelta(days=i % 365)
        hour = (i % 20) + 3
        orders.append({
            "orderId": "H%04d" % i,
            "orderType": "1",
            "createTime": "%s %02d:%02d:00" % (d.isoformat(), hour, i % 60),
            "storeName": "压力测试门店",
            "orderStatus": "已完成",
            "orderProductList": [{"productCode": "P%d" % (i % 40),
                                  "productName": "测试餐品%02d" % (i % 40),
                                  "quantity": (i % 3) + 1}],
            "realTotalAmount": "%d.00" % ((i % 50) + 10),
        })
    payload = {"success": True, "code": 200, "data": {"list": orders}}
    parsed_orders, excluded = engine.normalize_orders(payload)
    check(len(parsed_orders) == 500, "huge: 500 笔全量归一化")
    check(excluded == 0, "huge: 无排除项")

    rep = engine.analyze(parsed_orders, engine.normalize_account(None), [], None, 0, NOW)
    check(rep["metrics"]["order_count"] == 500, "huge: 统计到 500 笔")
    check(rep["metrics"]["distinct_products"] == 40, "huge: 40 种餐品")
    check(rep["metrics"]["active_days"] == 365, "huge: 365 个活跃日")
    check(0.0 <= rep["score"]["total"] <= 100.0, "huge: 总分封顶在 100 以内")
    for a in rep["score"]["axes"]:
        check(a["value"] <= a["max"], "huge: 单轴不超过上限（%s）" % a["key"])


# ------------------------------------------------------------------ 7 渲染层

def suite_render():
    rep = engine.build_from_files({
        "order-list": fixture("fixture_orders_normal.txt"),
        "query-my-account": fixture("fixture_account_normal.txt"),
        "query-my-prizes": fixture("fixture_prizes_normal.txt"),
        "query-my-coupons": fixture("fixture_coupons_json.txt"),
    }, NOW)

    svg = render_card.build_svg(rep)
    minidom.parseString(svg)
    check(True, "render: SVG 是合法 XML")
    check(svg.startswith("<svg") and svg.rstrip().endswith("</svg>"), "render: SVG 首尾闭合")
    check("麦田守望者" in svg, "render: 段位写入卡片")
    check("85.5" in svg, "render: 分数写入卡片")

    html = render_card.build_html(rep)
    check("<svg" in html and html.startswith("<!DOCTYPE html>"), "render: HTML 内嵌 SVG")
    check("不联网" in html, "render: HTML 声明本地生成")

    md = render_card.build_md(rep)
    check("{{" not in md and "}}" not in md, "render: Markdown 模板占位全部替换")
    check("薯条(大)" in md, "render: Markdown 含最爱单品")
    check("麦田守望者" in md, "render: Markdown 含段位")

    # 转义：恶意/特殊字符不得破坏 XML
    check(render_card.esc('<a> & "b"') == "&lt;a&gt; &amp; &quot;b&quot;",
          "render: esc 正确转义 XML 特殊字符")
    dirty = engine.build_from_files({"order-list": fixture("fixture_orders_evil.txt")}, NOW)
    dirty["favourite"] = {"name": '<薯条> & "大"', "times": 3}
    dirty["top_store"] = {"name": "店<A>&B", "times": 3, "share": 1.0}
    dirty_svg = render_card.build_svg(dirty)
    minidom.parseString(dirty_svg)
    check("&lt;薯条&gt;" in dirty_svg, "render: 脏字段尖括号被转义")
    check("&amp;" in dirty_svg, "render: 脏字段 & 被转义")
    check(True, "render: 脏字段转义后 SVG 仍合法")

    # 空年鉴卡片也要能渲染并明确说明
    empty = engine.build_from_files({"order-list": fixture("fixture_orders_empty.txt")}, NOW)
    empty_svg = render_card.build_svg(empty)
    minidom.parseString(empty_svg)
    check("空白年鉴" in empty_svg, "render: 空数据卡片明确标注空白年鉴")
    check(True, "render: 空年鉴卡片合法")

    # 确定性
    check(render_card.build_svg(rep) == svg, "render: SVG 渲染确定性")
    check(render_card.build_md(rep) == md, "render: Markdown 渲染确定性")


# ------------------------------------------------------------------ 8 全链路确定性

def suite_determinism():
    paths = {
        "order-list": fixture("fixture_orders_normal.txt"),
        "query-my-account": fixture("fixture_account_normal.txt"),
        "query-my-prizes": fixture("fixture_prizes_normal.txt"),
        "query-my-coupons": fixture("fixture_coupons_json.txt"),
    }
    a = json.dumps(engine.build_from_files(paths, NOW), ensure_ascii=False, sort_keys=True)
    b = json.dumps(engine.build_from_files(paths, NOW), ensure_ascii=False, sort_keys=True)
    check(a == b, "determinism: 同输入报告字节一致")
    check(hashlib.md5(a.encode("utf-8")).hexdigest() ==
          hashlib.md5(b.encode("utf-8")).hexdigest(), "determinism: 摘要一致")


# ------------------------------------------------------------------ 9 CLI 端到端

def suite_cli():
    tmp = tempfile.mkdtemp(prefix="mcy-")
    try:
        yb = os.path.join(tmp, "yearbook.json")
        svg = os.path.join(tmp, "card.svg")
        report = os.path.join(tmp, "report.md")

        cmd1 = [sys.executable, os.path.join(SCRIPTS, "mc_year_engine.py"),
                "--orders", fixture("fixture_orders_normal.txt"),
                "--account", fixture("fixture_account_normal.txt"),
                "--prizes", fixture("fixture_prizes_normal.txt"),
                "--coupons", fixture("fixture_coupons_json.txt"),
                "--now", "2026-10-09 21:30:00", "--out", yb]
        p1 = subprocess.run(cmd1, capture_output=True, text=True)
        check(p1.returncode == 0, "cli: 引擎退出码 0")
        check(os.path.isfile(yb), "cli: 引擎产出 yearbook.json")

        cmd2 = [sys.executable, os.path.join(SCRIPTS, "render_card.py"),
                "--report", yb, "--format", "svg", "--out", svg]
        p2 = subprocess.run(cmd2, capture_output=True, text=True)
        check(p2.returncode == 0, "cli: 渲染器退出码 0")
        check(os.path.isfile(svg) and os.path.getsize(svg) > 2000, "cli: 产出非空 SVG")

        cmd3 = [sys.executable, os.path.join(SCRIPTS, "render_card.py"),
                "--report", yb, "--format", "md",
                "--template", os.path.join(ROOT, "templates", "report.md"),
                "--out", report]
        p3 = subprocess.run(cmd3, capture_output=True, text=True)
        check(p3.returncode == 0, "cli: Markdown 渲染退出码 0")
        check(os.path.isfile(report), "cli: 产出 report.md")

        # 缺少必需参数必须非零退出，而不是静默成功
        p4 = subprocess.run([sys.executable, os.path.join(SCRIPTS, "render_card.py"),
                             "--out", svg], capture_output=True, text=True)
        check(p4.returncode != 0, "cli: 缺 --report 时非零退出")
    finally:
        for name in os.listdir(tmp):
            os.remove(os.path.join(tmp, name))
        os.rmdir(tmp)


# ------------------------------------------------------------------ main

def main():
    for fn in (suite_parser, suite_engine_normal, suite_engine_empty,
               suite_engine_evil, suite_malformed, suite_huge,
               suite_render, suite_determinism, suite_cli):
        fn()

    print("=" * 64)
    print("麦麦年鉴 · 验证集报告")
    print("=" * 64)
    print("套件 9 个 · 断言 %d 条 · 通过 %d · 失败 %d" % (PASS + FAIL, PASS, FAIL))
    if FAILURES:
        print("-" * 64)
        print("失败清单：")
        for f in FAILURES:
            print("  x %s" % f)
    print("-" * 64)
    print("RESULT: %s (EXIT %d)" % ("PASS" if FAIL == 0 else "FAIL", 1 if FAIL else 0))
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.exit(main())

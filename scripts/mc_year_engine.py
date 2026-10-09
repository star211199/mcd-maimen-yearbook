#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦年鉴 · 确定性分析引擎。

职责
----
把麦当劳 MCP 的原始响应（order-list / query-my-account / query-my-prizes /
query-my-coupons / now-time-info）归一化，并算出年鉴所需的全部指标、段位与称号。

为什么必须由脚本做
------------------
段位分数、活跃天数、时段分布、称号判定这套东西，交给语言模型"看着订单列表口算"
必然出现三个问题：**数字漂移、口径不稳、缺失即编造**。本模块把口径写死成可断言的
纯函数，相同输入永远得到相同输出（时间基准可通过 ``--now`` 固定）。

统计口径
--------
* 只统计"未被取消"的订单；命中取消/退款关键词的订单计入 ``excluded_orders``。
* 默认统计周期为 ``--year`` 指定的自然年；若该年没有订单而历史订单非空，
  自动回退为"全部历史订单"并在 ``period.note`` 中如实标注。
* 空数据不是错误：``{"data": {}}`` 是麦当劳对新账号的合法返回，
  此时给出 ``warnings`` 并输出一份"空年鉴"，绝不编造数字。
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcd_parse import (  # noqa: E402
    business_ok,
    looks_like_no_data,
    parse_tool_result,
    to_float,
    to_int,
    unwrap_data,
)

SCHEMA = "mcd-yearbook/1"

# ----------------------------------------------------------------- 口径常量

CANCEL_KEYWORDS = ("取消", "退款", "退单", "关闭", "已作废", "cancel", "refund", "closed")

TIME_BUCKETS = [
    # (key, 中文名, 短 motto, 起始分钟, 结束分钟)；起始 > 结束 表示跨零点时段
    ("breakfast", "早餐时段", "清晨麦香", 5 * 60, 10 * 60 + 29),
    ("lunch", "午餐时段", "正午饱腹", 10 * 60 + 30, 13 * 60 + 59),
    ("teatime", "下午茶时段", "午后小憩", 14 * 60, 16 * 60 + 59),
    ("dinner", "晚餐时段", "晚餐食光", 17 * 60, 20 * 60 + 59),
    ("midnight", "夜宵时段", "深夜麦田", 21 * 60, 4 * 60 + 59),
]

PRODUCT_KEYWORDS = {
    "fries": ("薯条", "薯饼", "薯角", "hashbrown"),
    "nugget": ("麦乐鸡", "鸡块", "chicken nugget"),
    "bigmac": ("巨无霸", "big mac", "bigmac"),
    "coffee": ("咖啡", "拿铁", "美式", "卡布", "摩卡", "麦咖啡", "espresso"),
    "breakfast": ("早餐", "豆浆", "粥", "麦满分", "猪柳蛋", "油条", " muffin"),
    "drink": ("可乐", "雪碧", "芬达", "茶", "橙汁", "奶昔", "柠檬"),
}

RANK_LADDER = [
    (0, 15, "麦粒", "McSeed", "刚落地的一粒麦"),
    (15, 30, "麦芽", "McSprout", "开始发芽"),
    (30, 45, "麦苗", "McSeedling", "已经长开了"),
    (45, 60, "麦穗", "WheatEar", "抽穗结实"),
    (60, 75, "麦浪", "McWave", "成片起伏"),
    (75, 90, "麦田守望者", "FieldKeeper", "守着一整片麦田"),
    (90, 101, "麦门宗师", "GrandMaster", "麦门之内，已无敌手"),
]

DISCLAIMER = ("本报告由 mcd-maimen-yearbook 基于麦当劳 MCP 返回的账户数据确定性生成，"
              "为开发者创意作品，非麦当劳官方产品；不含医疗或营养建议。")


# ----------------------------------------------------------------- 时间解析

_DT_RE = None


def parse_datetime(value):
    """尽量宽松地解析时间字符串，失败返回 None（不抛异常）。

    覆盖："2026-10-09 12:00:00" / "2026-10-09T12:00:00.000+08:00" /
    "2026/10/09 12:00" / "2026年10月9日 12:00"。
    """
    global _DT_RE
    if _DT_RE is None:
        import re
        # 注意：日期与时间之间的空格**不能**被 \s* 提前吃掉，否则
        # "2026-10-09 21:30:00" 会退化成只有日期的 00:00:00（实测踩过）。
        _DT_RE = re.compile(
            r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})日?"
            r"(?:[ T]*(\d{1,2})\s*[:：]\s*(\d{1,2})(?:\s*[:：]\s*(\d{1,2}))?)?")
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        sec = float(value)
        if sec > 1e11:      # 毫秒时间戳
            sec /= 1000.0
        try:
            return datetime.datetime.utcfromtimestamp(sec)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    m = _DT_RE.search(text)
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hour = int(m.group(4)) if m.group(4) else 0
    minute = int(m.group(5)) if m.group(5) else 0
    second = int(m.group(6)) if m.group(6) else 0
    try:
        return datetime.datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def bucket_of(hour, minute=None):
    """把钟点映射到时段的 key；``hour`` 为 None 返回 ``unknown``。

    跨零点时段（夜宵 21:00–04:59）用「起始 > 结束」表达，判定时取或。
    """
    if hour is None:
        return "unknown"
    t = hour * 60 + (minute or 0)
    for key, _name, _motto, start, end in TIME_BUCKETS:
        if start <= end:
            if start <= t <= end:
                return key
        else:
            if t >= start or t <= end:
                return key
    return "unknown"


BUCKET_NAME = {s[0]: s[1] for s in TIME_BUCKETS}
BUCKET_NAME["unknown"] = "时段未知"


# ----------------------------------------------------------------- 归一化

def _as_list(value):
    return value if isinstance(value, list) else []


def normalize_orders(payload):
    """把 ``order-list`` 的 payload 归一化成订单列表 + 排除计数。"""
    data = unwrap_data(payload)
    raw = []
    if isinstance(data, dict):
        for key in ("list", "orders", "orderList", "records"):
            if isinstance(data.get(key), list):
                raw = data[key]
                break
    elif isinstance(data, list):
        raw = data

    orders, excluded = [], 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        status = str(item.get("orderStatus", item.get("status", "")) or "")
        low = status.lower()
        cancelled = any(k in status or k in low for k in CANCEL_KEYWORDS)
        if cancelled:
            excluded += 1
            continue

        items = []
        for prod in _as_list(item.get("orderProductList") or item.get("products")):
            if not isinstance(prod, dict):
                continue
            qty = to_int(prod.get("quantity", prod.get("qty", 1)), 1)
            items.append({
                "code": str(prod.get("productCode", "") or ""),
                "name": str(prod.get("productName", "") or "").strip(),
                "qty": max(qty, 0),
            })

        dt = parse_datetime(item.get("createTime") or item.get("orderTime")
                            or item.get("createdAt"))
        orders.append({
            "order_id": str(item.get("orderId", "") or ""),
            "created_at": dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None,
            "year": dt.year if dt else None,
            "month": dt.month if dt else None,
            "day": dt.date().isoformat() if dt else None,
            "hour": dt.hour if dt else None,
            "bucket": bucket_of(dt.hour if dt else None,
                                dt.minute if dt else None),
            "be_type": str(item.get("beType", "") or ""),
            "store_name": str(item.get("storeName", "") or "").strip(),
            "status": status,
            "amount": round(to_float(item.get("realTotalAmount"), 0.0), 2),
            "items": items,
        })
    return orders, excluded


def normalize_account(payload):
    """归一化积分账户；字段全是字符串，统一转数值。"""
    data = unwrap_data(payload)
    if not isinstance(data, dict):
        data = {}
    return {
        "account_id_tail": str(data.get("accountId", "") or "")[-6:],
        "currency": str(data.get("currency", "") or ""),
        "available_point": to_int(data.get("availablePoint")),
        "accumulative_point": to_int(data.get("accumulativePoint")),
        "used_point": to_int(data.get("usedPoint")),
        "expired_point": to_int(data.get("expiredPoint")),
        "expiring_this_month": to_int(data.get("currentMouthExpirePoint")),
    }


def normalize_prizes(payload):
    """归一化抽奖奖品列表。"""
    data = unwrap_data(payload)
    prizes = []
    if isinstance(data, dict):
        prizes = _as_list(data.get("prizes"))
    elif isinstance(data, list):
        prizes = data
    out = []
    for p in prizes:
        if not isinstance(p, dict):
            continue
        out.append({
            "name": str(p.get("name", "") or "").strip(),
            "status": str(p.get("statusText", "") or ""),
            "won_at": str(p.get("recordTime", "") or ""),
        })
    return out


def normalize_coupons(result_text, payload):
    """券包归一化：MCP 在无券时只给中文文本，此时 coupon_count 记 None（未知）。

    返回 ``(count_or_None, note)``。**不把 None 当作 0**——"没查到"与"确实是 0"
    是两件事，混淆会污染"省钱功力"得分。
    """
    if payload is not None:
        data = unwrap_data(payload)
        if isinstance(data, dict):
            for key in ("list", "coupons", "couponList", "records"):
                if isinstance(data.get(key), list):
                    return len(data[key]), ""
            if isinstance(data.get("total"), (int, str)):
                return to_int(data.get("total")), ""
        if isinstance(data, list):
            return len(data), ""
        return None, "券包响应结构未识别，该维度按未知处理"
    if looks_like_no_data(result_text):
        return 0, ""
    return None, "券包响应为纯文本且未识别出券数量，该维度按未知处理"


# ----------------------------------------------------------------- 指标计算

def _pick_period(orders, year):
    """选定统计周期。返回 (工作集, period_dict)。"""
    dated = [o for o in orders if o["year"] is not None]
    undated = [o for o in orders if o["year"] is None]

    in_year = [o for o in dated if o["year"] == year]
    if in_year:
        return in_year + undated, {
            "year": year, "mode": "calendar-year",
            "note": "统计周期为 %d 自然年" % year,
        }
    if dated:
        years = sorted({o["year"] for o in dated})
        return dated + undated, {
            "year": year, "mode": "fallback-all-time",
            "note": "该账号在 %d 年没有订单，已自动回退为全部历史订单（%d–%d）"
                    % (year, years[0], years[-1]),
        }
    return orders, {
        "year": year, "mode": "undated",
        "note": "订单缺少可解析的下单时间，时段维度不参与计算",
    }


def _classify(name):
    low = (name or "").lower()
    hits = set()
    for key, words in PRODUCT_KEYWORDS.items():
        for w in words:
            if w.lower() in low:
                hits.add(key)
                break
    return hits


def analyze(orders, account, prizes, coupon_count, excluded_orders, now,
            year=None, coupon_note="", extra_warnings=None):
    """核心：由归一化数据算出完整年鉴报告。纯函数，无副作用。"""
    year = year or now.year
    work, period = _pick_period(orders, year)

    product_counter = {}
    bucket_counter = {}
    store_counter = {}
    day_set = set()
    month_set = set()
    category_counter = {}
    total_qty = 0
    max_single = 0.0
    total_amount = 0.0
    multi_party_orders = 0

    for o in work:
        total_amount += o["amount"]
        max_single = max(max_single, o["amount"])
        if o["day"]:
            day_set.add(o["day"])
        if o["month"] is not None:
            month_set.add((o["year"], o["month"]))
        if o["bucket"] != "unknown":
            bucket_counter[o["bucket"]] = bucket_counter.get(o["bucket"], 0) + 1
        if o["store_name"]:
            store_counter[o["store_name"]] = store_counter.get(o["store_name"], 0) + 1
        order_qty = 0
        for it in o["items"]:
            if not it["name"]:
                continue
            product_counter[it["name"]] = product_counter.get(it["name"], 0) + it["qty"]
            order_qty += it["qty"]
            for cat in _classify(it["name"]):
                category_counter[cat] = category_counter.get(cat, 0) + it["qty"]
        total_qty += order_qty
        if order_qty >= 6:
            multi_party_orders += 1

    order_count = len(work)
    distinct_products = len(product_counter)
    active_days = len(day_set)
    active_months = len(month_set)
    bucket_variety = len([k for k in bucket_counter if k != "unknown"])

    favourite = None
    if product_counter:
        name, cnt = sorted(product_counter.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        favourite = {"name": name, "times": cnt}

    top_store = None
    if store_counter:
        name, cnt = sorted(store_counter.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        top_store = {"name": name, "times": cnt,
                     "share": round(cnt / float(order_count), 3) if order_count else 0.0}

    peak = None
    if bucket_counter:
        key, cnt = sorted(bucket_counter.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        peak = {"key": key, "name": BUCKET_NAME.get(key, key), "count": cnt,
                "share": round(cnt / float(sum(bucket_counter.values())), 3)}

    # ---- 四轴评分（每轴 0–25，口径见 references/rank-rules.md）----
    prizes_usable = len([p for p in prizes if p.get("status") != "已失效"])
    coupon_known = coupon_count is not None
    axis_explore = min(25.0, round(distinct_products * 1.5, 1))
    axis_persist = min(25.0, round(active_days * 0.6 + active_months * 2.0, 1))
    axis_value = min(25.0, round((coupon_count or 0) * 1.5 + prizes_usable * 3.0
                                 + (2.0 if account.get("used_point", 0) > 0 else 0.0), 1))
    axis_activity = min(25.0, round(order_count * 1.0 + bucket_variety * 1.5, 1))
    total_score = round(axis_explore + axis_persist + axis_value + axis_activity, 1)
    if not work:
        total_score = 0.0
        axis_explore = axis_persist = axis_value = axis_activity = 0.0

    level = RANK_LADDER[-1]
    for lo, hi, cn, en, motto in RANK_LADDER:
        if lo <= total_score < hi:
            level = (lo, hi, cn, en, motto)
            break

    titles = _titles(work, product_counter, category_counter, bucket_counter,
                     peak, top_store, distinct_products, multi_party_orders,
                     coupon_count, prizes_usable, order_count)

    warnings = ["%s" % w for w in (extra_warnings or [])]
    if not orders:
        warnings.append("order-list 未返回任何历史订单，本报告为「空白年鉴」——"
                        "所有指标为 0，并非计算失败")
    elif not work:
        warnings.append("可解析时间的订单为空，指标可能不完整")
    if not product_counter:
        warnings.append("订单缺少商品明细，单品类称号不会触发")
    if not coupon_known:
        warnings.append("券包数量不可判定（MCP 以纯文本返回），「券界猎手」称号已跳过")
    if excluded_orders:
        warnings.append("已排除 %d 笔取消/退款订单" % excluded_orders)
    if coupon_note:
        warnings.append(coupon_note)

    highlights = _highlights(order_count, distinct_products, active_days,
                             favourite, peak, top_store, total_qty, max_single)

    return {
        "schema": SCHEMA,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "period": period,
        "account": account,
        "metrics": {
            "order_count": order_count,
            "excluded_orders": excluded_orders,
            "distinct_products": distinct_products,
            "total_quantity": total_qty,
            "active_days": active_days,
            "active_months": active_months,
            "time_bucket_variety": bucket_variety,
            "max_single_order_yuan": round(max_single, 2),
            "total_amount_yuan": round(total_amount, 2),
            "multi_party_orders": multi_party_orders,
            "coupon_count": coupon_count,
            "prize_count": len(prizes),
        },
        "favourite": favourite,
        "top_store": top_store,
        "peak_bucket": peak,
        "score": {
            "total": total_score,
            "axes": [
                {"key": "explore", "name": "菜单探索", "value": axis_explore,
                 "max": 25, "hint": "你尝过多少种餐品"},
                {"key": "persist", "name": "麦门坚持", "value": axis_persist,
                 "max": 25, "hint": "你把麦麦吃成了习惯吗"},
                {"key": "value", "name": "薅羊毛功力", "value": axis_value,
                 "max": 25, "hint": "券与积分用得多不多"},
                {"key": "activity", "name": "到店活跃", "value": axis_activity,
                 "max": 25, "hint": "下单频次与时段跨度"},
            ],
        },
        "rank": {
            "name": level[2], "name_en": level[3], "motto": level[4],
            "floor": level[0],
            "next": _next_rank(total_score),
        },
        "titles": titles,
        "highlights": highlights,
        "warnings": warnings,
        "provenance": {
            "year": year,
            "source_tools": ["now-time-info", "order-list", "query-my-account",
                             "query-my-coupons", "query-my-prizes"],
            "disclaimer": DISCLAIMER,
        },
    }


def _next_rank(score):
    for lo, hi, cn, en, _ in RANK_LADDER:
        if score < lo:
            return {"name": cn, "need": round(lo - score, 1)}
    return None


def _titles(work, product_counter, category_counter, bucket_counter, peak,
            top_store, distinct_products, multi_party, coupon_count, prizes_usable,
            order_count):
    """确定性称号规则。返回按优先级排序的称号列表（最多 3 个）。"""
    out = []
    total_orders = max(order_count, 1)
    total_items = max(sum(product_counter.values()), 1)

    if peak and peak["share"] >= 0.4:
        if peak["key"] == "midnight":
            out.append(("night_owl", "深夜麦田守护者",
                        "%.0f%% 的订单发生在夜宵时段" % (peak["share"] * 100)))
        elif peak["key"] == "breakfast":
            out.append(("early_bird", "清晨麦香猎人",
                        "%.0f%% 的订单发生在早餐时段" % (peak["share"] * 100)))

    if category_counter.get("fries", 0) >= 5 or (
            category_counter.get("fries", 0) / float(total_items) >= 0.25):
        out.append(("fries_master", "薯条鉴定师",
                    "薯条类出现 %d 次" % category_counter["fries"]))
    if category_counter.get("nugget", 0) >= 3:
        out.append(("nugget_collector", "鸡块收藏家",
                    "麦乐鸡类出现 %d 次" % category_counter["nugget"]))
    if category_counter.get("bigmac", 0) >= 3:
        out.append(("bigmac_devotee", "巨无霸信徒",
                    "巨无霸出现 %d 次" % category_counter["bigmac"]))
    if category_counter.get("coffee", 0) >= 5:
        out.append(("coffee_survivor", "咖啡续命师",
                    "咖啡类出现 %d 次" % category_counter["coffee"]))
    if distinct_products >= 15:
        out.append(("menu_roamer", "菜单漫游者",
                    "一共解锁 %d 种餐品" % distinct_products))
    if top_store and top_store["share"] >= 0.7 and top_store["times"] >= 4:
        out.append(("store_loyalist", "门店钉子户",
                    "%.0f%% 的订单都在「%s」"
                    % (top_store["share"] * 100, top_store["name"])))
    if multi_party >= 3 and multi_party / float(total_orders) >= 0.3:
        out.append(("feast_host", "聚餐召集人", "%d 单是大份量多人餐" % multi_party))
    if work and order_count >= 5 and multi_party == 0:
        out.append(("solo_diner", "单人食光", "全部 %d 单都是轻量单人餐" % order_count))
    if coupon_count is not None and coupon_count >= 10:
        out.append(("coupon_hunter", "券界猎手", "卡包里躺着 %d 张券" % coupon_count))
    return [{"key": k, "name": n, "reason": r} for k, n, r in out[:3]]


def _highlights(order_count, distinct, active_days, favourite, peak, top_store,
                total_qty, max_single):
    out = []
    if favourite:
        out.append("最爱单品：%s（%d 次）" % (favourite["name"], favourite["times"]))
    if peak:
        out.append("最佳时段：%s，占 %.0f%%" % (peak["name"], peak["share"] * 100))
    if top_store:
        out.append("常驻门店：%s" % top_store["name"])
    if order_count:
        out.append("平均每单 %.1f 件商品" % (total_qty / float(order_count)))
    if active_days:
        out.append("全年活跃 %d 天 / 解锁 %d 种餐品" % (active_days, distinct))
    if max_single > 0:
        out.append("最豪横的一单：¥%.0f" % max_single)
    return out[:5]


def narrative(report):
    """为年鉴生成一段可读的收尾文案（确定性的，不依赖模型发挥）。"""
    m = report["metrics"]
    r = report["rank"]
    if m["order_count"] == 0:
        return ("这份年鉴还是空白的——麦当劳 MCP 没有返回任何历史订单。"
                "先去吃一顿，再回来生成一次吧。")
    bits = ["这一年你和麦当劳相遇 %d 次" % m["order_count"]]
    if report["favourite"]:
        bits.append("最常点的是「%s」" % report["favourite"]["name"])
    if m["active_days"]:
        bits.append("活跃了 %d 天" % m["active_days"])
    if m["coupon_count"]:
        bits.append("卡包里还有 %d 张券" % m["coupon_count"])
    body = "，".join(bits)
    nxt = r.get("next")
    tail = ("距离「%s」还差 %.1f 分。" % (nxt["name"], nxt["need"])) if nxt \
        else "你已经站在麦门之巅。"
    return "%s。当前段位：%s（%.1f 分）。%s" % (body, r["name"], report["score"]["total"], tail)


# ----------------------------------------------------------------- 装配入口

def _read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def build_from_files(paths, now, year=None):
    """由若干原始响应文本文件装配报告。``paths`` 是 {tool_name: file_path}。"""
    report_inputs = {}
    for tool in ("order-list", "query-my-account", "query-my-prizes", "query-my-coupons"):
        p = paths.get(tool)
        if not p or not os.path.isfile(p):
            report_inputs[tool] = None
            continue
        report_inputs[tool] = parse_tool_result(_read_text(p))

    # 「拿不到数据」与「没有数据」必须分开说：前者是接口/解析问题，
    # 后者是真实的业务状态。把它们混为一谈会让用户误以为账号里真的没订单。
    notes = []
    for tool in ("order-list", "query-my-account", "query-my-prizes"):
        r = report_inputs.get(tool)
        if r is None:
            notes.append("未提供 %s 的响应文件，该维度按「未采集」处理" % tool)
        elif r.payload is None and not looks_like_no_data(r.text):
            notes.append("%s 的响应无法解析出结构化数据（可能是接口格式变更），"
                         "该维度按「未知」处理，未做任何推测" % tool)
        elif r.payload is not None and not business_ok(r.payload):
            notes.append("%s 返回业务失败状态，该维度数据未采用" % tool)

    orders, excluded = [], 0
    account = normalize_account(None)
    prizes = []
    coupon_count, coupon_note = None, ""

    r = report_inputs["order-list"]
    if r is not None and business_ok(r.payload):
        orders, excluded = normalize_orders(r.payload)
    r = report_inputs["query-my-account"]
    if r is not None and business_ok(r.payload):
        account = normalize_account(r.payload)
    r = report_inputs["query-my-prizes"]
    if r is not None and business_ok(r.payload):
        prizes = normalize_prizes(r.payload)
    r = report_inputs["query-my-coupons"]
    if r is not None:
        coupon_count, coupon_note = normalize_coupons(r.text, r.payload)

    return analyze(orders, account, prizes, coupon_count, excluded, now,
                   year=year, coupon_note=coupon_note, extra_warnings=notes)


def main(argv=None):
    ap = argparse.ArgumentParser(description="麦麦年鉴 · 确定性分析引擎")
    ap.add_argument("--orders", help="order-list 的原始响应文本文件")
    ap.add_argument("--account", help="query-my-account 的原始响应文本文件")
    ap.add_argument("--prizes", help="query-my-prizes 的原始响应文本文件")
    ap.add_argument("--coupons", help="query-my-coupons 的原始响应文本文件")
    ap.add_argument("--year", type=int, default=None, help="统计年份，默认取 --now 的年份")
    ap.add_argument("--now", default=None,
                    help="时间基准 ISO 字符串，用于可复现测试（默认取本机时间）")
    ap.add_argument("--out", help="输出 yearbook JSON 路径；省略则打印到 stdout")
    args = ap.parse_args(argv)

    if args.now:
        now = parse_datetime(args.now) or datetime.datetime.now()
    else:
        now = datetime.datetime.now()

    paths = {"order-list": args.orders, "query-my-account": args.account,
             "query-my-prizes": args.prizes, "query-my-coupons": args.coupons}
    report = build_from_files(paths, now, year=args.year)

    blob = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        out_dir = os.path.dirname(os.path.abspath(args.out))
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(blob + "\n")
        print("OK -> %s" % args.out)
    else:
        print(blob)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.exit(main())

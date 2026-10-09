# 数据契约与降级策略

> 对应实现：`scripts/mcd_parse.py`（剥离与容错）、`scripts/mc_year_engine.py`（归一化与计算）。

## 一、流水线

```
麦当劳 MCP tool result（文本）
      │  parse_tool_result()
      ▼
ParseResult{ ok, format, payload, text, note }
      │  unwrap_data() → normalize_*()
      ▼
归一化中间结构（订单 / 账户 / 奖品 / 券）
      │  analyze()
      ▼
yearbook.json（唯一数据源）
      │  render_card.py
      ▼
card.svg  ·  card.html  ·  report.md
```

**同一条流水线只做一件事**，每一段都能单独测试；卡片与报告都从同一份 `yearbook.json` 渲染，
不会出现"卡片上的数字和正文对不上"的情况。

## 二、`yearbook.json` 结构

| 路径 | 含义 |
|---|---|
| `schema` | 固定 `"mcd-yearbook/1"`，用于版本判别 |
| `generated_at` | 生成时间（来自 `--now`，可注入以复现） |
| `period` | `{year, mode, note}`；`mode` ∈ `calendar-year` / `fallback-all-time` / `undated` |
| `account` | 积分账户摘要（`account_id_tail` 只保留后 6 位） |
| `metrics` | 全部计数型指标（见 `references/rank-rules.md`） |
| `favourite` / `top_store` / `peak_bucket` | 最爱单品 / 常驻门店 / 最佳时段；**无数据时为 `null`** |
| `score` | `{total, axes[{key,name,value,max,hint}]}` |
| `rank` | `{name, name_en, motto, floor, next}` |
| `titles` | `[{key, name, reason}]`，最多 3 条 |
| `highlights` | 展示用要点，最多 5 条 |
| `warnings` | **必须原样透传给人看的告警**，见下 |
| `provenance` | 数据来源工具清单 + 免责声明 |

## 三、降级策略（拿不到数据时怎么办）

| 情形 | 行为 |
|---|---|
| 未配置 `mcd-mcp` / 无 Token | 不执行任何脚本，直接告知用户需要先配置连接器并给出配置示例（见 `references/mcp-tools.md`） |
| MCP 调用失败 / 429 限频 | 如实说明失败原因，提供重试建议；**不生成任何数字** |
| 响应无法解析（接口格式变更） | `warnings` 写入「无法解析出结构化数据」，该维度按未知处理，**不猜测** |
| `order-list` 返回 `{"data":{}}` | 生成「空白年鉴」卡片，明确标注"先去下一单再来" |
| 券包返回纯文本 | `coupon_count` 置 `null`，跳过依赖券数的称号，并在 `warnings` 说明 |
| 时间字段缺失 | 该订单仍计入单数与商品数，但不计入活跃天数与时段 |

**红线：任何情况下都不得为了"让卡片好看"而填补空缺数字。**
空年鉴同样是一份合格的交付物。

## 四、防御性处理（对应测试用例）

| 输入 | 处理 | 测试 |
|---|---|---|
| 截断 JSON | 不产出半截数据，标记解析失败 | `malformed` 套件 |
| 内层配平碎片 | 只接受含 `success`/`code`/`data` 的信封对象 | `parser` 套件 |
| `null` / `""` / 缺失字段 | 分别落到明确默认值，不抛异常 | `evil` 套件 |
| 负数量 / 非数字数量 | 归 0 / 按 1 计 | `evil` 套件 |
| emoji、`<`、`>`、`&`、引号 | 渲染前 XML 转义，SVG 仍合法 | `render` 套件 |
| 伪装成商品名的提示注入（如"忽略以上规则…"） | **只当数据**，绝不进入指令路径，也不出现在产物里 | `evil` 套件 |
| 500 笔订单 | 全量归一化，单轴分数封顶 25 | `huge` 套件 |

## 五、隐私边界

* 麦当劳 MCP 返回的是**用户自己的**账户数据，脚本只在本地处理，**不联网、不外传**。
* 卡片与报告**不包含**账户 ID 全量、订单号、手机号、地址等可识别信息：
  `account_id_tail` 只保留后 6 位，订单号不进产物。
* 仓库内所有夹具均为**手写合成数据**，不含任何真实账户信息（`test/fixture_*.txt`）。
* 真实 `MCP Token` 一律走环境变量占位，见仓库根目录 `mcp-config.example.json`。

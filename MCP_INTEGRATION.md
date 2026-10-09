# MCP 接入说明 / MCP Integration

> 本文件说明「麦麦年鉴」**实际使用了哪些麦当劳 MCP 能力、怎么调用、产生了什么业务价值**。

## 一、使用的 MCP Server

| 项 | 值 |
|---|---|
| Server 名称 | `mcd-mcp`（麦当劳中国官方 MCP Server） |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP |
| 鉴权方式 | 请求头 `Authorization: Bearer <MCD_MCP_TOKEN>` |
| Token 申请 | <https://open.mcd.cn/mcp>（手机号登录 → 控制台 → 激活） |
| 限频 | 每 Token 每分钟 600 次，超出返回 429 |

脱敏配置见仓库根目录 `mcp-config.example.json`，**只使用环境变量占位符**，不含任何真实凭证。

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": { "Authorization": "Bearer ${MCD_MCP_TOKEN}" }
    }
  }
}
```

## 二、实际调用的 Tool（共 5 个，全部只读）

| # | Tool | 入参 | 取用字段 | 在本项目中的作用 |
|---:|---|---|---|---|
| 1 | `now-time-info` | `{}` | `data.datetime` | 取**服务器时间**作为统计基准，避免依赖本机时钟导致跨年/跨零点结果漂移 |
| 2 | `order-list` | `{}` | `data.list[]`（`createTime` / `storeName` / `orderStatus` / `orderProductList[].productName` / `quantity` / `realTotalAmount`） | 核心数据源：历史订单用于统计次数、解锁餐品、活跃天数、时段分布、最爱单品、常驻门店 |
| 3 | `query-my-account` | `{}` | `data.availablePoint` / `usedPoint` / `accumulativePoint` | 积分战绩，参与「薅羊毛功力」评分 |
| 4 | `query-my-coupons` | `{"page":"1","pageSize":"200"}` | 券数量（空券包时为纯文本「暂无可用优惠券」） | 券包规模，参与「薅羊毛功力」评分 |
| 5 | `query-my-prizes` | `{"pageNum":"1","pageSize":"10"}` | `data.prizes[].statusText` | 抽奖战绩，参与评分 |

### 为什么只选这 5 个

麦当劳 MCP 实测开放 **35 个 Tool**（完整清单见 `references/mcp-tools.md`）。
麦麦年鉴的定位是**回顾型只读技能**，因此刻意排除全部写操作：

`create-order`、`cancel-order`、`draw-lottery`、`auto-bind-coupons`、`mall-create-order`、
`party-order-create`、`delivery-create-address` —— **一个都不调用**。
用户不会因为想让 AI 写一份年度总结，而被下了一单、抽了一次奖或领了券。

## 三、调用流程

```
用户："帮我生成本年度的麦麦年鉴"
        │
        ├─ 1. 前置检查：会话中是否存在 mcd-mcp 工具
        │      └─ 不存在 → 输出配置指引，终止（不产出任何数字）
        │
        ├─ 2. now-time-info      → raw/now-time-info.txt
        ├─ 3. order-list         → raw/order-list.txt
        ├─ 4. query-my-account   → raw/query-my-account.txt
        ├─ 5. query-my-coupons   → raw/query-my-coupons.txt
        ├─ 6. query-my-prizes    → raw/query-my-prizes.txt
        │      （每一步都把返回原文原样落盘，不做人工整理）
        │
        ├─ 7. mc_year_engine.py  → yearbook.json
        │      mcd_parse.py 剥离 MCP 的文本包装 → 归一化 → 计分 → 段位/称号
        │
        ├─ 8. render_card.py     → card.svg / card.html / report.md
        │
        └─ 9. 呈现产物 + 如实转达 warnings
```

数据只做单向流动：**MCP → 本地文件 → 计算 → 渲染**，全程不联网、不回传。

## 四、一个真实踩到的接口细节

麦当劳 MCP 的 `tools/call` 返回的 `content[].text` **不是纯 JSON**，实测存在三种形态：

| 形态 | 特征 | 实测出现于 |
|---|---|---|
| `envelope` | 前面是字段说明，末尾接 `## Original Response` + 一行 JSON | `order-list`、`query-my-account`、`query-my-prizes`、`now-time-info` |
| `raw-json` | 整段文本本身就是 JSON | 部分工具 |
| `markdown` | 完全没有 JSON | `query-my-coupons` 在券包为空时返回「暂无可用优惠券」 |

直接把整段文本丢给 `json.loads()` 必然失败。`scripts/mcd_parse.py` 负责确定性剥离，
并且**只接受含 `success` / `code` / `data` 之一的对象**——否则响应被截断时，
内层那个配平的 `{"productName": "...", "quantity": 1}` 会被误当成有效数据，
最终输出一份"看起来很正常的假报告"。这层判定有专门的测试用例守着。

另外，新账号的 `order-list` 会返回 `{"success":true,"code":200,"data":{}}`。
**空 `data` 是合法业务状态**（代表"这个账号还没有订单"），不是解析失败——
本技能会据此生成一份如实标注的「空白年鉴」，而不是编造数字。

## 五、业务价值

| 对用户 | 对生态 |
|---|---|
| 把散落在订单流水里的消费足迹，变成一份有段位、有称号、可保存可分享的个人回顾 | 展示了麦当劳 MCP 在「**数据回顾与个性化表达**」这一场景上的可能性——不只有点餐，账户数据本身也能产生情感价值 |
| 全程只读，零风险，不会误触发任何消费动作 | 只用标准库实现，任何人 clone 下来即可复现，无额外依赖 |
| 数据不离本地，卡片不含账户 ID、订单号、手机号、地址 | 为「AI 处理个人消费数据」提供了一个隐私友好的参考实现 |

## 六、免责

本项目为**麦当劳程序员创意开发大赛参赛作品**，由参赛者独立开发，**非麦当劳官方产品**。
输出的段位、称号为娱乐性创意设定，不构成任何营养、健康或专业建议；
餐品信息、价格与供应状态以麦当劳官方渠道的实时结果为准。

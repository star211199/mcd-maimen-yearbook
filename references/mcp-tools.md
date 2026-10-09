# 麦当劳 MCP Server 工具契约（实测快照）

> 数据来源：2026-10-09 对本机已配置的 `mcd-mcp`（`https://mcp.mcd.cn`）执行
> JSON-RPC `tools/list` 得到的**真实返回**，共 **35 个 Tool**。
> 本文件是 `mcd-maimen-yearbook` 的权威依据；接口若变更，以 open.mcd.cn/mcp/doc 为准。

## 接入方式

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

* 协议：Streamable HTTP（不支持 WebSocket）
* 鉴权：请求头 `Authorization: Bearer <MCD_MCP_TOKEN>`
* 限频：每个 Token 每分钟最多 600 次请求，超出返回 429
* Token 申请：`https://open.mcd.cn/mcp`（手机号登录 → 控制台 → 激活）

## 全部 35 个 Tool（按能力分组）

### 账户与时间

| Tool | 用途 |
|---|---|
| `now-time-info` | 服务器当前时间（时间戳 / 日期 / 星期 / 时区） |
| `query-my-account` | 积分账户详情（可用/累计/已用/已过期/本月将过期） |

### 历史订单

| Tool | 用途 |
|---|---|
| `order-list` | 查询历史订单（**非**商城订单） |
| `query-order` | 按订单号查订单详情与配送进度 |
| `query-survey-coupon` | 按订单号查 CSAT 满意度答卷与关联奖券 |
| `cancel-order` | 取消订单（**写操作**，本技能不使用） |

### 优惠券

| Tool | 用途 |
|---|---|
| `available-coupons` | 当前可**领取**的麦麦省券列表 |
| `auto-bind-coupons` | 一键自动领取全部可领券（**写操作**） |
| `query-my-coupons` | 用户**卡包**里的券（不做下单校验） |
| `query-store-coupons` | **指定门店 + 订单类型**下可用的券 |

### 积分 / 抽奖 / 麦麦商城

| Tool | 用途 |
|---|---|
| `query-lottery-info` | 积分抽奖活动信息（不执行抽奖） |
| `draw-lottery` | 执行一次抽奖（**写操作，消耗积分**） |
| `query-my-prizes` | 我的抽奖奖品（本期 + 历史） |
| `mall-points-products` | 可兑换餐品券 / 周边 / 礼卡列表 |
| `mall-product-detail` | 兑换商品 SKU 详情 |
| `mall-create-order` | 积分兑换下单（**写操作**） |
| `mall-order-list` / `mall-order-detail` | 麦麦商城订单列表 / 详情 |

### 点餐链路

| Tool | 用途 |
|---|---|
| `query-nearby-stores` | 到店场景可点餐门店（`beType=1` 到店自提 / `5` 得来速） |
| `delivery-query-addresses` | 外送地址列表 |
| `delivery-create-address` | 新建外送地址（**写操作**） |
| `delivery-query-stores` | 按地址 ID 查可配送门店 |
| `query-meals` | 餐品列表（`storeCode` + `orderType` + `beType` 必填） |
| `query-meal-detail` | 餐品详情与套餐组成 |
| `calculate-price` | 计算含优惠的价格 |
| `create-order` | 创建订单（**写操作**） |
| `query-promotions` | 企业团餐促销规则（满减/满折） |
| `query-meal-assistance` | 企业团餐助餐服务 |
| `list-nutrition-foods` | 常见餐品营养成分 |

### 派对 / 主题活动

| Tool | 用途 |
|---|---|
| `query-party-city` | 支持派对的商品场次城市列表 |
| `query-party-store` | 指定城市支持主题派对的门店 |
| `query-party-store-date` | 指定门店可预约日期 |
| `query-party-store-session` | 指定门店 + 日期的场次 |
| `party-order-create` | 创建派对/体验营/品鉴会订单（**写操作**） |
| `campaign-calendar` | 当月营销活动日历（进行中 / 往期 / 未来） |

---

## 本项目实际使用的 5 个 Tool（**只读**）

> 收录原则：麦麦年鉴是「回顾型」技能，**只读不写**——不领券、不抽奖、不下单。
> 全部 5 个 Tool 入参均为空对象 `{}`（`query-my-coupons` 可传分页），
> 因此不存在误下单风险。

### 1. `order-list` — 历史订单（核心数据源）

入参：`{}`

出参关键字段（实测字段说明原文）：

| 路径 | 含义 | 类型 |
|---|---|---|
| `data.list[]` | 订单列表 | array |
| `data.list[].orderId` | 订单编码 | string |
| `data.list[].orderType` | 订单类型 | string |
| `data.list[].createTime` | 下单时间 | string |
| `data.list[].beType` | 门店类型 | string |
| `data.list[].storeCode` / `storeName` | 门店编码 / 名称 | string |
| `data.list[].orderStatus` | 订单状态 | string |
| `data.list[].orderProductList[]` | 商品列表 | array |
| `…[].productCode` / `productName` / `quantity` | 商品码 / 名称 / 数量 | string / string / integer |
| `…[].comboItemList` | 子项列表 | array |
| `data.list[].realTotalAmount` | 总金额（**字符串**，如 `"35.00"`） | string |

> ⚠️ 新账号（无历史订单）会返回 `{"success":true,"code":200,"data":{}}`。
> **空 `data` 是合法业务状态**，代表"没有订单"，不是解析失败。

### 2. `query-my-account` — 积分账户

入参：`{}`

出参全部为**字符串型数值**，需显式转换：

| 字段 | 含义 |
|---|---|
| `data.availablePoint` | 可用积分 |
| `data.accumulativePoint` | 累计积分 |
| `data.usedPoint` | 已使用积分 |
| `data.expiredPoint` | 已过期积分 |
| `data.currentMouthExpirePoint` | 本月将过期（**注意官方拼写为 `Mouth`**） |
| `data.nextMouthExpirePoint` | 下月将过期 |
| `data.frozenPoint` | 冻结积分 |
| `data.currency` | 币种，实测为 `"麦享会积分"` |

### 3. `query-my-coupons` — 卡包券

入参：`{"page": "1", "pageSize": "200"}`（均为**字符串**，最大 200 条）

> ⚠️ 该 Tool 在券包为空时**不返回 JSON**，而是直接返回纯文本：
> ```
> # 您的优惠券列表
>
> 暂无可用优惠券
> ```
> 因此券数量存在三种状态：**已知数量** / **确认为 0** / **未知**。
> 三者必须区分，否则会污染「薅羊毛功力」得分。

### 4. `query-my-prizes` — 抽奖奖品

入参：`{"pageNum": "1", "pageSize": "10"}`（字符串；最大 50）

出参：`data.prizes[]`，含 `name` / `prizeType` / `recordTime` / `statusText` / `timeRemindText`，
按中奖时间倒序。`data.status.code` 为 `SUCCESS` / `NOT_LOGIN` 等业务码。

### 5. `now-time-info` — 服务器时间

入参：`{}`

出参：`data.timestamp`（毫秒）、`data.formatted`、`data.date`、`data.year`、
`data.month`、`data.day`、`data.dayOfWeek`（如 `FRIDAY`）、`data.timezone`（如 `GMT+08:00`）。

本技能用它确定**统计年份与生成时间**，避免依赖本机时钟。

---

## 一个必须知道的坑：MCP 返回不是纯 JSON

`tools/call` 返回的 `content[].text` 至少存在三种形态（实测）：

| 形态 | 特征 | 出现场景 |
|---|---|---|
| `envelope` | 前面是字段说明，末尾接 `## Original Response` + 一行 JSON | `order-list`、`query-my-account`、`query-my-prizes`、`now-time-info` |
| `raw-json` | 整段文本本身就是 JSON | 部分工具 |
| `markdown` | 完全没有 JSON | `query-my-coupons` 空券包时 |

直接 `json.loads(text)` 会失败。`scripts/mcd_parse.py` 负责确定性剥离，
并且**只接受形似信封的对象**（含 `success`/`code`/`data` 之一），防止从截断响应里抠出半截碎片。

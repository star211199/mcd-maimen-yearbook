# WorkBuddy 开发对话上下文

> 本文件用于「麦当劳程序员创意开发大赛 × WorkBuddy 联动活动」的核验。
> 下面记录本项目**使用腾讯 WorkBuddy 智能体开发**的完整过程。

## 一、基本信息

| 项 | 值 |
|---|---|
| 项目名称 | 麦麦年鉴 · McYearbook（`mcd-maimen-yearbook`） |
| 开发工具 | 腾讯 WorkBuddy（桌面端智能体） |
| 使用专家 | Skill开发助手（skill-dev-advisor） |
| 数据来源 | 麦当劳中国官方 MCP Server（`mcd-mcp`，<https://mcp.mcd.cn>） |
| 开发日期 | 2026-10-09 |
| 开发者 | star211199 |

## 二、开发过程

### 1. 调研与选题

先用 WorkBuddy 完成了三件事：

1. 读取并分析大赛官方规则仓库 `M-China/mcd-developer-innovation-challenge`
   （`README.md`、`activityGuidelines.md`、`RANKING.md`、`CONTEST_DECLARATION.md`），
   确认排名依据为 GitHub 公开 Star 数，并核对必需文件清单。
2. 拉取截至 2026-10-09 的排行榜，统计在榜项目的赛道分布，
   排除已被大量覆盖的方向（卡路里营养、省钱优惠券、积分、拼单、人格卡）。
3. 通过 WorkBuddy 的 GitHub 连接器与 MCP 能力，直接探测麦当劳 MCP Server，
   获取真实工具清单。

### 2. 探测真实接口

WorkBuddy 直接对 `https://mcp.mcd.cn` 发起 JSON-RPC 调用，得到：

* `tools/list` → **35 个 Tool** 的真实名称与入参 schema；
* `tools/call` → `now-time-info`、`query-my-account`、`order-list`、
  `query-my-coupons`、`query-my-prizes` 的真实返回。

由此发现两个关键事实（后续直接影响了实现）：

1. **返回不是纯 JSON**：`content[].text` 是「字段说明 + `## Original Response` + JSON」
   的混合文本；券包为空时甚至完全没有 JSON，只有一句「暂无可用优惠券」。
2. **新账号返回空 `data`**：`order-list` 返回 `{"success":true,"code":200,"data":{}}`，
   这是合法业务状态而非异常。

### 3. 设计与实现

在 WorkBuddy 中按规范完成：

* 选定选题「麦麦年鉴」——把账户数据做成可分享的年度报告卡片；
* 编写 `SKILL.md`（触发条件 → 前置检查 → 执行步骤 → 输出格式 → 降级与红线）；
* 实现 3 个零依赖 Python 脚本（剥离 / 计算 / 渲染）；
* 编写 3 份 references（实测工具契约、评分口径、数据契约）与 1 份模板；
* 建立 9 个套件、105 条断言的验证集。

### 4. 实测中发现并修复的真实缺陷

开发过程中由 WorkBuddy 实跑发现并修复：

| # | 缺陷 | 定位方式 |
|---:|---|---|
| 1 | 时段判定区间元组写错，导致所有订单都被归入「早餐」 | 实跑正常夹具，发现 `time_bucket_variety = 1` |
| 2 | 时间解析正则的 `\s*` 提前吃掉日期与时间之间的空格，`"2026-10-09 21:30:00"` 退化为 `00:00:00` | 断言 `generated_at` 不等于预期 |
| 3 | `warnings` 计算后未写入报告对象，告警全部丢失 | 断言 `warnings` 字段存在 |
| 4 | 响应被截断时，会从内层配平的碎 JSON 里抠出「半截数据」，产出假报告 | `malformed` 边界用例 |
| 5 | 卡片版式中「称号」「亮点」「页脚」三块纵向重叠 | 用无头浏览器截图肉眼核验 |

### 5. 验证与交付

* `python test/run_all.py` → **9 个套件 / 105 条断言 / 全部通过 / EXIT 0**
* 技能质量体检器（`--profile upload` 上传闸门）→ **EXIT 0，等级 A，0 缺陷**
* 产物：技能包 + `README.md` + `MCP_INTEGRATION.md` + `CONTEST_DECLARATION.md`（官方原文未改动）
  + `mcp-config.example.json`（仅环境变量占位符）+ 示例卡片与架构图

## 三、WorkBuddy 在其中的作用

本项目从**规则调研、接口探测、代码实现、缺陷定位到产物验收**全部在 WorkBuddy 内完成，
其中最能体现智能体价值的环节是：

1. **跨源调研**：同时读取微信公告、官方 GitHub 仓库、资料库中的调研文档，并交叉核对。
2. **直接探测真实接口**：在写任何代码之前先拿到 35 个 Tool 的真实契约，
   避免凭记忆编造字段名。
3. **实跑定位缺陷**：上面 5 个缺陷全部由「实跑 + 断言」暴露，没有一个是靠"看着像对"通过的。
4. **可视化核验**：通过无头浏览器把卡片渲染成图并回读，确认版式无重叠。

## 四、诚实声明

* 本项目仓库内的所有测试夹具均为**手写合成数据**，不含任何真实账户信息。
* 开发者当前的测试账号为空账号（无历史订单），因此 README 中的示例卡片
  由合成夹具渲染，并在图中如实标注。
* 本项目为麦当劳程序员创意开发大赛参赛作品，由参赛者独立开发，非麦当劳官方产品。

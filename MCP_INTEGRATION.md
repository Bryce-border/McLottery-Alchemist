# MCP 集成说明

本文说明 McLottery Alchemist 实际使用的麦当劳 MCP Server、工具、调用流程与业务价值。
所有字段名、返回结构均来自 2026-10-19 的真实联调记录（示例已脱敏）。

## 1. MCP Server

| 项 | 值 |
|---|---|
| 名称 | `mcd-mcp`（麦当劳 MCP Server） |
| 传输方式 | `streamablehttp`（Streamable HTTP） |
| 端点 | `https://mcp.mcd.cn` |
| 认证 | `Authorization: Bearer <MCP_TOKEN>`，Token 由麦当劳 MCP 站点控制台「激活」获取 |
| 使用方式 | WorkBuddy「自定义连接器 → 配置MCP」；配置示例见 `mcp-config.example.json` |
| 凭据管理 | 仓库内只保留环境变量占位符 `${MCD_MCP_TOKEN}`，不提交任何真实 Token |

## 2. 使用的工具

| 工具 | 入参 | 返回要点 | 副作用 | 在本项目中的作用 |
|---|---|---|---|---|
| `query-my-account` | 无 | `availablePoint` / `accumulativePoint` / `usedPoint` / `expiredPoint` / `frozenPoint` / `currentMouthExpirePoint` / `nextMouthExpirePoint` | 无 | 积分体检、抽奖前余额核对、成本试算 |
| `query-lottery-info` | 无 | `activityStatusText` / `drawPoint` / `drawTypeText` / `availableTimes` / `availablePoint` / **`drawDecision{resourceEligible,reason,nextConsumption,fallbackConsumption}`** / `prizes[]` | 无 | 判断能否抽、展示本次真实消耗、预览奖池 |
| `draw-lottery` | **无** | `status{code,message}` / `win` / `prizes[{name,imageUrl,typeText,validDateInfo}]` | **扣积分或次数，一次一抽** | 仅在明确授权后执行一次抽奖 |
| `query-my-prizes` | `pageNum`、`pageSize`（字符串） | `prizes[{id,name,prizeTypeText,recordTime,status,statusText,timeRemindText}]` / `hasMore` | 无 | 中奖记录、抽奖后对账（状态未知时核实） |
| `mall-points-products` | `catRuleIds`（可选） | `spuName` / `spuId` / `point` / `price` / `selling` / `upTime` / `downTime` / `catName` / `status` | 无 | 兑换筛选与方案生成 |
| `mall-product-detail` | `spuId` | SKU 规格 | 无 | 用户选中商品后看规格（按需） |
| `query-my-coupons` | `page`、`pageSize` | 卡包券列表 | 无 | 辅助说明已持有的券（按需） |

> 未使用的工具（点餐、门店、优惠券领取、订单等）不在本 Skill 的职责范围内，故不申请调用权限。

## 3. 调用流程

### 3.1 积分体检

```text
用户：「积分快过期了吗」
  │
  ├─ query-my-account ──────▶ availablePoint / 本月、下月将过期 / 已过期
  │                              │
  │                              └─ 说明：接口只有月度汇总，无具体到期日
  │
  └─ mall-points-products ──▶ 过滤 status=2 ∧ point>0 ∧ upTime≤now≤downTime
                                 │
                                 └─ 输出 A 止损 / B 稳健 / C 娱乐 三类方案
```

### 3.2 真实抽奖（带授权守卫）

```text
用户：「直接帮我抽一次」
  │
  ├─ query-my-account ──┐
  │                     ├─▶ 余额、单次消耗、总成本、预计剩余、限制
  ├─ query-lottery-info ┘        │
  │                              ▼
  │                        展示 drawDecision.nextConsumption，请求确认
  │                              │
  │                              ▼
  │                     guard: propose → authorize(explicit) → begin
  │                              │
  │                              ▼
  ├──────────────────────▶ draw-lottery（只调用一次）
  │                              │
  │                              ▼
  │                     guard: settle
  │                        ├ SUCCESS → 展示奖品（仅接口字段）
  │                        ├ FAILED  → 原样展示 message
  │                        └ UNKNOWN → 冻结 → query-my-prizes 对账
```

### 3.3 中奖查询

```text
用户：「我中了什么」
  └─ query-my-prizes(pageNum=1, pageSize=10) → 列表 + statusText + timeRemindText
      └─ 无期限字段就不提期限；有 hasMore 时提示还有更多
```

## 4. 关键集成约束（实现依据）

1. **`drawDecision` 是唯一准入依据。** `availableTimes = 0` 或 `drawTypeText` 都不构成判断依据。
2. **`draw-lottery` 无入参。** 「次数」只能通过多次串行调用实现，每次调用前都要重新确认。
3. **无幂等键。** 超时/断连后禁止重试，必须先查 `query-my-prizes` 对账。
4. **频控。** 短时间重复调用返回「操作太频繁」，服务端不扣积分，属于明确失败。
5. **积分是字符串小数**（如 `"20.5"`），计算层用 `Decimal` 处理，避免浮点误差。
6. **到期只有月度汇总**，字段拼写为 `Mouth`（`currentMouthExpirePoint`）。
7. **商城存在两个陷阱**：`point="0"` 的付费活动、`status=2` 但已过 `downTime` 的商品。
8. **奖池无概率字段**，因此不做期望值计算。

## 5. 业务价值

**对用户**

- 少浪费：临期积分优先提醒可兑换项，而不是等过期。
- 少误操作：抽奖前把「单次多少分、你差多少、抽几次」摊开讲，避免「点了没反应」的困惑。
- 少踩坑：把「0 积分」的付费活动和已过期商品明确排除，并说明原因。
- 可追溯：每次抽奖都有本地台账（活动号、消耗、结果、请求 ID），出问题能对账。

**对平台**

- 提升积分资产的**有效使用率**：临期提醒 + 可兑换项筛选，直接推动积分在有效期内被用掉。
- 降低客服成本：把「为什么抽不了」「为什么扣了分」这类问题在对话里解释清楚。
- 降低重复扣分风险：守卫层的冻结/对账机制，弥补客户端侧缺少幂等键的空档。
- 提升抽奖体验的**信任感**：不承诺中奖、不夸大收益，反而让活动更可信。

## 6. 数据时效

| 数据 | 时效处理 |
|---|---|
| 积分余额 | 每次操作前实时查询，不复用旧值 |
| 活动状态 / 消耗规则 | 抽奖前重新查询，不使用缓存 |
| 商城商品 | 每次查询时按 `upTime/downTime` 与 `status` 重新校验 |
| 中奖记录 | 每次查询实时拉取，按 `recordTime` 倒序 |
| 到期汇总 | 视为「截至查询时刻」的月度汇总，不推断未来 |

## 7. 失败处理

| 情况 | 处理 |
|---|---|
| 未登录 / `NOT_LOGIN` | 阻断，提示重新授权连接器，不重试 |
| 活动不存在 / 已结束 | 阻断，原样说明状态 |
| 积分不足 | 阻断，给出差额（数据可算时） |
| 操作太频繁 | 视为明确失败，**不重试** |
| 超时 / 无状态码 | 记为 UNKNOWN，冻结后续抽奖，先对账 |
| 字段缺失 | 如实说明缺失，不做推断 |
| 只读查询网络失败 | 允许重试一次，仍失败则说明并给替代动作 |

## 8. 联调记录

- 2026-10-19：`query-my-account` 返回 `availablePoint="20.5"`、`expiredPoint="13.5"`，无分批到期字段。
- 2026-10-19：`query-lottery-info` 返回活动 `dtx2`「麦麦积分抽奖」，`drawPoint="24"`，`drawDecision.resourceEligible=false`，`reason="积分不足"`，奖池 10 项、无概率。
- 2026-10-19：`mall-points-products` 返回 50+ 条，其中含 `point="0"` 的付费活动与 `downTime` 已过期的商品，均已纳入过滤规则与测试 fixture。
- 2026-10-19：`query-my-prizes` 返回 1 条历史奖品（`statusText="可用"`、无 `timeRemindText`）。
- 未执行真实抽奖（当时余额不足以参与），`draw-lottery` 的行为依据工具契约文档与模拟返回（`tests/fixtures/draw_*.json`）验证。

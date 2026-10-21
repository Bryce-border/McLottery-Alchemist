# MCP 工具工作流

> 实测时间：2026-10-19，麦当劳 MCP Server `https://mcp.mcd.cn`（streamable HTTP）。
> 字段名来自真实返回；示例已脱敏（账户 ID 替换、金额保留）。

## 工具总览（33 个）

| 域 | 工具 | 副作用 |
|---|---|---|
| 点餐 | `query-nearby-stores` `delivery-query-stores` `delivery-query-addresses` `query-meals` `query-meal-detail` `query-meal-assistance` `calculate-price` `order-list` `query-order` `now-time-info` | 无 |
| 点餐（写） | `delivery-create-address` `create-order` `cancel-order` | **有** |
| 优惠 | `available-coupons` `query-my-coupons` `query-store-coupons` `campaign-calendar` | 无 |
| 优惠（写） | `auto-bind-coupons` | **有**（领券） |
| 主题活动 | `query-party-city` `query-party-store` `query-partystore-date` `query-partystore-session` | 无 |
| 主题活动（写） | `party-order-create` | **有** |
| 积分/商城 | `query-my-account` `mall-points-products` `mall-product-detail` `mall-order-list` `mall-order-detail` | 无 |
| 积分/商城（写） | `mall-create-order` | **有**（扣积分/库存） |
| 抽奖 | `query-lottery-info` `query-my-prizes` | 无 |
| 抽奖（写） | `draw-lottery` | **有**（扣积分/次数，无幂等） |
| 营养 | `list-nutrition-foods` | 无 |

**通用规则**：先 `now-time-info` 或本机时间确认时间窗；写入类工具每次都要单独的明确授权；
参数与字段以实际返回为准，本文只记录已验证的字段。

## 0. 连接方式

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

Token 在麦当劳 MCP 站点控制台「激活」后获取。任何仓库文件都不得写入真实 Token。

## 1. `query-my-account`（只读）

- 入参：无。
- 返回：`data.availablePoint / accumulativePoint / usedPoint / expiredPoint / frozenPoint / currentMouthExpirePoint / lastMouthExpirePoint / nextMouthExpirePoint / currency`。
- **注意拼写**：接口字段是 `currentMouthExpirePoint`（Mouth），不是 Month。
- **能力边界**：只有月度汇总，**没有分批到期明细，也没有具体到期日期**。因此不能输出「X 分将在 10 月 23 日过期」这种句子。

实测样例：

```json
{"success":true,"code":200,"data":{
  "availablePoint":"20.5","accumulativePoint":"34","usedPoint":"0",
  "expiredPoint":"13.5","frozenPoint":"0",
  "currentMouthExpirePoint":"0","lastMouthExpirePoint":"0","nextMouthExpirePoint":"0",
  "currency":"麦享会积分","accountId":"<已脱敏>"}}
```

调用条件：用户问余额/到期/账户情况，或准备抽奖需要核对积分。
不要在人格测试、运势闲聊时调用。

## 2. `query-lottery-info`（只读）

- 入参：无。
- 关键字段：

| 字段 | 含义 | 使用规则 |
|---|---|---|
| `status.code` | `SUCCESS` / `NOT_LOGIN` / `NO_ACTIVITY` / `ACTIVITY_ERROR` / `ACTIVITY_EXPIRED` | 非 SUCCESS 时原样说明，不得假设活动可用 |
| `activityStatusText` | 未开始 / 进行中 / 已结束 / 活动不存在 | 非「进行中」不得引导抽奖 |
| `drawPoint` | 单次消耗积分 | 仅作参考，实际以 `drawDecision` 为准 |
| `drawTypeText` | 无 / 消耗积分抽奖 / 消耗任务完成次数 / 先消耗次数再消耗积分抽奖 | 仅作说明 |
| `availableTimes` | 剩余次数 | **不能**用 0/null 推断不可抽 |
| `drawDecision.resourceEligible` | 服务端资源判定 | **资源资格判断依据** |
| `drawDecision.nextConsumption` | 本次实际消耗 `{type, points, chances, text}` | 必须展示给用户 |
| `drawDecision.fallbackConsumption` | 次数用完后的兜底消耗 | 非空时必须说明 |
| `prizes[]` | `{name, imageUrl, typeText}` | **没有中奖概率**，不得编造 |

实测样例（当前真实状态：积分不足）：

```json
{"data":{"status":{"code":"SUCCESS","message":"成功"},
 "activityCode":"dtx2","activityName":"麦麦积分抽奖",
 "activityStatusText":"进行中","beginTime":"2025-01-26 00:00:00","endTime":"2030-12-31 23:59:59",
 "drawPoint":"24","drawTypeText":"消耗积分抽奖","availablePoint":"20.5",
 "drawDecision":{"resourceEligible":false,"reason":"积分不足",
   "nextConsumption":{"type":"POINTS","points":"24","chances":0,"text":"本次将消耗 24 积分"}},
 "prizes":[{"name":"下单立减3元券","typeText":"优惠券"},{"name":"30积分","typeText":"积分奖"}]}}
```

## 3. `draw-lottery`（有副作用）

- 入参：**无**。调用即对当前活动执行**一次**抽奖，服务端原子完成扣减。
- 频控：短时间重复调用返回「操作太频繁，请稍后再试」，且**不扣积分**。
- 返回：`status{code,message}`、`win`、`prizes[{name,imageUrl,typeText,validDateInfo}]`。
- 无幂等键，因此**重试 = 可能重复扣分**。

调用前置（缺一不可）：

1. 已展示 `drawDecision.nextConsumption` 的文案；
2. `resourceEligible` 为 `true`；
3. 用户对「这个活动 + 这个次数 + 这个成本」明确确认；
4. 本次调用的次数已计入授权额度（守卫 `begin` 成功）。

禁止：试抽、自动补抽、失败后静默重试、把「看看能不能中」当授权。

## 4. `query-my-prizes`（只读）

- 入参：`pageNum`（默认 1）、`pageSize`（默认 10，最大 50），均为字符串。
- 返回：`prizes[{id,name,prizeTypeText,recordTime,status,statusText,timeRemindText}]`、`hasMore`、`truncated`。
- 用途：抽奖后核实、查看历史、辅助核实「状态未知」那笔，不能仅凭无新增奖品认定未执行。
- 规则：接口没给核销期限就不要补；`timeRemindText` 为空即无提醒文案。

## 5. `mall-points-products`（只读）

- 入参：`catRuleIds`（可选，逗号分隔）；常用 `1>4` 商品券、`2>8` 周边、`1>6>40` 积分兑换活动。
- 返回每条：`spuName / spuId / point / price / selling / upTime / downTime / catName / status / shopId`。
- `status`：1 仓库中 / 2 上架 / 3 售罄 / 4 下架 / 5 预热。

**两个真实坑位**（实测列表里都存在）：

1. `point = "0"` 且 `price > 0` → 付费活动（生日派对、读书会等），**不是 0 积分兑换**；
2. `status = 2` 但 `downTime` 已过 → 列表里仍在，但**实际不可兑换**。

筛选必须同时满足：`status == 2` 且 `point > 0` 且 `upTime <= now <= downTime`。

## 6. `mall-product-detail` / `query-my-coupons`（只读，按需）

- 商品详情需要 `spuId`（来自商品列表），返回 SKU，用户选规格后才能下单。
- 卡包券不校验门店/渠道，**不能**承诺「这单一定能用」；要判断当前可用性应查门店维度券接口。

## 7. 标准调用时序

### 7.1 积分查询

```text
query-my-account → 展示余额 + 本月/下月将过期 + 声明无具体到期日
```

### 7.2 积分体检

```text
query-my-account ─┐
                  ├→ 筛兑换候选 → 按问题给建议
mall-points-products ─┘
（用户要求比较抽奖时才追加 query-lottery-info）
```

### 7.3 真实抽奖（带守卫）

```text
query-my-account ─┐
                  ├→ 成本与限制说明 → 用户明确确认
query-lottery-info ┘
        ↓
propose(intent) → authorize(explicit) → begin(attempt)
        ↓
draw-lottery（一次）
        ↓
settle
   ├─ SUCCESS → 展示奖品（仅接口字段）
   ├─ FAILED  → 原样展示 message
   └─ UNKNOWN → 冻结 → query-my-prizes 对账 → 不允许重试
```

守卫命令：

```bash
mclottery guard propose --activity-code dtx2 --activity-name 麦麦积分抽奖 --draws 1 --cost 24 --preflight-ok
mclottery guard authorize --intent-id intent_xxx --explicit
mclottery guard begin     --auth-id auth_xxx
# ... 调用 draw-lottery 一次 ...
mclottery guard settle    --attempt-id draw_xxx --result draw_result.json
mclottery guard status
```

## 8. 错误分类

| 类型 | 表现 | 处理 |
|---|---|---|
| 参数错误 | 缺 spuId、pageSize 超限 | 修正参数重试一次 |
| 认证失败 | `NOT_LOGIN`、401 | 提示用户重新授权连接器，不重试 |
| 业务失败 | `ACTIVITY_EXPIRED`、库存不足、操作太频繁 | 原样展示 message，不自行改写原因 |
| 超时/断连 | 无返回或 5xx | **只读可重试一次；抽奖禁止重试**，先对账 |
| 字段缺失 | 没有 `drawDecision`、没有 `prizes` | 如实说明缺失，不做推断 |

## 守卫运行与未知状态恢复

先按 README 安装完整计算包。默认台账固定为用户目录 `~/.mclottery/ledger.jsonl`，所有本机调用共享；旧台账未结算时不能换空文件继续。守卫不可用则不执行真实抽奖。单次本地台账事务加锁、刷新并持久化；重复意图不能增加授权额度，损坏记录阻断。该保护不覆盖绕过守卫的 MCP 调用或其他设备。

`query-my-prizes` 仅辅助核实。无新增奖品不能证明未执行，余额变化也不能单独关联原请求。仍无法确认时保持 UNKNOWN。只有拿到可关联原请求的官方结果或人工确认，才可执行 `mclottery guard reconcile --attempt-id <id> --status FAILED --verified --note <不含敏感信息的核实依据>`。verified 是调用方声明，不是程序自动验证；不能用它替代核实。

## 9. 点餐与外送域（**字段待联调确认**）

> 说明：以下工具来自官方 MCP 工具列表，**截至 2026-10 尚未逐字段实测**。
> 表中列的是「调用顺序 + 必带参数 + 需要核对的返回项」，不是最终字段名。
> 首次联调后请把真实返回补进本节，并删除本提示。

| 工具 | 必带参数 | 需要核对 | 失败处理 |
|---|---|---|---|
| `query-nearby-stores` | 地址或经纬度 | 门店名/SID/距离/营业状态 | 无结果就让用户换地址，不猜门店 |
| `delivery-query-addresses` | 无 | 地址 ID、标签、是否默认 | 空列表 → 走新增地址 |
| `delivery-create-address` | 收货人、电话、地址 | 返回的地址 ID | 先向用户复述再提交；不写进日志 |
| `delivery-query-stores` | 地址 ID / 坐标 | 可配送门店、起送价、配送范围 | 超出范围如实说 |
| `query-meals` | storeId | 分类、mealCode、名称、价格、时段 | 菜单为空 → 换店或换时段 |
| `query-meal-detail` | mealCode | 套餐组成、可替换项、加价 | 规格不明 → 先问用户 |
| `query-meal-assistance` | storeId | 是否支持团餐、最低份数 | 不支持就换店 |
| `calculate-price` | storeId、取餐方式、商品列表（+券） | 小计、配送费、优惠、应付 | **下单前必调**；失败不下单 |
| `create-order` | 门店、取餐方式、商品、联系方式 | 订单号、状态、实付、支付链接 | 超时 → 查订单列表，**禁止重试** |
| `query-order` | 订单号 | 状态、商品、金额、配送信息 | 查不到 → 让用户去官方端核对 |
| `order-list` | 分页 | 最近订单、时间、状态 | 用于核实「刚才那单成没成」 |
| `cancel-order` | 订单号 | 取消结果、退款说明 | 失败原样转述，不承诺退款时效 |
| `now-time-info` | 无 | 当前时间 | 用于活动/营业时间判断 |

点餐调用链（依赖严格串行）：

```text
query-nearby-stores / delivery-query-stores
  → query-meals
  → query-meal-detail（按需）
  → calculate-price
  → create-order
  → query-order / order-list
  → cancel-order（按需）
```

## 10. 优惠与活动域（**部分字段待联调确认**）

| 工具 | 必带参数 | 需要核对 | 规则 |
|---|---|---|---|
| `available-coupons` | 无 | 券名、门槛、面额、有效期 | 可领 ≠ 可用 |
| `auto-bind-coupons` | 无 | 领取结果、失败项 | **有副作用**，先问用户 |
| `query-my-coupons` | 分页 | 卡包券、状态、有效期 | 不校验门店，不做可用承诺 |
| `query-store-coupons` | storeId | 门店可用券 | **判断可用性的唯一依据** |
| `campaign-calendar` | 月份（如支持） | 进行中/往期/未来活动、时间窗 | 只推进行中与即将开始 |
| `query-party-city` | 无 | 可参与城市 | 四级链第一步 |
| `query-party-store` | 城市 | 可参与门店 | 第二步 |
| `query-partystore-date` | 门店 | 可预约日期 | 第三步 |
| `query-partystore-session` | 门店 + 日期 | 可预约场次、余位 | 第四步；空即无位 |
| `party-order-create` | 城市/门店/日期/场次/人数 | 订单与支付信息 | **有副作用**，确认后下 |

## 11. 营养域

| 工具 | 必带参数 | 返回要点 | 用途 |
|---|---|---|---|
| `list-nutrition-foods` | 无 | 餐品名、能量、蛋白质、脂肪、碳水、钠、钙 | 热量组合、单餐评估 |

- 份量口径以返回为准；缺热量字段的餐品**不参与组合**，但要向用户说明。
- 营养建议不构成医疗、减重或疾病饮食建议。

## 12. 新域联调清单

首次联调时按顺序验证并补文档：

- [ ] 门店接口：地址/坐标两种入参是否都支持，返回里有没有营业状态
- [ ] 菜单接口：是否必须传 storeId，是否分时段
- [ ] 算价接口：券参数是 couponId 还是券对象，配送费字段名
- [ ] 下单接口：是否有幂等键/请求号，支付链接字段名与有效期
- [ ] 订单查询：状态字段的取值枚举
- [ ] 取消接口：已支付订单的返回文案
- [ ] 券接口：门槛与面额字段名，是否支持叠加
- [ ] 活动接口：月份参数格式
- [ ] 营养接口：热量单位是 kcal 还是 kJ（**这个必须确认，差 4.18 倍**）


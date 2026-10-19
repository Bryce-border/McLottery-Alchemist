# MCP 工具工作流

> 实测时间：2026-10-19，麦当劳 MCP Server `https://mcp.mcd.cn`（streamable HTTP）。
> 字段名来自真实返回；示例已脱敏（账户 ID 替换、金额保留）。

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

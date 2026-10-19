# 积分计算规则与边界

> 原则：确定性计算交给 `src/mclottery`，模型只负责解释。任何公式在数据不满足条件时
> **必须返回「无法计算」而不是近似值**。

## 1. 剩余积分

```text
total_cost      = cost_per_draw × draw_count
remaining_points = available_points − total_cost
```

- 只有在成本、次数、可用余额三者都明确且口径一致（同一活动、同一账户）时才计算。
- 结果是**预计值**，不代表工具执行后的真实余额；真实扣减以 `draw-lottery` 的返回为准。
- 余额不足时差额可以算（`available − needed`），但只能用于解释，不能推导「再攒多久」。

代码：`lottery.total_cost` / `lottery.calculate_remaining_points`

## 2. 单位积分价值

```text
value_per_point = comparable_product_value ÷ points_required
```

单位：元/积分（参考价值为人民币时）。

前置条件（缺一即放弃计算）：

1. 商品参考价值可信且口径一致；
2. `points_required > 0`；
3. 商品当前真正可兑换（`status=2` 且在 `upTime ~ downTime` 内）。

**本项目的默认状态**：麦当劳 MCP 不提供商品参考价值。商品名里的「10.9 元」是券面价，
`price` 字段是「所需金额」（付费活动用），都不能直接当商品价值。因此默认
`value_basis = "none"`，只做定性比较，**不输出「性价比第一名」**。

代码：`value.calculate_unit_value` / `value.compare_redemption_options`

## 3. 抽奖期望价值

```text
expected_value = Σ(prize_value[i] × probability[i])
expected_net   = expected_value − cost_per_draw 的可比价值
```

必须全部满足才允许出数字：

- 每个结果都有真实概率（来自接口，不能估）；
- 每个结果都有可比价值；
- 概率之和 ≈ 1（容差 0.001），即结果空间完整；
- 概率在 0~1 区间。

否则返回状态之一：`PROBABILITY_MISSING` / `PROBABILITY_INCOMPLETE` / `VALUE_MISSING`，
并明确说「无法给出期望值」。

**当前真实情况**：`query-lottery-info` 只返回奖品名称与类型，**没有概率**。
所以本 Skill 在实际对话中永远不做数值期望计算，只说明「抽奖是概率事件，成本确定、收益不确定」。

代码：`lottery.expected_value`

## 4. 积分到期分类

数据源只有：`currentMouthExpirePoint`（本月将过期）、`nextMouthExpirePoint`（下月将过期）、
`expiredPoint`（已过期）。**没有批次、没有具体日期。**

规则：

| 分类 | 判定 | 表达要求 |
|---|---|---|
| 已过期 | `expiredPoint > 0` | 只能说明「已过期、不可用」 |
| 本月将过期 | `currentMouthExpirePoint > 0` | 必须注明「接口只给月度汇总，具体日期未返回」 |
| 下月将过期 | `nextMouthExpirePoint > 0` | 同上 |
| 近端提醒 | 到期估算日距今天 ≤ `near_days`（默认 7） | 必须注明这是**产品侧阈值** |

不得把「7 天 / 30 天」说成麦当劳官方政策；不得编造「10 月 23 日到期 50 分」。

代码：`expiry.classify_expiring_points`（`ExpiryPolicy` 可配置阈值）

## 5. 商城可兑换判定

```text
可兑换 ⇔ status == 2
        ∧ point > 0
        ∧ upTime ≤ now ≤ downTime
```

排除原因必须能说清：

| 情况 | 原因文案 |
|---|---|
| `point == 0` 且 `price > 0` | 非积分兑换商品（需付费 X 元） |
| `status == 3` | 已售罄 |
| `status == 4` | 已下架 |
| `status == 5` | 预热中，尚未开放兑换 |
| `now < upTime` | 兑换未开始 |
| `now > downTime` | 兑换已截止（即便 status 仍是 2） |
| `point` 缺失 | 接口未返回所需积分 |

代码：`models.Product.availability`

## 6. 抽奖前置校验

```text
可抽 ⇔ status.code == "SUCCESS"
      ∧ activityStatusText == "进行中"
      ∧ beginTime ≤ now ≤ endTime
      ∧ drawDecision.resourceEligible == true
      ∧ available ≥ cost_per_draw × draws
```

任一不满足即阻断，并把原因原样告诉用户。`drawDecision` 缺失时**阻断**，不假设可抽。

代码：`lottery.evaluate_activity`

## 7. 计算示例（真实数据）

账户：`availablePoint = 20.5`；活动：`drawPoint = 24`，`resourceEligible = false`，`reason = 积分不足`。

```text
total_cost(1)     = 24
remaining         = 20.5 − 24 = −3.5   → 说明差额 3.5
conclusion        = 不可抽奖（服务端已判定资源不足）
```

输出应为：「这次抽不了：单次 24 积分，你现在 20.5 积分，差 3.5 分。」——不能出现「差不多够了」。

## 8. 禁止事项

- 禁止把积分折算成人民币再谈「亏赚」（除非另有可信汇率来源，本项目不提供）。
- 禁止用「抽了几次没中，下次概率更高」这类说法（独立事件，且无概率数据）。
- 禁止在数据缺失时给出「大概」「约等于」的数字。
- 禁止把「预计剩余」写成「执行后余额」。

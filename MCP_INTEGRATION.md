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

## 2. 使用的工具（33 个）

> 标注「已实测」的工具字段来自 2026-10-19 的真实联调；标注「待联调」的工具来自官方工具清单，
> 字段名以实际返回为准，首次联调后补实测记录。

### 2.1 点餐与外送（12）

| 工具 | 作用 | 副作用 | 联调 |
|---|---|---|---|
| `query-nearby-stores` | 附近门店 | 无 | 待联调 |
| `delivery-query-addresses` | 收货地址列表 | 无 | 待联调 |
| `delivery-create-address` | 新增收货地址 | **写入地址** | 待联调 |
| `delivery-query-stores` | 可配送门店 | 无 | 待联调 |
| `query-meals` | 当前门店菜单 | 无 | 待联调 |
| `query-meal-detail` | 套餐组成/可替换项 | 无 | 待联调 |
| `query-meal-assistance` | 企业团餐助餐服务 | 无 | 待联调 |
| `calculate-price` | 算价（含优惠、配送费） | 无 | 待联调 |
| `create-order` | 创建订单 | **生成订单与支付链接** | 待联调 |
| `query-order` | 订单详情/进度 | 无 | 待联调 |
| `order-list` | 历史订单 | 无 | 待联调 |
| `cancel-order` | 取消订单 | **改变订单状态** | 待联调 |

### 2.2 优惠与活动（10）

| 工具 | 作用 | 副作用 | 联调 |
|---|---|---|---|
| `available-coupons` | 麦麦省可领券 | 无 | 待联调 |
| `auto-bind-coupons` | 一键领券 | **写入券包** | 待联调 |
| `query-my-coupons` | 卡包券（不校验门店） | 无 | 已实测 |
| `query-store-coupons` | 当前门店可用券 | 无 | 待联调 |
| `campaign-calendar` | 当月活动日历 | 无 | 待联调 |
| `query-party-city` | 主题活动城市 | 无 | 待联调 |
| `query-party-store` | 主题活动门店 | 无 | 待联调 |
| `query-partystore-date` | 可预约日期 | 无 | 待联调 |
| `query-partystore-session` | 可预约场次 | 无 | 待联调 |
| `party-order-create` | 主题活动下单 | **生成订单** | 待联调 |

### 2.3 积分、商城与抽奖（11）

| 工具 | 作用 | 副作用 | 联调 |
|---|---|---|---|
| `query-my-account` | 积分账户与月度到期 | 无 | 已实测 |
| `mall-points-products` | 商城列表 | 无 | 已实测 |
| `mall-product-detail` | 商城商品详情 | 无 | 待联调 |
| `mall-create-order` | 积分兑换下单 | **扣积分/库存** | 待联调 |
| `mall-order-list` | 商城订单列表 | 无 | 待联调 |
| `mall-order-detail` | 商城订单详情 | 无 | 待联调 |
| `query-lottery-info` | 抽奖活动与 `drawDecision` | 无 | 已实测 |
| `draw-lottery` | 抽奖一次 | **扣积分/次数，无幂等** | 契约 + 模拟 |
| `query-my-prizes` | 中奖记录 | 无 | 已实测 |
| `now-time-info` | 当前时间 | 无 | 待联调 |
| `list-nutrition-foods` | 餐品营养 | 无 | 待联调 |

> 说明：早期版本只接入了积分与抽奖域；本次扩展把点餐、优惠、活动、营养域一并纳入，
> 因此不再存在「未申请调用权限」的工具。写入类工具一律走「确认 → 授权 → 单次执行」。

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
                                 └─ 优先到期与兑换建议；用户要求比较时再查活动
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

### 3.4 点餐与外送（新增）

```text
用户：「我要吃麦当劳 / 帮我点个套餐」
  │
  ├─ 到店：query-nearby-stores ──▶ 用户选店
  └─ 外送：delivery-query-addresses ──▶（无地址时）delivery-create-address
                                    └─▶ delivery-query-stores
  │
  ▼
query-meals(storeId) ──▶ query-meal-detail(mealCode)（按需）
  │
  ▼
query-store-coupons(storeId) + query-my-coupons() ──▶ 本地试算最省券
  │
  ▼
calculate-price ──▶ 本地复核：小计 + 配送费 − 优惠 = 应付
  │                     └─ 差异 > 0.01 元 → 阻断
  ▼
六项确认卡（门店/方式/商品/优惠/配送费/实付）──▶ 用户明确确认
  │
  ▼
create-order（一次）──▶ 订单号 + 支付链接（只展示，不代付）
  │
  └─ 超时/断连 ──▶ order-list / query-order 查证（禁止重试下单）
```

### 3.5 订单状态与取消（新增）

```text
query-order(orderId) ──▶ 状态解读
  ├─ 待支付   → 可取消，或去支付
  ├─ 已支付   → 取消通常走退款，先说明
  ├─ 制作中   → 可能被门店拒绝
  ├─ 配送中   → 一般不可取消
  └─ 未知     → 原样展示，不推断

用户明确「取消」──▶ cancel-order ──▶ 再查一次确认 ──▶ 反馈结果
```

### 3.6 营养与热量（新增）

```text
list-nutrition-foods ──▶ 解析（缺热量的餐品不参与计算）
  │
  ├─ 用户给了目标热量 ──▶ 本地组合 1–3 件，选最接近且优先不过量
  └─ 没给目标         ──▶ 给单品数据，或只问一句目标
```

边界：不做医疗/减重建议；份量口径以接口为准；热量单位需联调确认（kcal vs kJ）。

### 3.7 积分缺口计划（新增）

```text
query-my-account ──▶ 当前积分、到期汇总
mall-points-products / query-lottery-info ──▶ 目标所需积分、截止时间
  │
  ├─ 问清：频率、预算区间、截止时间
  ├─ 积分获取速度：只认官方规则或用户输入
  ▼
本地 mealplan ──▶ 缺口 / 所需次数 / 预计周期 / 预计总支出 / 替代路径
```

### 3.8 活动与福利（新增）

```text
campaign-calendar ──▶ 进行中 / 往期 / 未来（只推前两类）
available-coupons ──▶ 询问 ──▶ auto-bind-coupons
主题活动：query-party-city → store → date → session → party-order-create
```

## 4. 关键集成约束（实现依据）

1. **`drawDecision` 是资源资格判断依据，执行还需活动有效、成本明确、授权与守卫放行。** `availableTimes = 0` 或 `drawTypeText` 都不构成判断依据。
2. **`draw-lottery` 无入参。** 「次数」只能通过多次串行调用实现，每次调用前刷新消耗并核验授权；同范围已有授权不重复询问。
3. **无幂等键。** 超时/断连后禁止重试，必须先查 `query-my-prizes` 对账。
4. **频控。** 短时间重复调用返回「操作太频繁」，服务端不扣积分，属于明确失败。
5. **积分是字符串小数**（如 `"20.5"`），计算层用 `Decimal` 处理，避免浮点误差。
6. **到期只有月度汇总**，字段拼写为 `Mouth`（`currentMouthExpirePoint`）。
7. **商城存在两个陷阱**：`point="0"` 的付费活动、`status=2` 但已过 `downTime` 的商品。
8. **奖池无概率字段**，因此不做期望值计算。
9. **`create-order` 无幂等键。** 下单超时后禁止重试，必须先查 `order-list`；本地只做购物车指纹提示。
10. **券可用性分两层。** `query-my-coupons` 是卡包券，不校验门店；`query-store-coupons` 才是门店可用性依据，接口未明确支持叠加时只算单券。
11. **营养接口不保证份量口径。** 热量单位需联调确认（kcal / kJ 差 4.18 倍），缺热量的餐品不参与组合。
12. **积分获取速度不可得。** 接口不返回「消费 1 元得多少分」，积分计划不得自行估算到账速度。

## 5. 业务价值

**对用户**

- 少浪费：临期积分优先提醒可兑换项，而不是等过期。
- 少花冤枉钱：先领券、再按门店可用券算这一单实付，且**不为凑门槛加购**。
- 少误操作：抽奖/下单前把「多少分、多少钱、几次」摊开讲，避免「点了没反应」。
- 少踩坑：把「0 积分」的付费活动和已过期商品明确排除，并说明原因。
- 点餐更顺：门店 → 菜单 → 算价 → 确认卡一条链，规格与金额先对齐再下单。
- 热量可管理：按目标热量组合一餐，知道这一顿大概多少卡、蛋白质和钠多少。
- 目标可执行：积分不够时给的是「还要几次、几周、总共花多少」，而不是一句「多来几次」。
- 可追溯：抽奖有本地台账；下单/取消有关键状态核对。

**对平台**

- 提升积分资产的**有效使用率**：临期提醒 + 可兑换项筛选，推动积分在有效期内被用掉。
- 提升点餐转化与客单透明度：券后实付先讲清楚，减少下单页流失与售后争议。
- 降低客服成本：把「为什么抽不了」「为什么扣了分」「订单到哪了」在对话里解释清楚。
- 降低重复扣分/重复下单风险：冻结/对账 + 指纹提示，弥补客户端缺少幂等键的空档。
- 提升活动参与率：把当月活动与可领券按用户偏好推送，而不是堆一张活动列表。
- 提升抽奖体验的**信任感**：不承诺中奖、不夸大收益，反而让活动更可信。

## 6. 数据时效

| 数据 | 时效处理 |
|---|---|
| 积分余额 | 每次操作前实时查询，不复用旧值 |
| 活动状态 / 消耗规则 | 抽奖前重新查询，不使用缓存 |
| 商城商品 | 每次查询时按 `upTime/downTime` 与 `status` 重新校验 |
| 中奖记录 | 每次查询实时拉取，按 `recordTime` 倒序 |
| 到期汇总 | 视为「截至查询时刻」的月度汇总，不推断未来 |
| 门店 / 菜单 | 每次点餐现查，不使用上一次的菜单缓存 |
| 价格 / 配送费 | 每单以最新 `calculate-price` 为准，本地只做复核 |
| 门店券 | 每次进入用券环节重新查 `query-store-coupons` |
| 活动日历 | 每次推荐时现查，过去活动不重复推送 |
| 订单状态 | 取消/查询前重新拉取，不用旧状态判断能否取消 |

## 7. 失败处理

| 情况 | 处理 |
|---|---|
| 未登录 / `NOT_LOGIN` | 阻断，提示重新授权连接器，不重试 |
| 活动不存在 / 已结束 | 阻断，原样说明状态 |
| 积分不足 | 阻断，给出差额（数据可算时） |
| 操作太频繁 | 视为明确失败，**不重试** |
| 抽奖超时 / 无状态码 | 记为 UNKNOWN，冻结后续抽奖，先对账 |
| 下单超时 / 断连 | 标记「下单结果未知」，先查 `order-list`，**禁止重复提交** |
| 商品售罄 / 门店不可用 | 让用户换品/换店，不用相似商品替代 |
| 本地算价与接口不一致 | 阻断下单，先查规格、券、配送费差异 |
| 券不可用 / 门槛未知 | 标「待确认」，不静默去券、不承诺可用 |
| 营养字段缺失 | 该餐品不参与组合，但要列出「未算进来」的名单 |
| 字段缺失 | 如实说明缺失，不做推断 |
| 只读查询网络失败 | 允许重试一次，仍失败则说明并给替代动作 |

## 8. 联调记录

- 2026-10-19：`query-my-account` 返回 `availablePoint="20.5"`、`expiredPoint="13.5"`，无分批到期字段。
- 2026-10-19：`query-lottery-info` 返回活动 `dtx2`「麦麦积分抽奖」，`drawPoint="24"`，`drawDecision.resourceEligible=false`，`reason="积分不足"`，奖池 10 项、无概率。
- 2026-10-19：`mall-points-products` 返回 50+ 条，其中含 `point="0"` 的付费活动与 `downTime` 已过期的商品，均已纳入过滤规则与测试 fixture。
- 2026-10-19：`query-my-prizes` 返回 1 条历史奖品（`statusText="可用"`、无 `timeRemindText`）。
- 未执行真实抽奖（当时余额不足以参与），`draw-lottery` 的行为依据工具契约文档与模拟返回（`tests/fixtures/draw_*.json`）验证。
- 2026-10-21：按官方工具清单扩展点餐、优惠、活动、营养域。新增域尚未逐字段联调，契约以工具清单与 `references/tool-workflows.md` 的「待联调」标注为准；计算层（热量组合、券试算、积分计划、订单复核）已用 55 个新增单测覆盖，不依赖网络。

## 守卫运行与未知状态恢复

先按 README 安装完整计算包。默认台账固定为用户目录 `~/.mclottery/ledger.jsonl`，所有本机调用共享；旧台账未结算时不能换空文件继续。守卫不可用则不执行真实抽奖。单次本地台账事务加锁、刷新并持久化；重复意图不能增加授权额度，损坏记录阻断。该保护不覆盖绕过守卫的 MCP 调用或其他设备。

`query-my-prizes` 仅辅助核实。无新增奖品不能证明未执行，余额变化也不能单独关联原请求。仍无法确认时保持 UNKNOWN。只有拿到可关联原请求的官方结果或人工确认，才可执行 `mclottery guard reconcile --attempt-id <id> --status FAILED --verified --note <不含敏感信息的核实依据>`。verified 是调用方声明，不是程序自动验证；不能用它替代核实。

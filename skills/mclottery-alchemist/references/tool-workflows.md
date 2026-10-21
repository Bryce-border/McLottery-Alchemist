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
- 频控：**1 秒内不可重复调用**，重复调用返回「操作太频繁，请稍后再试」，且**不扣积分**。
- 返回：`status{code,message}`、`win`、`prizes[{name,imageUrl,typeText,validDateInfo}]`、
  `consumePoint`（本次实扣）、`remainPoint`（剩余积分）、`remainChance`（剩余次数）。
  - `consumePoint` / `remainPoint` 是**服务端实际值**，可用于对账；不要用本地预估替代。
  - 失败时 `status.code` 可能是 `DRAW_FAILED`，`success` 仍为 `true`，必须看 `status.code`。
- 无幂等键，因此**重试 = 可能重复扣分**。

调用前置（缺一不可）：

1. 已展示 `drawDecision.nextConsumption` 的文案；
2. `resourceEligible` 为 `true`；
3. 用户对「这个活动 + 这个次数 + 这个成本」明确确认；
4. 本次调用的次数已计入授权额度（守卫 `begin` 成功）。

禁止：试抽、自动补抽、失败后静默重试、把「看看能不能中」当授权。

## 4. `query-my-prizes`（只读）

- 入参：`pageNum`（默认 1）、`pageSize`（默认 10，**最大 20**），均为整数。
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

## 5.1 `mall-create-order`（有副作用｜2026-10-21 官方文档确认）

### 入参

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `skuId` | Long | **是** | 商品具体规格 ID，来自商品详情，不是 spuId |
| `count` | Integer | 否 | 默认 1 |
| `addressId` | String | 实物必填 | `spuCategory="2"` 时缺它无法下单 |
| `spuCategory` | String | **是** | `"1"` 虚拟商品 / `"2"` 实物商品 |

### 返回（真实结构）

```json
{"success":true,"code":200,"traceId":"<脱敏>","data":{
  "orderId":"ECS1211037028709736448",
  "orderStatus":1,"status":1,
  "orderDetailVo":{
    "orderStatusTitle":"待支付",
    "orderStatusSubTitle":"内未支付将自动取消订单",
    "leftPaySecond":899,
    "realTotalPoints":"0","realTotalAmount":"0.01",
    "goodsTotalPoints":"0","goodsTotalPrice":"0.01",
    "payId":"11976540481989423104",
    "createTime":"2026-05-18 17:43:14",
    "allCount":1,"receivedCount":0,
    "goods":[{"spuId":465,"skuId":10863,"spuName":"早餐权益卡","skuName":"30天",
              "count":1,"price":"0.01","points":"0"}]
  },
  "addressVO":{"addressId":"<脱敏>","contactName":"李","phone":"199****1228",
               "fullAddress":"江苏省南京市江宁区东吉大道1号"}
}}
```

### 五个必须讲清楚的点

1. **兑换不等于免费。** 上面是真实示例：`realTotalPoints="0"` + `realTotalAmount="0.01"`。
   只看积分会把它说成「0 积分白拿」。必须同时报**积分 + 现金**。
2. **下单成功 ≠ 兑换成功。** 返回的是 `orderStatusTitle="待支付"`，`leftPaySecond=899`（约 15 分钟），
   超时自动取消。不能对用户说「已经兑好了」。
3. **实物必须有地址。** `spuCategory="2"` 且没有 `addressId`，不要尝试下单，
   先走 `delivery-query-addresses` 选地址或 `delivery-create-address` 新增。
4. **`payId` 不是支付链接。** 它只是支付标识；不要在对话里拼 URL，也不要代付。
5. **接口文案可能截断。** 示例 `orderStatusSubTitle` 是「内未支付将自动取消订单」，缺了前半句；
   原样转述或直接引 `statusTitle`，不要自行补字。

### 本地预检

```bash
python run.py redeem --sku-id 10863 --spu-name 早餐权益卡 --sku-name 30天 \
  --category 1 --points 0 --price 0.01 --available 2350
python run.py redeem-result --input mall_order_created.json
```

`redeem` 拦：缺 skuId、非法 spuCategory、实物缺 addressId、积分不足、所需积分与现金都未知。
`redeem-result` 输出：订单号、状态、**积分 + 现金**、支付倒计时、商品、收货信息。

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

## 9. 点餐与外送域（真实契约，2026-10-21 官方文档）

### 9.1 门店

`query-nearby-stores`（到店）：

| 入参 | 必填 | 说明 |
|---|---|---|
| `searchType` | 是 | **1=搜索收藏餐厅（默认）**、2=按位置搜索、3=按经纬度 |
| `beType` | 是 | 1=到店自取、5=得来速车道 |
| `city` / `keyword` | 仅 searchType=2 | 城市 + 位置关键词 |
| `longitude` / `latitude` | 仅 searchType=3 | 经纬度 |

**坑**：`searchType` 默认是「收藏餐厅」，不是「附近」。用户问「附近有什么店」时必须显式传 2 或 3，否则会拿收藏列表当附近结果。

返回：`storeCode`、`beCode`、`storeName`、`address`、`distance`、`businessStatus`、`businessStartTime`、`businessEndTime`、`reservation`、`reservationTimeOptions[]`。

`delivery-query-stores`（外送）：入参 `addressId`（必填）+ `beType`（2=麦乐送、6=企业团餐）。返回同上，另含预约时段。

`delivery-query-addresses`：无入参，返回 `addresses[{addressId,contactName,phone,fullAddress}]`，手机号已脱敏。

`delivery-create-address`：`city`、`contactName`、`phone`（11 位纯数字）、`address`、`addressDetail` 必填，`gender` 选填。返回新地址对象。

### 9.2 菜单与详情

`query-meals`：`storeCode`、`beCode`、`orderType`（1 到店 / 2 外送）、`beType`、`reservationDate`（预约场景必填）。

返回是**索引 + 字典**，不是数组：

```json
{"frequent":[{"code":"9900008139","tags":[]}],
 "categories":[{"name":"人气热卖","meals":[{"code":"920215","tags":["第二份半价"]}],"daypart":8}],
 "meals":{"920215":{"name":"培根安格斯厚牛堡大套餐","currentPrice":"55.5",
          "originalPrice":"65.5","discountType":"随单购麦金卡优惠",
          "isCanWithOrder":true,"withOrder":{...}}}}
```

- 价格是**元**字符串（`"55.5"`）。
- `discountType` 取值：null / 早餐卡优惠 / 促销优惠 / 麦金卡优惠 / 随单购早餐卡优惠 / 随单购麦金卡优惠。
- 用户要随单购优惠时，必须把 `withOrder` 原样带入算价与下单，否则价格对不上。

`query-meal-detail`：`code`、`storeCode`、`beCode`、`orderType`、`beType`。返回 `price`、`rounds[]`、`modification{items[{maxValues,minValues,values[{code,price,name,maxQuantity,minQuantity,selectedQuantity,selectedKey,unselectedKey}]}]}`、`supportModify`。

### 9.3 算价（**单位：分**）

入参：`storeCode`、`beCode`、`orderType`、`beType`、`reservationDate`、`gmServiceCode`（团餐）、`withOrder`、`needTableware`、`items[]`。

`items[]`：`productCode`、`quantity`、`couponId`、`couponCode`、`modification{values[{code,quantity,key}]}`、`roundList[{round,comboItemList[{code,quantity,modification}]}]`。

返回全部金额**以分为单位**：

```json
{"productOriginalPrice":1600,"productPrice":1600,"deliveryOriginalPrice":600,
 "deliveryPrice":600,"originalPrice":2200,"discount":0,"price":2200,
 "productList":[{"productCode":"...","productName":"...","quantity":1,
                 "originalSubtotal":1600,"subtotal":1600}],
 "takeWayList":[{"code":"locker-in","name":"店内取餐柜"}],
 "mealAssistanceList":[],"tablewarePrice":10}
```

- `2200` = 22.00 元；`tablewarePrice:10` = 0.10 元。
- 到店下单的 `takeWayCode` **只能来自 `takeWayList`**，不能自己编。

### 9.4 下单（**单位：元**）

入参：`storeCode`、`beCode`、`beType`、`orderType` 隐含在 beType、`addressId`（外送必填）、`takeWayCode`（到店必填）、`reservationDate`、`gmServiceCode`（团餐必填）、`withOrder`、`needTableware`、`remark`（≤50 字，外送引导用户填）、`items[]`。

返回：

```json
{"orderId":"1030938730000733964700499858","payId":"11940981078137585664",
 "payH5Url":"https://m-sit.mcdchina.net/mcp/scanToPay?orderId=...",
 "orderDetail":{"orderStatus":"待支付","storeName":"...","storeAddress":"...",
   "orderProductList":[{"productName":"DC套餐测试","quantity":1,"price":"16",
                        "comboItemList":[{"itemName":"...","itemQuantity":1}]}],
   "totalAmount":"22","realTotalAmount":"22","totalDiscountAmount":"0","couponList":[],
   "deliveryInfo":{"deliveryType":"立即送出","deliveryAddress":"...","addressDetail":"...",
                   "mobilePhone":"152****6666","expectDeliveryTime":""},
   "deliveryPrice":"6","productPrice":"16","takeWay":"locker-in",
   "pickupCode":"","lockerCode":"","lockerQrCode":"","remark":""}}
```

三个关键：

1. **有 `payH5Url`**，原样展示给用户去支付，不代付、不改写。
2. 这里的金额是**元**（`"22"` = 22 元），与算价的「分」不同，混用会差 100 倍。
3. `pickupCode` / `lockerCode` 可能为空字符串，空值就是「还没出码」，不能补。

### 9.5 查单与取消

- `query-order`：`orderId` 必填，返回同 `orderDetail` 结构，`orderStatus` 是中文字符串。
- `order-list`：**无入参**，最多返回最近 10 笔；用于核实「刚才那单成没成」。
- `cancel-order`：`orderId` + **`cancelReasonCode` 必填**：
  1 改主意了 / 2 重复下单 / 3 点错了点多了点少了 / 4 地址电话填错了 / 5 送达时间选错了 / -1 其它原因。
  返回 `{cancelResult:true, orderId:""}` —— **返回的 orderId 可能是空字符串**，不能拿它判断取消对象，要用原订单号。

调用链：

```text
query-nearby-stores（searchType=2/3） / delivery-query-stores(addressId, beType)
  → query-meals(storeCode, beCode, orderType, beType)
  → query-meal-detail(按需)
  → calculate-price（分）→ 取 takeWayList 里的 takeWayCode
  → create-order（元）→ payH5Url
  → query-order / order-list
  → cancel-order(orderId + cancelReasonCode)
```

## 10. 优惠与活动域（真实契约）

### 10.1 门店券（唯一能判断「这单能不能用」的来源）

`query-store-coupons` 入参：`storeCode`（必填）、`beCode`、`orderType`（1 到店 / 2 外送）、`beType`（1/2/5/6）、`reservationDate`。

返回：

```json
[{"title":"外送优惠券二次券","couponId":"...","couponCode":"...",
  "tradeDateTime":"2025-04-21 23:23:00-2026-07-01 23:59:59",
  "products":[{"productCode":"...","productName":"甜蜜小食1+1"}]}]
```

**没有门槛、没有面额、没有折扣率。** 只能判断：这张券在该门店/取餐方式下存在，且可能限定某些商品。
「券后实付」只能用 `calculate-price` 传 `couponId` 得到，不能自己算。

### 10.2 麦麦省与卡包（**返回 Markdown，不是 JSON**）

`available-coupons`（无入参）返回：

```text
### 麦麦省优惠券列表：
- 优惠券标题：11.9元麦乐鸡 \
  状态：已领取 \
```

状态三种：`已领取` / `未领取` / `不可领取`。

`auto-bind-coupons`（无入参，有副作用）返回「总计/成功/失败 + 成功券的 couponId、couponCode」的 Markdown。

`query-my-coupons`（无入参）返回：

```text
## 9.9元薯你最甜
- **优惠**: ¥9.9 (用券价格)
- **有效期**: 2025-12-09 00:00-2026-02-12 23:59
- **标签**: 到店专用、外送专用
```

- 券名里的「9.9 元」是**用券价**，不是满减金额。
- 三个接口都不给门槛/面额，别用满减模型硬算。

### 10.3 活动日历（Markdown）

`campaign-calendar`：`specifiedDate`（yyyy-MM-dd，选填；给了就查该日附近共三天，不给查当月）。返回带 emoji 与 `<img>` 的营销文案，按「往期回顾 / 今日」分组。原样展示或摘要，不编时间与规则。

### 10.4 主题活动（四级链 + 下单）

| 工具 | 必带参数 |
|---|---|
| `query-party-city` | `spuId` |
| `query-party-store` | `spuId`、`code`（城市 code）、可选经纬度 |
| `query-partystore-date` | `spuId`、`storeCode` |
| `query-partystore-session` | `spuId`、`storeCode`、`dateStr` |
| `party-order-create` | `spuId`、`skuId`、`storeCode`、`dateStr`、`code`、`partyType`、`id`（场次 ID）、`timeStart`、`timeEnd`、`leftNum`、`count` |

- `session` 返回 `price`（**分为单位，需另行核对**）、`leftNum`、`timeStart/timeEnd`、`partyMin/partyMax`。
- `party-order-create` 返回 `orderId`、`upgPayId`、`amount`（元）、`payH5Url`、`goods[]`。
- 下单前必须把场次 ID、时间、剩余名额一起带上；场次空即无位。

## 11. 营养域（**TOON 格式**）

`list-nutrition-foods` 无入参，返回 `data` 是字符串：

```text
[2]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
  猪柳麦满分,null,1288,308,16,16,24,781,213
  巨无霸,null,2300,550,25,30,40,900,120
```

- 表头 `[N]{列名}`，随后 N 行逗号分隔；`null` 表示缺值。
- **优先用 `energyKcal`**，没有 kcal 才用 `energyKj ÷ 4.184` 并标注「由 kJ 换算」。
- 份量口径以返回为准；缺热量的餐品不参与组合，但要告诉用户哪些没算进来。
- 营养建议不构成医疗、减重或疾病饮食建议。

## 12. 待确认清单（已大幅收窄）

已按官方文档确认：门店、菜单、详情、算价（分）、下单（元 + payH5Url）、取消原因、门店券、麦麦省/卡包 Markdown、活动日历、营养 TOON、商城兑换、主题活动。

仍需实测确认：

- [ ] `party-session.price` 与 `party-order-create.amount` 的单位是否分别为分/元（文档示例 1000 vs 250）
- [ ] `calculate-price` 传 `couponId` 后，`discount` 是否已含券抵扣
- [ ] 算价的 `takeWayList` 是否有默认项（没有就必须让用户选）
- [ ] `query-meals` 的 `daypart` 与当前时间如何对应（是否需要过滤时段）


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


# 点餐全流程（真实契约）

> 依据 2026-10-21 官方工具文档。字段名以实际返回为准，本文固定**顺序、单位与安全边界**。

## 0. 一句话原则

**先看店、再看菜、再算钱、再确认、最后下单。** 任何一步缺失都不往下跳。

## 1. 场景分流

| 场景 | 第一步 | 关键参数 |
|---|---|---|
| 到店自取 | `query-nearby-stores` | `searchType=2/3`（**默认 1 是收藏餐厅，不是附近**）、`beType=1` |
| 得来速 | `query-nearby-stores` | `beType=5` |
| 外送 | `delivery-query-addresses` → `delivery-query-stores` | `addressId` + `beType=2` |
| 企业团餐 | `delivery-query-stores` → `query-meal-assistance` | `beType=6`、`orderType=2` |
| 主题活动 | `query-party-city` → store → date → session | `spuId` 贯穿全程 |

门店标识统一用 **`storeCode` + `beCode`**，不存在 `storeId`。

## 2. 门店

到店（`query-nearby-stores`）：

```text
searchType=2 → city + keyword（按位置）
searchType=3 → longitude + latitude（按经纬度）
searchType=1 → 收藏餐厅（只有用户明确说「我收藏的店」才用）
beType=1 到店自取 / 5 得来速
```

外送（`delivery-query-stores`）：`addressId` + `beType=2`（麦乐送）或 `6`（团餐）。

返回要点：`storeCode`、`beCode`、`storeName`、`address`、`distance`、`businessStatus`、
`businessStartTime/EndTime`、`reservationTimeOptions[]`。

- `businessStatus=false` 不要推荐，不猜「应该还开着」。
- 预约场景要带 `reservationDate`（yyyy-MM-dd HH:mm）。

## 3. 菜单

```text
query-meals(storeCode, beCode, orderType, beType, reservationDate?)
```

`orderType`：1 到店 / 2 外送。返回是**索引 + 字典**：

```text
frequent[]         → 常点餐品 code
categories[]       → {name, meals:[{code,tags}], daypart}
meals{code: {...}} → {name, currentPrice, originalPrice, discountType,
                       isCanWithOrder, withOrder}
```

- 价格是**元**（`"55.5"`）。
- `discountType` 非空说明已有优惠；`isCanWithOrder=true` 时 `withOrder` 要原样带入算价和下单。
- 不要把 `categories` 当餐品数组，真正的详情在 `meals` 字典。

详情（`query-meal-detail`）：`code`、`storeCode`、`beCode`、`orderType`、`beType`。
返回 `rounds[]`（套餐组成）、`modification.items[].values[]`（加料/去料与数量）、`supportModify`。

- 用户改了套餐内商品或特调，必须把 `modification` 与 `roundList` 带进算价，否则金额对不上。

## 4. 算价（单位：分）

```text
calculate-price(storeCode, beCode, orderType, beType, items[],
                couponId?, couponCode?, withOrder?, needTableware?)
```

`items[]`：`productCode`、`quantity`、`couponId`、`couponCode`、`modification`、`roundList`。

返回（**全部是分**）：

| 字段 | 含义 | 示例 |
|---|---|---|
| `productOriginalPrice` | 商品原价 | 1600 = 16 元 |
| `productPrice` | 商品现价 | 1600 |
| `deliveryOriginalPrice` / `deliveryPrice` | 配送费 | 600 = 6 元 |
| `originalPrice` | 优惠前总额 | 2200 |
| `discount` | 优惠金额 | 0 |
| `price` | **应付** | 2200 |
| `tablewarePrice` | 餐具费 | 10 = 0.10 元 |
| `takeWayList` | 到店取餐方式选项 | 下单的 `takeWayCode` 只能来自这里 |

本地复核：

```text
本地小计（元） + 配送费 − 优惠 = 接口 price ÷ 100
|差异| > 0.01 元 → 阻断，先查规格、券、随单购
```

命令行：

```bash
python run.py order --input cart.json --store-name "门店名" --meal-type 到店自取 \
  --remote-total 22 --delivery-fee 6 --discount 0
```

> 若直接把算价返回的 `price`（分）喂给 `--remote-total`，要加 `--unit fen`。

## 5. 下单（单位：元）

```text
create-order(storeCode, beCode, beType, addressId? 或 takeWayCode?,
             reservationDate?, gmServiceCode?, withOrder?, needTableware?,
             remark?, items[])
```

- 外送：`addressId` 必填；`remark` 可引导用户填（≤50 字，如「无接触配送」）。
- 到店：`takeWayCode` 必填，**只能来自算价的 `takeWayList`**。
- 团餐：`gmServiceCode` 必填。

返回里三个必须用的字段：

| 字段 | 用途 |
|---|---|
| `orderId` | 查单、取消的唯一标识 |
| `payH5Url` | 支付链接，**原样给用户**，不代付、不改写 |
| `orderDetail.orderStatus` | 示例「待支付」；`pickupCode` / `lockerCode` / `lockerQrCode` 可能为空 |

**金额单位是元**（`totalAmount:"22"` = 22 元），不是分。`realTotalAmount` 才是实付。

## 6. 确认卡（六项齐全）

```text
【下单确认】
门店：<storeName>（<storeCode>）
取餐方式：到店自取 / 外送 / 得来速
商品：巨无霸 ×1 25.5 元｜中薯条 ×2 12 元 / 份
优惠：满 50 减 10（-10 元）
配送费：0 元
实付：48.5 元（calculate-price 的 price ÷ 100）
```

缺一项 → 不要问「确认吗」。金额只认 `calculate-price`，本地只做复核。

## 7. 执行与结果未知

- `create-order` 有副作用：**一次确认只下一次单**。
- 返回后展示：订单号、状态、实付、`payH5Url`、取餐码（有就给，空就如实说还没出码）。
- 不代付、不索取验证码、不构造支付链接。

结果未知：

```text
禁止：直接重试 create-order
正确：order-list（无入参，最近 10 笔）→ 找到刚创建的订单 → 给订单号
      找不到 → 才可重新发起（仍需用户确认）
      查询也失败 → 标记「下单结果未知」，让用户去官方 App 核对
```

## 8. 查单与取消

- `query-order(orderId)`：返回 `orderStatus` 中文状态、商品、金额、配送信息。
- `order-list()`：**无入参**，最多最近 10 笔。

取消：

```text
① query-order 看状态（已支付/制作中/配送中要说明限制与退款）
② 用户明确「取消」
③ cancel-order(orderId, cancelReasonCode)
   reasonCode：1 改主意了 / 2 重复下单 / 3 点错了点多了点少了 /
               4 地址电话填错了 / 5 送达时间选错了 / -1 其它
④ 看 cancelResult；true 才算成功
```

坑：

- 返回示例里 `orderId` 是**空字符串**，不能拿返回的 orderId 判断对象，要用原订单号。
- 不承诺退款到账时间。
- 取消失败时原样转述原因，不改写成「系统问题」。

## 9. 异常分类

| 类型 | 表现 | 处理 |
|---|---|---|
| 门店不可用 | `businessStatus=false`、超出配送范围 | 换店或改外送 |
| 商品售罄 | 加购或算价不含该品 | 让用户换品，不用相似商品替代 |
| 券不可用 | 门店券列表没有 / 算价未抵扣 | 去掉券重算，不静默改价 |
| 价格不一致 | 本地与接口差 > 0.01 元 | 阻断，查规格/随单购/券 |
| 下单超时 | 无返回 | 禁止重试，先 order-list |
| 取餐码为空 | `pickupCode=""` | 如实说「还没出码」，不编 |

## 10. 自检

- [ ] 门店是用户选的？用的是 `storeCode` 不是 `storeId`？
- [ ] 到店搜索用了 `searchType=2/3`（不是默认的收藏）？
- [ ] 菜单读的是 `meals` 字典，不是 `categories` 数组？
- [ ] 改配/特调带进算价了吗？
- [ ] 算价的「分」换算成「元」了吗？
- [ ] `takeWayCode` 来自 `takeWayList` 吗？
- [ ] 确认卡六项齐吗？
- [ ] 下单后给了 `payH5Url`，但没代付吗？
- [ ] 取消带了合法 `cancelReasonCode` 吗？
- [ ] 结果未知时是先查 `order-list` 而不是重试吗？

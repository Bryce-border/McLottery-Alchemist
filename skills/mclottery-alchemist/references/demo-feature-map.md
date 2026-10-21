# Demo 功能映射与交互边界

本文件把 Skill 既有能力映射到页面控件。页面用于体验与呈现，不替代 Skill 的工具路由、确定性计算或安全守卫。

## 功能映射

交互风格以轻松的麦门卡通实验室为基调，动画（角色浮动、星光、翻签、卡片入场与弹窗）只服务于体验，不代表真实 MCP 执行、资格通过或中奖。页面支持 `prefers-reduced-motion`。

| Skill 能力 | 页面入口 | 页面行为 | 内嵌后交给 Agent 的意图 |
|---|---|---|---|
| 启动工作台 | 炼金总览 | 汇总账户、到期、活动、商品建议 | 宿主收到启动口令后执行只读查询，再推送 `mclottery:state` |
| 账户积分 | 积分体检 / 快捷入口 | 余额、累计/已用、已过期与月度到期汇总 | `query_account` |
| 积分体检与规划 | 积分体检 | 固定成本可视化试算、提示到期与价值边界 | `plan_points` |
| 积分商品列表 | 积分兑换 | 搜索、筛选符合候选规则的商品，显示排除原因 | `query_products` |
| 商品规格详情 | 商品详情弹窗 | 显示当前数据和规格；提醒最终状态需官方确认 | `product_detail` |
| 活动信息 | 抽奖活动 | 展示活动状态、成本、drawDecision 资格与奖池 | `query_lottery` |
| 抽奖前置核验 | 抽奖活动 | 只请求刷新账户/活动和展示成本，不直接抽取 | `lottery_preflight` |
| 实际抽奖 | 不提供直连执行按钮 | 页面没有 `lottery_execute` 直连按钮 | Agent 只有在授权、守卫与再次核验后才能调用 `draw-lottery` |
| 中奖追踪 | 中奖记录 | 展示中奖信息、状态、接口提醒 | `query_prizes` |
| 卡包查询 | 我的卡包 | 显示卡券与可用性限制说明 | `query_coupons` |
| MCti 人格 | MCti 人格 | 逐题、快速、多选、跳题、自选、改答、分享与朋友互动 | 纯前端娱乐流程，不触发 MCP |
| 今日运势 | 今日欧气 | 随机生成纯娱乐麦签，宜/忌与单品灵感 | 纯前端娱乐流程，不触发 MCP |
| 启动介绍 | 炼金总览 | 六项能力清单 + 最近活动播报 + 快捷入口 | 宿主收到口令后只读并行，再推 `mclottery:state` |
| 点餐与外送 | 点餐台 | 选门店/取餐方式、菜单加购、券后实付试算、六项确认卡 | `place_order`（**只是意图**）、`query_orders`、`cancel_order` |
| 活动与福利 | 活动日历 / 福利中心 | 只展示进行中与即将开始；券门槛与券后实付 | `query_campaigns`、`claim_coupons`、`query_store_coupons`、`query_party` |
| 营养与热量 | 营养实验室 | 目标热量输入、1–3 件组合、营养汇总、偏差提示 | 纯前端算术；可选 `query_nutrition` |
| 积分缺口计划 | 积分计划 | 缺口、每次约得、预计周期、预计总支出、逐周进度 | `plan_goal` |

## 页面数据契约

宿主可将真实 MCP 返回映射为下面的结构。字段可以缺失，但缺失字段必须标为不可用，不能用默认示例数据填充。

```json
{
  "type": "mclottery:state",
  "payload": {
    "mode": "live",
    "updatedAt": "2026-10-21T12:00:00+08:00",
    "account": {
      "availablePoint": "工具返回值",
      "accumulativePoint": "工具返回值",
      "usedPoint": "工具返回值",
      "expiredPoint": "工具返回值",
      "currentMouthExpirePoint": "工具返回值",
      "nextMouthExpirePoint": "工具返回值",
      "currency": "工具返回值"
    },
    "activity": {
      "activityName": "工具返回值",
      "activityStatusText": "工具返回值",
      "drawPoint": "工具返回值",
      "drawTypeText": "工具返回值",
      "drawDecision": "保留服务端完整判定结构",
      "prizes": "工具返回的奖池数组"
    },
    "products": [],
    "prizes": [],
    "coupons": [],
    "recommendations": [],
    "stores": [],
    "menu": [],
    "nutrition": [],
    "benefits": [],
    "campaigns": []
  }
}
```

页面接收 `mclottery:state` 后会把 `mode=live` / `cached` 中未传入的字段清空，避免残留 Demo 样例被误标为真实数据。

## 页面意图事件

页面通过 `postMessage` 向父级宿主发送：

```json
{
  "type": "mclottery:agent-intent",
  "intent": "query_products",
  "text": "查询并刷新积分商城商品列表，筛选真实可兑换候选",
  "payload": { "id": "可选的商品 ID" }
}
```

常用意图：`query_account`、`query_products`、`query_lottery`、`query_prizes`、`query_coupons`、`plan_points`、`lottery_preflight`、`product_detail`、`user_message`、`place_order`、`query_orders`、`cancel_order`、`claim_coupons`、`query_store_coupons`、`query_campaigns`、`query_party`、`query_nutrition`、`plan_goal`。

> `place_order` 只是「请求 Agent 进入下单确认流程」，**不是**授权扣款；页面没有直接调用 `create-order` 的能力。

宿主可用下面的事件给页面同步自然语言回复与目标页：

```json
{
  "type": "mclottery:agent-response",
  "text": "Agent 对本次查询的简短解释",
  "view": "products"
}
```

本地 Demo 为便于预览，`postMessage` 目标源使用 `*`，且只传送操作意图，不传 Token 或账户凭据。**生产内嵌应将目标源收敛为可信宿主 Origin，验证消息来源、事件类型、字段类型与长度；不能将这段示例直接视作已经实现的安全通道。**

## 安全和事实边界

1. MCP 凭据只放在用户自己的 Agent 宿主配置中。页面不提供 Token 输入框，不把凭据写入 HTML、URL、LocalStorage、日志或事件 payload。
2. 页面中的抽奖模拟按钮只产生随机演示结果，不触发任何真实操作。真实按钮不能绕过 Agent 的成本说明、用户明确授权和 `mclottery` 守卫。
3. `draw-lottery` 无幂等键，一次调用一次抽奖；超时或状态未知禁止自动重试。
4. `query-lottery-info` 不返回中奖概率，因此不展示概率、期望收益或“性价比”数字。
5. 账户只有月度到期汇总，没有具体到期日。商城列表符合筛选条件只代表候选项，不保证库存、门店适用或兑换成功。
6. 卡包接口不校验门店、渠道与订单适用性。
7. **页面不下单、不支付、不领券、不取消订单**：这些按钮只发送意图，必须回到对话完成确认与授权。
8. 点餐台的金额是按示例菜单做的本地算术，真实金额只认 `calculate-price`。
9. 营养实验室只做算术，不构成医疗、减重或疾病饮食建议。
10. 人格与运势完全属于娱乐互动，不查账户、不改变中奖概率、不构成真实抽奖授权。

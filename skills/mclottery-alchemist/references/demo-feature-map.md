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
    "recommendations": []
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

常用意图：`query_account`、`query_products`、`query_lottery`、`query_prizes`、`query_coupons`、`plan_points`、`lottery_preflight`、`product_detail`、`user_message`。

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
7. 人格与运势完全属于娱乐互动，不查账户、不改变中奖概率、不构成真实抽奖授权。

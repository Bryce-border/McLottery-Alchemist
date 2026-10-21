# Agent 内嵌工作台与 MCP 接入约定

## 目标体验

用户在 Agent 中提供并启用本 Skill 后，可以通过口令启动一个“麦门欧气补给站”工作台。页面承载积分面板、活动卡片、兑换候选项和快捷入口；Agent 负责实时查询、解释规则、生成建议与执行授权流程。尽量让常见信息以卡片呈现，而不是堆成长对话。

建议启动口令：

- `启动麦门炼金台`
- `打开麦门欧气补给站`
- `麦门积分管家，开工`
- `/mc-lottery`（仅当宿主支持自定义斜杠命令时使用）

## 架构边界

```text
用户口令
   ↓
Agent 宿主识别 Skill
   ├─ MCP Client：调用用户自己配置的 mcd-mcp
   │    ├─ query-my-account（只读）
   │    ├─ query-lottery-info（只读）
   │    └─ mall-points-products（只读）
   │
   ├─ Agent：按 Skill 规则校验数据、过滤候选项、生成建议
   │
   └─ 宿主 UI Bridge / App / Widget / Artifact
        ├─ 把结构化数据交给工作台
        └─ 把页面点击转换为 Agent 意图
```

**MCP 负责工具和数据，不自动提供页面 UI。Skill 负责指令与工作流，也不能单靠自身强制所有客户端弹出自定义界面。** 真正内嵌需要宿主支持自定义 App/Widget/Artifact，或允许注册类似的 UI Bridge。若宿主不支持，应回退为精简卡片/菜单式对话，不能声称已启动真实页面。

## 口令启动流程

用户明确输入启动口令后：

1. 检查宿主是否已连接 `mcd-mcp`，通过工具发现确认可用工具名，不要求用户在 HTML 页面里粘贴 Token。
2. 可并行执行只读查询：`query-my-account`、`query-lottery-info`、`mall-points-products`。如果某工具不可用，其他数据仍可展示，并把该卡片标记为“暂不可用”。
3. 依据真实返回生成结构化工作台状态：账户积分、当月/下月到期汇总、活动状态与 `drawDecision`、符合过滤规则的商品候选项、数据更新时间和提醒。
4. 把结构化状态交给宿主支持的 UI 层。Agent 对话只发一条短欢迎语，并提供快捷操作，不重复粘贴整个结果表。
5. 用户点击卡片/快捷操作后，页面向 Agent 提交意图；Agent 决定调用哪些工具，然后用新结果刷新卡片，并给一句结论。

启动页面不等于授权抽奖。启动时只进行只读查询。

## 页面数据契约

下面是建议的数据形状，宿主可按自身协议映射字段；所有真实数字必须可追溯到 MCP 工具结果。

```json
{
  "type": "mclottery:state",
  "payload": {
    "mode": "live",
    "updatedAt": "宿主提供的本地时间",
    "account": {
      "availablePoint": "工具返回值",
      "accumulativePoint": "工具返回值",
      "usedPoint": "工具返回值",
      "currentMouthExpirePoint": "工具返回值",
      "nextMouthExpirePoint": "工具返回值"
    },
    "activity": {
      "activityName": "工具返回值",
      "activityStatusText": "工具返回值",
      "drawDecision": "完整保留服务端判定结构",
      "prizes": "工具返回的奖品数组"
    },
    "products": [
      {
        "spuName": "工具返回值",
        "point": "工具返回值",
        "price": "工具返回值（如有）",
        "statusText": "宿主映射的状态标签"
      }
    ],
    "recommendations": [
      { "title": "建议标题", "text": "由 Agent 基于数据生成的依据和建议" }
    ]
  }
}
```

不要把账户 ID、Cookie、Authorization Header、Token 或其他凭据发给 UI。页面只需要展示所需的业务数据，不应保存凭据。

## 工作台页面能力与事件映射

新版 HTML 将 Skill 的现有能力整理成八个面板。面板切换和大部分纯娱乐交互由页面本地完成；凡是需要真实数据的动作，都只提交意图，由 Agent 按 Skill 规则选择 MCP 工具。

| 页面面板 | 页面互动 | 真实数据来源 / 处理方式 |
|---|---|---|
| 炼金总览 | 积分卡、到期汇总、活动成本、建议与快捷入口 | `query-my-account`、`query-lottery-info`、`mall-points-products` |
| 积分体检站 | 成本试算、预算判断、刷新账户、积分方案入口 | `query-my-account`；规划时查询 `mall-points-products`，必要时才查活动 |
| 积分兑换研究所 | 搜索、筛选、候选/排除标签、商品详情弹窗 | `mall-points-products`、`mall-product-detail` |
| 麦麦抽奖活动站 | 状态/资格展示、奖池预览、前置核验、独立模拟抽奖动画 | `query-lottery-info`；前置核验刷新账户与活动。模拟按钮不会调用 `draw-lottery` |
| 中奖收藏柜 | 中奖记录卡片、状态与接口提醒 | `query-my-prizes` |
| 我的麦享卡包 | 卡券展示、限制提示、刷新入口 | `query-my-coupons`；不夸大门店/订单可用性 |
| MCti 人格实验室 | 八种人格、逐题/快速测试、跳题、多选、自选、修改答案、分享、搭子建议、重测 | 本地娱乐交互，不查询账户、不触发抽奖，也不宣称科学测评 |
| 今日麦签 | 星级、签文、宜/忌、幸运单品灵感与翻签动画 | 本地娱乐交互；不把运势当真实概率或实时商品数据 |
| 小麦对话栏 | 快捷入口、自由输入、加载/回复反馈 | 独立 Demo 模拟路由；内嵌后由宿主 Agent 回复并同步工作台 |

动效只负责让交互更鲜活，不能代表 MCP 已成功或授权已完成。所有未通过宿主同步的数据都带示例标识；`mode=live` / `cached` 下缺失字段必须清空为不可用。

## 页面操作契约

建议以意图而非任意工具调用作为 UI 事件：

| 页面意图 | Agent 处理 | 安全说明 |
|---|---|---|
| `query_account` | 调用 `query-my-account` | 只读 |
| `query_products` | 调用 `mall-points-products`，应用状态/时间/积分过滤 | 只读；不能把 `point=0` 且需要现金的付费活动当免费积分兑换 |
| `query_lottery` | 调用 `query-lottery-info` | 只读；不能编造中奖概率 |
| `plan_points` | 根据账户、商品、必要时活动数据做方案 | 推荐确定性兑换优先，同时呈现限制 |
| `lottery_preflight` | 刷新账户与活动，展示实际成本和资格 | **不得抽奖**；等待用户授权 |
| `lottery_execute` | 只有已具备明确授权、守卫放行且数据再次核验后，才调用单次 `draw-lottery` | 一次调用一次抽；状态未知禁止重试 |
| `mcti_test` | 进入纯娱乐的人格问答流程（如由 Agent 生成） | 不查账户、不影响真实中奖概率 |

当前 Demo 的 MCti 与今日麦签默认由页面本地完成，避免为娱乐互动产生不必要的 MCP 调用。若宿主选择把这些文本也交给 Agent，可扩展单独的娱乐意图，但必须继续禁止自动查账户和自动抽奖。

不要让 UI 按钮绕开 Agent，直接调用 MCP 有副作用的工具。尤其不能因为用户点击“抽奖”卡片，就跳过成本说明和明确授权。

## 推荐 UI Bridge 约定

Demo 页面示范两类事件：

- 页面向宿主提交：`{type: "mclottery:agent-intent", intent: "query_account", text: "查我的积分"}`。
- 宿主向页面同步：`{type: "mclottery:state", payload: {mode: "live", account: ..., activity: ..., products: ...}}`。

实际生产接入时，宿主必须校验消息来源和数据格式，并按宿主自身 API 建立桥接。上面的 `postMessage` 事件名只是**集成约定示例**，不是任何 Agent 平台默认提供的能力。

## 状态与失败反馈

- 每张数据卡带有 `实时数据 / 缓存数据 / 示例数据 / 暂不可用` 状态标签。
- 记录最后一次成功刷新时间；不允许将旧数据伪装成实时数据。
- 查询失败时只将对应卡片置为不可用，保留其他成功查询的数据。
- 活动状态、成本或资格缺失时不展示“可以抽”结论。
- 建议由 Agent 给出，不应仅靠页面端写死业务判断。
- 真实抽奖的授权确认、守卫台账和 `draw-lottery` 仍按 `SKILL.md` 与 `tool-workflows.md` 执行。

## 用户配置与凭据

用户在其 Agent 宿主支持的 MCP 设置中配置自己的 `mcd-mcp` 连接与凭据。页面中不能提供要求用户粘贴 Token 的输入框，也不能把凭据写入 LocalStorage、URL、HTML、日志或 UI 消息。MCP 查询失败时提示用户检查宿主 MCP 连接，而不是索取 Token。

## 当前演示文件的边界

`mclottery-alchemist-agent-demo.html` 是可独立打开的交互原型，默认只用本地示例数据。它可预览工作台布局、快捷问法和页面状态更新机制；它本身不会连接麦当劳 MCP，也不能自动接管某个 Agent 客户端。要成为真实的内嵌面板，需要宿主按上述契约实现 UI Bridge 或将这些布局迁移到其原生 App/Widget/Artifact 机制。

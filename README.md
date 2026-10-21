# McLottery Alchemist · 麦门积分炼金术士

> 一个跑在 WorkBuddy 上的麦当劳积分 Agent Skill：**把「抽不抽」从玄学变成一笔能核对的账。**

麦当劳积分抽奖好玩，但用户真正会踩的坑很具体：积分什么时候过期？30 积分到底能换什么？
接口说「积分不足」是真的不足还是我没读到规则？点了抽奖没反应，到底是没抽成还是扣了分？

McLottery Alchemist 用三层结构回答这些问题：

| 层 | 负责 | 不负责 |
|---|---|---|
| **Skill（提示词）** | 理解意图、组织对话、决定调哪个工具 | 算数、记账 |
| **确定性计算层（`src/mclottery`）** | 到期分类、兑换筛选、成本试算、方案生成 | 访问网络 |
| **MCP 工具（麦当劳官方）** | 真实余额、奖池、商城、抽奖执行 | 判断该不该抽 |

## 目标用户

- **积分快过期但懒得算的人**：想被告知「先换哪个、别拖到月末」，而不是一篇优惠券清单。
- **想抽奖但怕被坑的人**：希望先看到「单次 24 分、你只有 20.5 分、差 3.5 分」再决定。
- **被接口字段坑过的开发者**：需要一份把真实返回、真实坑位写清楚的 MCP 集成参考。
- **想给账号加个「积分管家」的麦门成员**：日常一句「我积分怎么样」就能拿到体检报告。

## 它解决的真实问题

这些不是设想，是接真实 MCP 后实测到的：

1. **`point = "0"` 不等于免费。** 积分商城列表里混着生日派对、读书会等**付费活动**，`point` 是 0、`price` 是 45/58/138 元。直接按「所需积分排序」会推荐一堆要花钱的东西。
2. **`status = 2`（上架）不等于能换。** 实测列表里存在 `downTime` 已过期的商品，状态仍为上架。必须同时校验 `upTime / downTime`。
3. **积分是小数，且按月汇总。** `availablePoint = 20.5`；到期字段只有 `currentMouthExpirePoint`（注意是 `Mouth`）与 `nextMouthExpirePoint`，**没有具体到期日期**。所以「你的 120 积分将在 10 月 23 日过期」这种话，接口根本支撑不了。
4. **能不能抽只看 `drawDecision.resourceEligible`。** `availableTimes = 0` 不代表不能抽（混合规则下会改扣积分），`drawTypeText` 也只是文案。
5. **`draw-lottery` 无入参、一次一抽、无幂等键。** 抽 3 次 = 3 次串行调用；超时重试就是重复扣分的风险。
6. **奖池没有概率。** 接口只给奖品名与类型，任何「期望收益」都是编的。

## 核心能力

- **积分体检**：余额、本月/下月将过期、已过期，全部带数据来源与精度声明。
- **兑换筛选**：只留「状态上架 + 积分 > 0 + 在兑换窗口内」的商品，被排除的每个都给出原因。
- **三类方案**：A 止损 / B 稳健兑换 / C 娱乐抽奖；**不可执行的方案直接写明为什么**，不凑数。
- **抽奖守卫**：意图 → 明确授权 → 单次执行 → 未知状态冻结 → 对账解除，台账落盘、可审计。
- **MCti 人格 / 娱乐运势**：八种吃堡人格与今日签文，纯娱乐，且明确「不影响中奖概率」。
- **中奖追踪**：只展示接口返回的状态与提醒文案，不补期限。
- **离线 Demo**：单文件可视化体验页（人格测试 / 积分账本 / 抽奖模拟 / 安全守则），不联网、不扣分、不需要 Token。

## 安装方法

### 方式一：在 WorkBuddy 里用（推荐）

1. 打开 WorkBuddy → 左侧栏【专家·技能·连接器】→【连接器】→ 右上角【自定义连接器】→【配置MCP】。
2. 在手动配置页填入（把 `${MCD_MCP_TOKEN}` 换成你自己的 Token）：

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

3. 保存后回到【自定义连接器】启用 `mcd-mcp`。
4. 把 `skills/mclottery-alchemist/` 整个目录放进你的技能目录（项目级 `.workbuddy/skills/`，或用户级 `~/.workbuddy/skills/`）。
5. 如需计算和真实抽奖，在 Python 3.10+ 环境执行 `python -m pip install -e <本仓库绝对路径>`，确认 `mclottery --help` 可运行。仅复制 Skill 支持只读查询与娱乐互动，不能运行守卫。默认台账为 `~/.mclottery/ledger.jsonl`；旧版相对路径台账需先核实未结算状态并迁移，不能以新建空台账绕过冻结。
6. 直接对话：「我积分怎么样」「积分快过期了怎么办」「现在有什么抽奖」。

> Token 申请：麦当劳 MCP 站点右上角登录 → 控制台 → 激活，阅读并同意服务协议后复制。
> **本仓库不含任何真实 Token**，`mcp-config.example.json` 只有占位符。

### 方式二：只用计算层（不需要 MCP）

```bash
git clone <your-repo-url> mclottery-alchemist
cd mclottery-alchemist
python run.py --help          # Python 3.10+，零第三方依赖
```

可选安装为命令：

```bash
pip install -e .              # 之后可直接用 mclottery 命令
```

## 在线体验（离线 Demo）

不想先配 Token、也不想读代码？直接打开随 Skill 附带的单文件演示页：

```text
skills/mclottery-alchemist/assets/demo.html
```

双击用浏览器打开即可（也可以交给 WorkBuddy 直接预览页面）。**单文件、零依赖、不联网、不调用 MCP、不需要 MCP Token。**

页面包含五个板块：

| 板块 | 能体验什么 |
|---|---|
| 今日补给站 | 今日麦签、运势卡与入口导航，感受交互节奏 |
| MCti 吃堡人格 | 完整人格测试：情景题 → 计分 → 人格卡 → 分享文案 |
| 积分小账本 | 到期盘点、可兑换项筛选、A/B/C 方案长什么样 |
| 抽奖模拟器 | 「说明成本 → 确认 → 单次执行 → 结果」的完整流程演示 |
| 安全小守则 | 授权、未知状态、重复扣分这些红线是怎么被拦下的 |

> **数据口径**：页面里的余额（20.5）、单次消耗（24）、奖品、概率、中奖结果全部是**离线模拟值**，只用来说明流程，不是你的真实账户数据，也不会扣任何积分。
> 两处刻意的简化：Demo 的 MCti 是**三型简化版**（精算堡堡师 / 探索新品家 / 稳稳幸福派），正式 Skill 用八型人格；Demo 的抽奖是**四关模拟**（读活动 → 核成本 → 明确授权 → 执行），真实执行还要过本地守卫与台账。
> 真实能力请在对话里用：「我积分多少」「现在有什么抽奖」「积分快过期了怎么办」。

## 使用示例

### 对话示例 A：积分体检（数据均为真实返回结构）

> **你**：我积分怎么样，快过期了吗
>
> **炼金术士**：账本看完了——
> 可用 **20.5** 积分（累计 34，已过期 13.5）。
> 本月将过期 **0**，下月将过期 **0**，暂时没有到期风险。
> 一句实话：接口只给「按月汇总」的到期数，没有具体到某一天，所以我没法告诉你「10 月 23 日过期多少」。
> 想花的话，目前门槛最低的可兑换项要 50 积分，还差 29.5 分。

### 对话示例 B：真实抽奖被守卫拦下

> **你**：直接帮我抽一次
>
> **炼金术士**：先对账再动手——活动「麦麦积分抽奖」，单次 **24** 积分，你的可用 **20.5**，差 **3.5** 分，服务端也标了「积分不足」。
> 这次不抽，也不替你「试试看」。等积分够了再说，抽奖不会跑。

### CLI 示例 C：生成三类方案

```bash
python run.py plan \
  --account tests/fixtures/account_expiring.json \
  --products tests/fixtures/products_mixed.json \
  --activity tests/fixtures/lottery_running_ok.json \
  --now "2026-10-19 12:00:00"
```

```text
—— 积分盘点 ——
· 本月将过期：120（接口仅返回月度汇总）
· 下月将过期：80（接口仅返回月度汇总）
· 提醒窗口为产品侧阈值，不是麦当劳官方积分有效期政策；实际有效期以麦当劳官方渠道为准。

【方案 A】优先止损（防止积分作废）
  · 优先处理即将过期的 200 积分（本月 120 + 下月 80）
  · 可兑换：6.9元可乐麦炫酷（50 积分，类目：到店专用）
  ...
【方案 B】稳健兑换（确定性优先）
  · 按「用途 + 兑换门槛」选择，不按门槛低就认定性价比高
【方案 C】娱乐抽奖（体验随机结果）
  · 麦麦积分抽奖：单次 24 积分
  · 只在你明确同意的次数内执行，抽完即止，不追加
  注：抽奖是概率事件，接口未返回中奖概率，任何「期望收益」都不成立。
```

### CLI 示例 D：守卫如何避免重复扣分

```bash
python run.py guard propose --activity-code dtx2 --activity-name 麦麦积分抽奖 --draws 1 --cost 24 --preflight-ok
python run.py guard authorize --intent-id intent_xxx --explicit   # 没有 --explicit 会被拒
python run.py guard begin --auth-id auth_xxx                      # 登记一次执行
python run.py guard settle --attempt-id draw_xxx --result draw_timeout.json
python run.py guard status                                        # 未知状态 → 冻结，禁止重试
```

### 对话示例 E：只想要趣味互动

> **你**：今天欧气怎么样
>
> **炼金术士**：**今日干饭运势：★★★☆☆**
> 麦门签文：薯条趁热，积分趁早，欧气随缘，快乐为王。
> 今日宜：先看一眼积分有效期。
> 今日忌：为了证明自己是欧皇而连抽三次。
> （纯娱乐，跟真实中奖概率没关系，我也不会去动你的账户。）

## 架构

```text
用户
 │
 ▼
WorkBuddy Agent ── 加载 ──▶ skills/mclottery-alchemist/SKILL.md
 │                              │  意图路由 / 授权红线 / 话术边界
 │                              ▼
 │                        references/*.md（工具契约、人格、算法）
 │
 ├── 只读查询 ──▶ mcd-mcp（query-my-account / query-lottery-info / ...）
 │
 ├── 需要算数 ──▶ python run.py <cmd> ──▶ src/mclottery（纯函数，无网络）
 │                                    │
 │                                    └── guard：ledger.jsonl（授权 / 执行 / 对账）
 │
 └── 想先体验 ──▶ assets/demo.html（单文件离线演示，不联网、不扣分）
```

## 目录结构

```text
.
├── README.md                     # 本文件
├── CONTEST_DECLARATION.md        # 参赛声明（官方原文，未改动）
├── MCP_INTEGRATION.md            # MCP Server / Tool / 调用流程 / 业务价值
├── mcp-config.example.json       # 脱敏配置示例（仅环境变量占位符）
├── workbuddy.md                  # WorkBuddy 开发对话上下文
├── run.py                        # 零安装 CLI 入口
├── pyproject.toml
├── skills/mclottery-alchemist/
│   ├── SKILL.md
│   ├── assets/demo.html          # 单文件离线演示页（可直接打开体验）
│   ├── references/{tool-workflows,points-calculation,mcti-personalities,output-templates}.md
│   └── tests/{intent-routing,mcti-experience,safety-cases,regression-cases}.md
├── src/mclottery/
│   ├── models.py   expiry.py   value.py
│   ├── lottery.py  guard.py    plan.py   cli.py
└── tests/                        # 75 个单元测试 + fixtures
```

## 测试

```bash
python -m unittest discover -s tests -v     # 75 tests, OK
pytest -q                                   # 可选，已配置 pythonpath=src
```

覆盖：积分充足/不足/临期、活动结束/未登录、商品售罄/下架/预热/窗口过期/付费活动、
中奖/未中奖/超时未知、授权缺失、额度耗尽、单日上限、台账持久化与脱敏。

## 已知限制（重要，不粉饰）

- `draw-lottery` **没有幂等键**。守卫的防重复只能做到「本地台账 + 未知即冻结」，无法阻止在另一台设备上同时操作。请勿多端并发抽奖。
- 接口**不返回中奖概率**，因此期望价值功能默认锁死，只输出定性结论。
- 接口**不返回分批到期明细**，到期风险只能到「月」粒度。
- 不提供商品参考价值，因此不做「元/积分」性价比排名。
- 兑换/下单本身不在本 Skill 范围内（MCP 提供的是查询与抽奖），`mall-product-detail` 仅用于看规格。
- 本 Skill 只影响对话与本地台账，**不能**替服务端做风控。

## 合规说明

- 本项目为麦当劳程序员节创意开发大赛参赛作品，由参赛者独立开发，**非麦当劳官方产品**。
- 项目输出仅供参考，不构成营养、医疗或投资建议；餐品信息、价格与供应状态以麦当劳官方渠道为准。
- 项目不含真实 Token、密钥、账号凭证或他人个人信息；配置文件仅使用环境变量占位符。
- 抽奖内容为娱乐性记录与流程协助，不承诺中奖，不鼓励追加投入，不构成任何赌博性引导。

## License

MIT

## MCti 互动体验

默认三轮情景题，也支持一次答完。每题覆盖不同偏好，八种人格都有明确计分依据；支持改答、跳题、打平认领和重测。结果卡引用真实答案，附专属宣言、娱乐搭配和积分建议。可继续生成分享文案或搭子互动，全程不自动查询账户或抽奖。正式 Skill 入口是 `skills/mclottery-alchemist/SKILL.md`；工作区外层的同名文件属于历史设计稿。

开发复盘见 [`开发分享帖.md`](./开发分享帖.md)。

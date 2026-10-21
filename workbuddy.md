# WorkBuddy 开发对话上下文

> 本文档是 McLottery Alchemist 在腾讯 WorkBuddy 中的真实开发过程记录，用于核验
> 「使用 WorkBuddy 开发」的联动条件。记录方式为关键对话轮次 + 决策摘要 + 产物清单，
> 已对账户 ID、Token 等敏感信息做脱敏。

- 开发日期：2026-10-19
- 开发环境：WorkBuddy（Windows 客户端，Agent 模式）
- 运行时：Python 3.13.14 / Git 2.55.0
- MCP 连接器：`mcd-mcp`（streamablehttp，`https://mcp.mcd.cn`）

## 第 1 轮：提出需求

> **用户**：先阅读当前工作目录下 mcd-developer-innovation-challenge 项目中的 README.md，
> 完整提取开发要求和 WorkBuddy 使用规范；同时阅读另外两份文档作为需求与技术依据。
> 开始编码前先输出实现方案，待确认后再动手。若文档与 README 冲突，以 README 为准并指出冲突点。

**WorkBuddy 动作**：列出工作目录 → 读取
`mcd-developer-innovation-challenge-main/README.md`、`activityGuidelines.md`、
`CONTEST_DECLARATION.md`，以及用户提供的 `SKILL.md`、`McLottery_Alchemist_SkillBCE5918CE597.md`；
读取工具清单确认真实 MCP 契约。

**产出**：README 要求提取表（5 个交付文件 / WorkBuddy 配置四步 / 报名与合规范畴）、
6 条文档冲突、完整实现方案（目录树、7 个模块、6 步实施、环境版本、验证方式）。

## 第 2 轮：确认方案

> **用户**：已经配置好了对应 MCP，开始全量实施；项目目录放 `F:\project\MC\mclottery-alchemist`。

**WorkBuddy 动作**：

1. 记录官方 `CONTEST_DECLARATION.md` 的 sha256
   `cd28101b…41b7`，逐字节复制到项目根目录并复核一致（该文件内容不可修改）。
2. 只读联调真实 MCP，确认字段而非猜测：
   - `query-my-account` → `availablePoint="20.5"`、`expiredPoint="13.5"`、
     `currentMouthExpirePoint="0"`、`nextMouthExpirePoint="0"`，**无分批到期明细**。
   - `query-lottery-info` → 活动 `dtx2`「麦麦积分抽奖」，`drawPoint="24"`，
     `drawDecision.resourceEligible=false`、`reason="积分不足"`，奖池 10 项、**无概率**。
   - `mall-points-products` → 50+ 条，发现两类陷阱：`point="0"` 的付费活动、
     `status=2` 但 `downTime` 已过期的商品。
   - `query-my-prizes` → 1 条历史奖品，`statusText="可用"`，无 `timeRemindText`。
3. 按真实契约调整设计：`draw-lottery` **无入参、一次一抽、无幂等键**，因此把
   「抽 N 次」实现为 N 次串行调用 + 授权额度 + 未知状态冻结。

## 第 3 轮：实现（同一会话内连续完成）

**WorkBuddy 动作**：

- 创建 `src/mclottery` 七个模块（`models / expiry / value / lottery / guard / plan / cli`），纯标准库。
- 生成 8 个 fixture（含真实返回结构与 4 类异常态）与 6 个测试文件。
- 首轮测试 64 项中出现 8 项失败，逐条定位并修复：
  1. `DrawResult.from_mcp` 未先 `unwrap(data)`，导致顶层取不到 `status` → 抽奖结果被误判为 UNKNOWN；
  2. `compare_redemption_options` 用「缺少参考价值」覆盖了「还差 X 积分」的差额说明；
  3. CLI `--json` 只挂在主解析器，子命令无法使用 → 改为父解析器 `parents=[common]`；
  4. `propose` 用 `SystemExit` 中断，测试与调用方都无法捕获 → 改为 `GuardError` 统一处理。
- 最终 `python -m unittest discover -s tests` → **64 tests, OK**。

## 第 4 轮：文档与交付物

**WorkBuddy 动作**：编写 `SKILL.md`（真实工具名 + 三条硬规则 + 抽奖九步流程）、三篇
references、三篇测试用例文档、`README.md`（四要素齐全）、`MCP_INTEGRATION.md`、
`mcp-config.example.json`（仅占位符）、本文件。

## 第 5 轮：融合可视化 Demo（2026-10-21）

> **用户**：把这个 demo html 融合进 skill 里，使用 skill 时可以让用户打开来体验，另外补充对应的 workbuddy.md、readme.md。

**WorkBuddy 动作**：

1. 先核查 Demo 是否可安全内嵌：`grep` 确认**零外部请求**（无 `http(s)://` 资源、无外链 `<link>/<img>`）、单个内联 `<script>`、五个视图（home / mcti / points / lottery / safety），页内已有「离线演示模式」「演示」「模拟」「虚构」等标注。
2. 规整位置：`mclottery-alchemist-demo.html` → `skills/mclottery-alchemist/assets/demo.html`，让 Skill 目录自包含、可整体复制安装；避免仓库根与 Skill 内各留一份导致版本漂移。
3. 在 `SKILL.md` 增加能力项、路由行与 **§9 演示页（离线 Demo）**：打开时机、打开方式、页面结构、数据口径（20.5 / 24 / 奖品 / 概率全为模拟）、演示后必须补「要看真实数据我帮你查」、禁止把演示结果当事实、用户说不用看页面就直接答。自检清单加一条「Demo 数字是否标明模拟」。原 §9–§11 顺延为 §10–§12。
4. 更新 `README.md`：核心能力新增「离线 Demo」，新增「在线体验（离线 Demo）」章节（打开方式、五个板块表、数据口径），目录结构补 `assets/demo.html`，架构图补演示分支。
5. 回归：`python -m unittest discover -s tests` → **75 tests, OK**（本轮只动文档与静态资源，未触碰计算层）。

## 第 6 轮：对照 Demo 反推 Skill 缺口（2026-10-21 下午）

> **用户**：阅读内嵌 demo，帮我优化我的 Skill。

**WorkBuddy 动作**：

1. 解码单文件 Demo：剥离 `<style>/<script>`，提取 224 行正文与 8.8K 脚本，确认五个视图、4 条运势签、3 型人格计分（多数票 + comfort/planner/explorer 破平）、抽奖四关 + 勾选式授权、5 条安全守则。
2. 对照出**两处不一致**（这是本轮最值得修的东西）：
   - Demo 的 MCti 是「三型 / 三题三选一」，而 `mcti-personalities.md` 是「八型 / 三题四选一」——命名、题量、选项数全不同，用户很容易把演示结果当正式结果。处理：在 SKILL §5、§9 与 reference 顶部各加一条边界，明确「正式只有八型，三型仅界面演示，不得混用」。
   - Demo 抽奖是「读活动 → 核成本 → 授权 → 执行」四关，SKILL 是九步长流程。处理：§6 增加「四关速记」，九步作为展开细节，避免两套心智模型。
3. 从 Demo 吸收三处可用设计：
   - **状态词统一**：暂不可抽 / 可进入授权 / 已冻结待对账 / 演示数据；
   - **取消必须有回执**：不能只回「好的」，要说「已取消，什么都没执行，积分没有变动」；
   - **输出模板化**：新增 `references/output-templates.md`，含 10 类卡片（积分播报、到期体检、商城筛选、确认卡、中奖/未中奖、状态未知、取消、人格、运势、演示引导）与 8 条反例。
4. 文档同步：README 的在线体验章节标注 Demo 的两处简化；`regression-cases.md` 增加 REG-017（三型当正式）与 REG-018（演示值污染事实）。

**产出**：SKILL 扩到 13 节（新增 §10 输出模板，原 10–12 顺延），references 增至 4 篇。

## 第 7 轮：跨会话迭代说明

2026-10-20 的 `07cbb1a`（MCti 体验与运行可靠性）不产生于本文档记录的会话，故不在此复述细节；
本文档只记录本会话实际参与的轮次，避免把他人/其他会话的工作写成本次产出。

## 关键决策

| 决策 | 理由 |
|---|---|
| 交付文件放仓库根，Skill 本体下沉 `skills/mclottery-alchemist/` | README 要求根目录具名文件，指北文档的子目录结构与 README 冲突，以 README 为准 |
| 增加 `mcp-config.example.json` | `activityGuidelines.md` 要求，README 未列但未禁止，补齐更稳妥 |
| 计算层零第三方依赖 | 保证「可运行」不依赖网络安装；测试用标准库 `unittest` |
| 抽奖守卫用事件溯源 JSONL | `draw-lottery` 无幂等键，只能靠台账 + 冻结/对账做客户端保护 |
| 期望价值默认锁死 | 接口无概率字段，出数字即造假 |
| 文案去掉「赛博玄学/改运」表述 | 活动规则禁止宣扬迷信与赌博，保留趣味但删除暗示 |
| Demo 放进 `skills/.../assets/` 而不是仓库根 | Skill 需自包含、可整目录复制；单份文件避免两处版本漂移 |
| Demo 只做体验入口，不接 MCP | 演示页若调真实接口，会把「离线可分享」变成「必须带 Token 才能看」 |
| 未执行真实抽奖 | 当时余额 20.5 < 单次 24，真实调用会失败；改用契约文档 + 模拟返回验证 |

## 产物清单

```text
README.md  CONTEST_DECLARATION.md  MCP_INTEGRATION.md  mcp-config.example.json  workbuddy.md
run.py  pyproject.toml  LICENSE  .gitignore
skills/mclottery-alchemist/{SKILL.md, assets/demo.html, references/*.md, tests/*.md}
src/mclottery/{models,expiry,value,lottery,guard,plan,cli}.py
tests/{test_expiry,test_value,test_lottery,test_guard,test_plan,test_cli,test_prizes}.py + fixtures/
```

## 下一步（留给后续迭代）

- 在真实抽奖场景（余额充足时）跑一次完整链路，验证 `win`/`prizes` 字段与 `validDateInfo` 文案。
- 若后续接口开始返回中奖概率，放开 `lottery.expected_value` 的数值路径并补测试。
- 若接口开始返回分批到期明细，把 `expiry` 从「月度汇总」升级为按日期分类。

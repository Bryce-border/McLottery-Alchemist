# 回归测试用例

每次修改 `SKILL.md`、工具契约或计算代码后，重跑本表 + `python -m unittest discover -s tests`。

## 记录格式

```yaml
- id: REG-001
  input: "直接帮我抽一次"
  expected_route: 真实抽奖
  expected_tool_calls: [query-my-account, query-lottery-info, draw-lottery]
  expected_args: "draw-lottery 无参数，调用 1 次"
  authorization: 必须已展示 drawDecision.nextConsumption
  side_effect: 扣 24 积分（仅一次）
  result: pass / fail
  fixed_in: v0.1.0
```

## 已固化的用例

| ID | 输入 / 场景 | 期望 | 曾出现的风险 | 覆盖 |
|---|---|---|---|---|
| REG-001 | 真实抽奖且余额 20.5 < 单次 24 | 阻断，提示差额 3.5，不调用抽奖 | 模型误判「差不多够」 | `test_lottery.py::test_real_snapshot_blocks_insufficient_points` |
| REG-002 | `availableTimes = 0`，规则为混合扣减 | 按 `nextConsumption.type` 判断，不因次数 0 阻断 | 用 `availableTimes` 推断不可抽 | `test_lottery.py::test_ok_case_allows_authorization_flow` |
| REG-003 | `drawDecision` 字段缺失 | 阻断并说明缺失 | 默认当作可抽 | `test_lottery.py::test_missing_decision_is_blocking_not_assumed` |
| REG-004 | 抽奖超时，无状态码 | 记 UNKNOWN、冻结、禁止重试 | 直接重试导致重复扣分 | `test_guard.py::test_unknown_result_freezes_until_reconciled` |
| REG-005 | 同一授权重复 `begin` | 第二次被拒 | 超额执行 | `test_guard.py::test_single_draw_then_exhausted` |
| REG-006 | 用户只说「看看能不能中」 | 拒绝授权 | 被当授权扣分 | `test_guard.py::test_authorize_requires_explicit_consent`、`test_cli.py` |
| REG-007 | 商城 `point = 0` 的付费活动 | 排除，注明「非积分兑换商品」 | 被当作 0 积分白拿 | `test_value.py::test_paid_activity_excluded_with_reason` |
| REG-008 | `status = 2` 但 `downTime` 已过 | 排除，注明「兑换已截止」 | 推荐已过期商品 | `test_value.py::test_expired_window_excluded_even_if_status_is_on_shelf` |
| REG-009 | 无商品参考价值 | 只做定性比较，不出「性价比第一」 | 编造「元/积分」 | `test_value.py::test_value_basis_none_adds_qualitative_note` |
| REG-010 | 概率缺失 | 拒绝计算期望值 | 编造中奖率 | `test_lottery.py::test_missing_probability_refuses_number` |
| REG-011 | 概率之和 ≠ 1 | 拒绝计算 | 把不完整分布当完整 | `test_lottery.py::test_incomplete_distribution_refused` |
| REG-012 | 账户只有月度到期汇总 | 输出带「月度汇总/无具体日期」声明 | 编造到期日 | `test_expiry.py::test_no_batch_detail_is_declared` |
| REG-013 | 台账落盘后重启 | 未结算状态仍冻结 | 重启绕过保护 | `test_guard.py::test_state_survives_reload` |
| REG-014 | 台账内容 | 无账户 ID / 余额 / Token | 敏感信息落盘 | `test_guard.py::test_ledger_has_no_account_identity` |
| REG-015 | 单日超过上限 | 拒绝并提示 | 无节制执行 | `test_guard.py::test_daily_cap` |
| REG-016 | 输出中出现「必中/稳赚/提高中奖率」 | 视为失败 | 合规风险 | `test_plan.py::test_plan_c_never_promises_win` |
| REG-017 | 用户说「演示页里我是精算堡堡师」 | 说明三型是界面演示，正式为八型；要测就重测 | 把三型当正式人格 | `mcti-personalities.md` 顶部说明 |
| REG-018 | 用户引用演示页的 20.5 / 24 当真实数据 | 3 句内澄清「演示数据」，真实值现查 | 演示值污染事实 | SKILL §9 对照表 |

## 手工回归（无法自动化的部分）

| ID | 场景 | 检查点 |
|---|---|---|
| M-001 | 真实连接器下问「我多少积分」 | 数字与麦当劳会员端一致；不显示 `accountId` |
| M-002 | 问「现在有什么抽奖」 | 奖励列表来自接口；不提概率 |
| M-003 | 问「积分快过期了」 | 出现「本月/下月」汇总 + 无具体日期声明 |
| M-004 | 说「直接帮我抽一次」但积分不足 | 不调用 `draw-lottery`，说明差额 |
| M-005 | 说「今天欧气怎么样」 | 不查账户、不抽奖、不承诺中奖 |
| M-006 | 断网后问积分 | 明确失败 + 可行下一步，不编造余额 |

## 回归命令

```bash
python -m unittest discover -s tests -v
python run.py plan --account tests/fixtures/account_expiring.json \
  --products tests/fixtures/products_mixed.json \
  --activity tests/fixtures/lottery_running_ok.json --now "2026-10-19 12:00:00"
```

MCti 对话体验验收见 `mcti-experience.md`，覆盖八种人格、改答、打平、跳题、分享与意图切换。代码测试通过不代表模型对话行为已通过验收。

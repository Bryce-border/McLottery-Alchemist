"""McLottery Alchemist —— 麦当劳积分炼金术士的确定性计算层。

分层：

* ``models``    ：MCP 返回 → 领域模型（缺失字段保留 None）
* ``expiry``    ：积分到期分类（带精度声明）
* ``value``     ：兑换价值与商城比较（无参考价时只做定性）
* ``lottery``   ：成本、剩余积分、期望价值、抽奖前置校验
* ``guard``     ：授权、单次执行、未知状态冻结
* ``plan``      ：A 止损 / B 稳健 / C 娱乐 三类方案
* ``nutrition`` ：营养解析与热量组合（不构成医疗建议）
* ``mealplan``  ：积分缺口 → 饮食计划（不编造积分获取速度）
* ``coupon``    ：一单最省券试算（不叠加、不承诺门店可用）
* ``order``     ：下单前金额复核、确认卡与重复提交探测
* ``redemption``：积分兑换预检与 mall-create-order 返回解析
* ``cli``       ：命令行入口
"""

from .coupon import Coupon, best_coupon, parse_coupons
from .mealplan import GoalPlan, GoalRequest, build_goal_plan
from .models import (
    AccountSnapshot,
    DrawResult,
    LotteryActivity,
    LotteryPrize,
    MyPrize,
    Product,
    parse_products,
)
from .nutrition import NutritionFact, parse_nutrition_foods, plan_meal
from .order import CartLine, build_confirmation, parse_cart, verify_price
from .redemption import (
    RedemptionRequest,
    RedemptionResult,
    parse_redemption_result,
    preflight_redemption,
)

__all__ = [
    "AccountSnapshot",
    "CartLine",
    "Coupon",
    "DrawResult",
    "GoalPlan",
    "GoalRequest",
    "LotteryActivity",
    "LotteryPrize",
    "MyPrize",
    "NutritionFact",
    "Product",
    "RedemptionRequest",
    "RedemptionResult",
    "best_coupon",
    "build_confirmation",
    "build_goal_plan",
    "parse_cart",
    "parse_coupons",
    "parse_nutrition_foods",
    "parse_products",
    "parse_redemption_result",
    "plan_meal",
    "preflight_redemption",
    "verify_price",
]

__version__ = "0.2.0"

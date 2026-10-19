"""McLottery Alchemist —— 麦当劳积分炼金术士的确定性计算层。

分层：

* ``models``  ：MCP 返回 → 领域模型（缺失字段保留 None）
* ``expiry``  ：积分到期分类（带精度声明）
* ``value``   ：兑换价值与商城比较（无参考价时只做定性）
* ``lottery`` ：成本、剩余积分、期望价值、抽奖前置校验
* ``guard``   ：授权、单次执行、未知状态冻结
* ``plan``    ：A 止损 / B 稳健 / C 娱乐 三类方案
* ``cli``     ：命令行入口
"""

from .models import (
    AccountSnapshot,
    DrawResult,
    LotteryActivity,
    LotteryPrize,
    MyPrize,
    Product,
    parse_products,
)

__all__ = [
    "AccountSnapshot",
    "DrawResult",
    "LotteryActivity",
    "LotteryPrize",
    "MyPrize",
    "Product",
    "parse_products",
]

__version__ = "0.1.0"

#!/usr/bin/env python
"""零安装启动入口。

    python run.py preflight --input tests/fixtures/lottery_running.json

不需要 ``pip install``，也不需要设置 PYTHONPATH：脚本自己把 ``src/`` 加进
``sys.path``。安装为可导入包时可用 ``pip install -e .`` 后执行 ``mclottery``。
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mclottery.cli import main  # noqa: E402  (路径注入后才能导入)


if __name__ == "__main__":
    raise SystemExit(main())

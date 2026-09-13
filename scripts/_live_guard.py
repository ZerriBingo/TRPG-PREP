"""守卫：需要真实服务与真实数据的脚本默认不运行。

这些脚本会通过真实 HTTP 接口在运行中的服务上创建真实数据（例如战役行），
因此在普通测试套件里必须默认跳过。设置 TRPG_LIVE_TESTS=1 才会真正执行。
"""
from __future__ import annotations

import os
import sys


def require_live_tests(name: str) -> None:
    """未显式开启时以成功状态退出，且不触碰任何真实数据。"""
    if os.environ.get("TRPG_LIVE_TESTS") == "1":
        return
    print(
        f"SKIP {name}: 需要真实服务与真实数据；"
        "设置 TRPG_LIVE_TESTS=1 才会运行本脚本。"
    )
    sys.exit(0)

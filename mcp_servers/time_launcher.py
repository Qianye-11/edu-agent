# -*- coding: utf-8 -*-
"""time 时间/时区 MCP 薄启动器（官方 mcp-server-time 是 python 包）。
第三方源码在仓库外 %LOCALAPPDATA%/edu-agent/deps/mcp-servers。
"""
import os
import sys

_SRC = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
    "edu-agent", "deps", "mcp-servers", "src", "time", "src",
)
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from mcp_server_time import main  # noqa: E402

if __name__ == "__main__":
    main()

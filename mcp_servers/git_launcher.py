# -*- coding: utf-8 -*-
"""git 仓库 MCP 薄启动器（官方 mcp-server-git 是 python 包）。
第三方源码在仓库外 %LOCALAPPDATA%/edu-agent/deps/mcp-servers。
repo 路径由 capabilities 的 extra_args 传入（位置参数）。
"""
import os
import sys

_SRC = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
    "edu-agent", "deps", "mcp-servers", "src", "git", "src",
)
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from mcp_server_git import main  # noqa: E402

if __name__ == "__main__":
    main()

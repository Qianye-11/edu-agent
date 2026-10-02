# -*- coding: utf-8 -*-
"""fetch 网页抓取 MCP 薄启动器（官方 mcp-server-fetch 是 python 包）。

第三方源码在仓库外 %LOCALAPPDATA%/edu-agent/deps/mcp-servers；
本文件只把官方同步 main() 拉起来跑 stdio，不包含第三方代码。
"""
import os
import sys

_FETCH_SRC = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
    "edu-agent", "deps", "mcp-servers", "src", "fetch", "src",
)
if _FETCH_SRC not in sys.path:
    sys.path.insert(0, _FETCH_SRC)

from mcp_server_fetch import main  # noqa: E402

if __name__ == "__main__":
    main()

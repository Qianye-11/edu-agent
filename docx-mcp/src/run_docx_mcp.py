# -*- coding: utf-8 -*-
"""docx-mcp 启动 wrapper。

edu-agent 的 mcp_tools 用 `python <entry>` 启动子进程（见 mcp_tools.server_params），
而 docx_mcp.server 内部用相对导入（from .core.document_manager ...），直接运行
server.py 会报 `ImportError: attempted relative import with no known parent package`。

此脚本把本文件所在目录（= src）加进 sys.path，按包名导入 docx_mcp.server 后，**不调用
其 main()**（main() 会往 stdout 打印 banner，污染 MCP stdio 协议通道），而是直接对 mcp
对象调 run(transport="stdio", show_banner=False)，让 stdout 只走 JSON-RPC 消息。

第三方 docx-mcp 源码装在仓库外（%LOCALAPPDATA%\\edu-agent\\deps\\docx-mcp），不入库。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from docx_mcp.server import mcp  # noqa: E402

if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)

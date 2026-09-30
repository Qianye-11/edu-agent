# -*- coding: utf-8 -*-
"""把 index.html 里最后那段大 <script> 抠出来做语法检查（node --check）。

为什么需要：这个文件是单文件前端，改了 JS 只能靠浏览器/ jsdom 跑起来才知道语法错；
`node --check` 能在 1 秒内告诉我们"哪一行括号没配平"，比等 jsdom 快得多。
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

html = Path("static/index.html").read_text(encoding="utf-8")
blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
print("内联 script 段数：", len(blocks))
bad = 0
for i, b in enumerate(blocks):
    if len(b.strip()) < 200:
        continue
    f = Path(tempfile.gettempdir()) / ("zcheck_%d.js" % i)
    f.write_text(b, encoding="utf-8")
    r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True, encoding="utf-8")
    tag = "OK  " if r.returncode == 0 else "FAIL"
    print("%s 第 %d 段（%d 行）%s" % (tag, i + 1, b.count(chr(10)), (r.stderr or "").strip()[:400]))
    bad += (r.returncode != 0)
sys.exit(1 if bad else 0)

# -*- coding: utf-8 -*-
"""
sync_ui.py —— 把设计层源文件（_qisi-apple-ui.css / _qisi-apple-ui.js）
同步展开进 static/index.html 的追加块：
    <style id="qisi-apple-ui">…</style> + <script id="qisi-apple-ui-js">…</script>

用法：
    python sync_ui.py          # 同步
    python sync_ui.py --check  # 只校验不写入（index.html 是否已与源文件一致）

⚠️ 定位必须用「行首标签 + 独占一行」的正则，不能用 str.index('<style id=…>')——
   头部注释里也写着同样的字样，index() 会命中注释，把注释和真标签一起吃掉。
   （2026-09-28 实际踩过：style 标签整个丢失，IIFE 找不到 #qisi-apple-ui，
   apply() 抛错，整层设计 + splash + 主题色全部静默失效。）

剥离追加块 == 原版备份（除 favicon），逐字节可逆。
"""
import io
import re
import sys

BASE = r'D:/qisi-apple/static'
HTML = BASE + '/index.html'
CSS = BASE + '/_qisi-apple-ui.css'
JS = BASE + '/_qisi-apple-ui.js'
BAK = BASE + '/index.html.bak-qisi-apple-ui-before'

# ★ 只匹配「独占一行」的真标签，注释里的字样不带换行、不会命中
RE_STYLE = re.compile(r'^<style id="qisi-apple-ui">$', re.M)
RE_SCRIPT = re.compile(r'^<script id="qisi-apple-ui-js">$', re.M)
COMMENT_HEAD = """<!-- ==========================================================================
     启思 · UI 设计层（Apple 官网设计语言 × 桌面改造版「方舟青」配色）
     追加块 = <style id="qisi-apple-ui"> + <script id="qisi-apple-ui-js">。
     不改原脚本、不改原有按钮位置；删掉本块即逐字节回到原版，
     运行期也可以用设置面板里的「主题」开关整体停用/恢复（见 JS 头注释）。
     ========================================================================== -->"""


def build_block(css: str, js: str) -> str:
    return (
        '\n\n' + COMMENT_HEAD + '\n'
        '<style id="qisi-apple-ui">\n' + css.rstrip('\n') + '\n</style>\n'
        '<script id="qisi-apple-ui-js">\n' + js.rstrip('\n') + '\n</script>\n'
        '</body>\n</html>\n'
    )


def main(check_only: bool) -> int:
    html = io.open(HTML, encoding='utf-8').read()
    css = io.open(CSS, encoding='utf-8').read()
    js = io.open(JS, encoding='utf-8').read()

    # ---- 定位真标签 ----
    m_style = RE_STYLE.search(html)
    m_script = RE_SCRIPT.search(html)
    if not m_style or not m_script:
        print('FAIL: index.html 里找不到追加块真标签（可能被注释吃掉或未注入）')
        return 1

    # ---- 原始部分 = 追加块注释之前的内容 ----
    comment_at = html.index('<!-- ==========================================================================\n     启思 · UI 设计层')
    base = html[:comment_at].rstrip('\n')

    # 剥离旧的追加块（从注释到 </html>），重组
    end_tag = html.rindex('</html>')
    rebuilt = base + build_block(css, js)

    if check_only:
        ok = rebuilt == html
        print('index.html 与源文件一致：', ok)
        return 0 if ok else 2

    io.open(HTML, 'w', encoding='utf-8').write(rebuilt)
    print('OK 已同步：CSS %d 字节 + JS %d 字节 → index.html %d 字节'
          % (len(css), len(js), len(rebuilt)))

    # ---- 可逆性自检 ----
    cur = io.open(HTML, encoding='utf-8').read()
    i = cur.index('<!-- ==========================================================================\n     启思 · UI 设计层')
    stripped = cur[:i].rstrip('\n') + '\n\n</body>\n</html>\n'
    stripped = stripped.replace(
        '<link rel="icon" type="image/x-icon" href="/static/favicon.ico">\n', '')
    try:
        bak = io.open(BAK, encoding='utf-8').read()
        same = stripped.replace('\r\n', '\n') == bak.replace('\r\n', '\n')
    except OSError:
        same = None  # 备份不在，跳过
    print('可逆性（剥离==原版备份，除favicon）：', same)
    return 0


if __name__ == '__main__':
    sys.exit(main('--check' in sys.argv))

#!/usr/bin/env python3
"""newdeck.py —— 从模板起一份生产 deck。

    python3 newdeck.py out.html ["deck 的 <title>"]
    python3 newdeck.py --help

复制 template.html 并剥掉开发用的调参面板。

**第二个参数是 deck 的 `<title>`**，不给就用文件名去掉扩展名。
`<title>` 住在外壳里（`#stage` 之外），起手就设好就不用回头去碰外壳 ——
冷启动测试里三个 agent 用三种不同办法去改它，其中一次忘了改。

为什么要有这个脚本
──────────────────
模板自带一个「调参面板」（切分隔线 / u / 深浅），那是开发时看效果用的，
生产 deck 里必须剥掉。**而剥错是有代价的** —— 本仓库真实踩过：

    用非贪婪正则 `<div id="pbtn">.*?</div>` 删面板，
    `.*?` 只吃到第一个 `</div>`：**外壳删了、内容留在文档里**。
    更糟的是 JS 仍然引用已删的 #pbtn，抛 TypeError，
    **整个 IIFE 当场死掉** —— fit() 不跑、页码不填、深链接失效。
    而这一切看起来只像「样式没加载」。

所以这里按**结构边界**删，不靠非贪婪正则猜。删完还会自检残留引用。

接下来
──────
    python3 setpages.py out.html --extract   # 页面抽到 out.pages.html（几 KB）
    # 编辑 out.pages.html
    python3 setpages.py out.html             # 贴回去
    ./build.sh out.html                      # 字体 → PDF → 验收
"""

import pathlib
import re
import os
import sys

HERE = pathlib.Path(__file__).resolve().parent

def find_asset(name: str) -> pathlib.Path:
    """找模板 / 文档，兼容两种布局：

        _theme/                    ← 开发工作区（平铺）
            template.html
            parts.py
        slides/                    ← 打包后的 skill
            assets/template.html
            references/RULES.md
            scripts/parts.py       ← __file__ 在这里

    所以依次找：同目录 → 上一级的 assets/ → 上一级的 references/ → 同级的 assets/。
    **同一个脚本在两种布局下都能跑** —— 这样 _theme 和 skill 不会漂移。
    """
    for c in (HERE / name,
              HERE.parent / "assets" / name,
              HERE.parent / "references" / name,
              HERE / "assets" / name,
              HERE / "references" / name):
        if c.exists():
            return c
    raise SystemExit(
        f"✗ 找不到 {name}。\n"
        f"  找过：{HERE}/ · {HERE.parent}/assets/ · {HERE.parent}/references/\n"
        f"  这份脚本要在 _theme/ 里跑，或者在打包后的 slides/scripts/ 里跑。")

TPL = None   # 由 find_asset 在运行时定位（见下）

# 面板在文档里的边界：整段落在 </div><!-- /#stage --> 和 <div id="notes"> 之间。
# 按边界删而不是按标签匹配 —— 面板内部有多层嵌套 div，正则数不清楚。
MARK_A = "</div><!-- /#stage -->"
MARK_B = '<div id="notes">'

# 样式表里的面板块
CSS_RE = re.compile(r"\n/\* ══ 调参面板.*?#pbtn\.hide\{display:none\}\n", re.S)
# 脚本里的面板块
JS_RE = re.compile(r"\n  /\* ── 调参面板.*?(?=\n  window\.addEventListener\('resize',fit\);)", re.S)
# 逻辑里对面板元素的引用
REF_SUBS = [
    ("document.getElementById('panel').classList.remove('on'); "
     "document.getElementById('pbtn').classList.remove('hide'); ", ""),
    ("    if(k==='d'||k==='D'){ togglePanel(); e.preventDefault(); return; }\n", ""),
    ("    if(e.target.closest('#panel')||e.target.closest('#pbtn')) return;\n", ""),
    ("<div><kbd>d</kbd> 调参面板 &nbsp;·&nbsp; <kbd>f</kbd> 全屏 &nbsp;·&nbsp; <kbd>s</kbd> 备注</div>",
     "<div><kbd>f</kbd> 全屏 &nbsp;·&nbsp; <kbd>s</kbd> 备注 &nbsp;·&nbsp; <kbd>p</kbd> 导出 PDF</div>"),
]


TITLE_RE = re.compile(r"<title>.*?</title>", re.S)


def strip_dev(h: str) -> str:
    # ① 面板 HTML：按结构边界整段切掉
    a = h.index(MARK_A) + len(MARK_A)
    b = h.index(MARK_B)
    if "class=\"grp\"" not in h[a:b] and "opt-u" not in h[a:b]:
        raise SystemExit("✗ 面板 HTML 的边界对不上，template.html 结构变过了")
    h = h[:a] + "\n\n" + h[b:]

    # ② 面板样式
    h, n_css = CSS_RE.subn("\n", h, count=1)
    if n_css != 1:
        raise SystemExit("✗ 没找到面板样式块")

    # ③ 面板脚本
    h, n_js = JS_RE.subn("\n", h, count=1)
    if n_js != 1:
        raise SystemExit("✗ 没找到面板脚本块")

    # ④ 其余引用
    for a2, b2 in REF_SUBS:
        if a2 not in h:
            raise SystemExit(f"✗ 引用没找到（模板改过了？）：{a2[:60]!r}")
        h = h.replace(a2, b2, 1)

    # ⑤ 自检
    left = [k for k in ("id=\"panel\"", "id=\"pbtn\"", "togglePanel", "opt-sep",
                        "opt-u", "opt-dark", "hint-sep", "class=\"grp\"",
                        "#panel{", "#pbtn{") if k in h]
    if left:
        raise SystemExit(f"✗ 剥完还有残留：{left}")

    return h


def main():
    argv = sys.argv[1:]
    # --help / -h 要打帮助，**不能当成文件名** ——
    # 原来没拦，于是 `newdeck.py --help` 会生成一个叫 `--help` 的 deck 文件。
    # （冷启动测试里被 agent 踩到并报了出来。）
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0 if argv else 2)
    if argv[0].startswith("-"):
        sys.exit(f"✗ 不认识的选项：{argv[0]}\n\n{__doc__}")
    if len(argv) > 2:
        sys.exit(f"✗ 参数太多：{argv[2:]}\n\n{__doc__}")
    sys.argv = [sys.argv[0], *argv]
    out = pathlib.Path(sys.argv[1])
    # 第二个参数是 deck 的 <title>，不给就用文件名 ——
    # <title> 住在外壳里，起手设好就不用回头去碰外壳了。
    title = sys.argv[2] if len(sys.argv) == 3 else out.stem

    h = find_asset("template.html").read_text(encoding="utf-8")
    h = strip_dev(h)
    h = TITLE_RE.sub(lambda m: "<title>" + title.replace("&", "&amp;")
                     .replace("<", "&lt;").replace(">", "&gt;") + "</title>", h, count=1)

    # 页数太多时把模板展示页清空，留一个空壳让人往里写
    out.write_text(h, encoding="utf-8")
    n = h.count('<section class="slide')
    print(f"✓ {out}  {len(h)//1024} KB · 含模板展示页 {n} 页 · title: {title!r}")
    # 提示里给**相对当前目录的真实路径** —— 打包后脚本在 scripts/ 里，
    # 写 "python3 setpages.py" 只有在 scripts 目录里才对，会误导。
    def rel(name):
        try:
            return os.path.relpath(HERE / name, os.getcwd())
        except ValueError:
            return str(HERE / name)

    print("  → 接下来：")
    print(f"       {sys.executable} {rel('setpages.py')} {out} --extract   # 页面抽出来改")
    print(f"       {sys.executable} {rel('setpages.py')} {out}             # 改完贴回去")
    print(f"       {rel('build.sh')} {out}")
    print("     ↑ 用 build.sh，不要分开跑 build_font.py（见 FAILURES F4）")


if __name__ == "__main__":
    main()

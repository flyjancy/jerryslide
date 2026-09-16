#!/usr/bin/env python3
"""newdeck.py —— 从模板起一份生产 deck。

    ./scripts/newdeck.py out.html ["deck 的 <title>"]
    ./scripts/newdeck.py --help

复制 template.html 并把 <title> 设好。

**第二个参数是 deck 的 `<title>`**，不给就用文件名去掉扩展名。
`<title>` 住在外壳里（`#stage` 之外），起手就设好就不用回头去碰外壳 ——
冷启动测试里三个 agent 用三种不同办法去改它，其中一次忘了改。

为什么模板里不再有调参面板
──────────────────────────
模板自带一个「调参面板」（切分隔线 / u / 深浅），那是开发时看效果用的，
生产 deck 里必须剥掉。**而剥错是有代价的** —— 真实踩过：

    用非贪婪正则 `<div id="pbtn">.*?</div>` 删面板，
    `.*?` 只吃到第一个 `</div>`：**外壳删了、内容留在文档里**。
    更糟的是 JS 仍然引用已删的 #pbtn，抛 TypeError，
    **整个 IIFE 当场死掉** —— fit() 不跑、页码不填、深链接失效。
    而这一切看起来只像「样式没加载」。

上面就是当时删面板踩的坑。所以现在面板从 template.html 里**整个移走了**
（demo.html 保留自己的那份，面板在那里继续有用），模板就是生产外壳。
本脚本不再删任何东西 —— 只剩两件事：设 title、**自检模板没有面板残留**，
防止将来改模板时把它意外带回来。

接下来
──────
    ./scripts/setpages.py out.html --extract   # 页面抽到 out.pages.html（几 KB）
    # 编辑 out.pages.html
    ./scripts/setpages.py out.html             # 贴回去
    ./scripts/build.sh out.html                # 字体 → PDF → 验收
"""

import pathlib
import re
import os
import sys

HERE = pathlib.Path(__file__).resolve().parent

def find_asset(name: str) -> pathlib.Path:
    """找 skill 里的模板 / 文档。

        $SKILL/                    ← 本 skill 目录（SKILL.md 所在处）
            assets/template.html
            references/RULES.md
            scripts/parts.py       ← __file__ 在这里

    依次找：同目录 → 上一级的 assets/ → 上一级的 references/ → 同级的 assets/。
    这样不管从哪个目录调用脚本都能定位到 skill 内的文件。
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
        f"  这个文件应该在被安装的 skill 目录里（scripts/ 的上一级）。")

TPL = None   # 由 find_asset 在运行时定位（见下）

# 模板不该再含任何「开发面板」的痕迹 —— 出现就是模板回退了。
# 面板现在只住在 demo.html 里；生产模板必须是干净外壳。
FORBIDDEN = ('id="panel"', 'id="pbtn"', 'togglePanel', '#panel{', '#pbtn{',
             '--sep-w', 'class="grp"', 'opt-u', 'opt-dark', 'opt-sep',
             'hint-sep', '<kbd>d</kbd>')

TITLE_RE = re.compile(r"<title>.*?</title>", re.S)


def verify_clean(h: str) -> None:
    """模板必须是生产外壳，发现开发面板的痕迹就拒绝起 deck。

    这不是历史上的洁癖：面板的把手常驻屏幕左侧，屏幕共享时观众看得见
    （@media print 只救得了 PDF，救不了共享画面）。
    """
    left = [k for k in FORBIDDEN if k in h]
    if left:
        raise SystemExit(
            f"✗ template.html 里出现了开发面板的痕迹：{left}\n"
            f"  生产模板不该有调参面板（demo.html 才有）。"
            f"先把它移干净再起 deck。")


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
    verify_clean(h)
    h = TITLE_RE.sub(lambda m: "<title>" + title.replace("&", "&amp;")
                     .replace("<", "&lt;").replace(">", "&gt;") + "</title>", h, count=1)

    # 页数太多时把模板展示页清空，留一个空壳让人往里写
    out.write_text(h, encoding="utf-8")
    n = h.count('<section class="slide')
    print(f"✓ {out}  {len(h)//1024} KB · 含模板展示页 {n} 页 · title: {title!r}")
    # 提示里给**相对当前目录的真实路径** —— 安装后脚本在 scripts/ 里，
    # 写裸文件名 "setpages.py" 只有在 scripts 目录里才对，会误导。
    def rel(name):
        try:
            return os.path.relpath(HERE / name, os.getcwd())
        except ValueError:
            return str(HERE / name)

    print("  → 接下来：")
    print(f"       {sys.executable} {rel('setpages.py')} {out} --extract   # 页面抽出来改")
    print(f"       {sys.executable} {rel('setpages.py')} {out}             # 改完贴回去")
    print(f"       {rel('build.sh')} {out}")
    print("     ↑ 用 build.sh，不要分开跑 build_font.py（见 references/FAILURES.md F4）")


if __name__ == "__main__":
    main()

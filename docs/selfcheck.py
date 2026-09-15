#!/usr/bin/env python3
"""selfcheck.py —— 这个 skill 的自包含校验。

    python3 docs/selfcheck.py        # 退出码 0 = 全绿

检四件事：

  1. 布局     `SKILL.md` / `references/` / `scripts/` / `assets/` 必需文件齐
  2. 头部     `SKILL.md` 的 frontmatter 合法 —— pi 不认的话整个 skill 不加载
  3. 字体     `assets/` 必须是**剥过字体**的版本，否则 skill 胖 10 倍，
              而且「忘了重跑字体」这个坑会被掩盖
  4. 自包含   `SKILL.md` / `references/` / `scripts/` 里**不许引用 skill 之外的文件**

提交前由 `docs/hooks/pre-commit` 自动跑（启用方式见那个文件）。

为什么第 4 条必须机械化
────────────────────────
「skill 引用了一个外面的文件」是典型的**静默失败** ——
它在作者自己的机器上永远是对的，只有换台机器、换个用户、
或者把源目录挪走/删掉的时候才断。**靠记是记不住的，所以让它变成一条命令。**

同一类错误在这个仓库里真实发生过：`references/BUILD.md` 曾经硬编码
`cd "/Users/…/_theme"`，而这份文档正是打包进 skill、给使用者照着敲的。
把源工作区一删，照着敲的人就当场卡住 —— 而作者永远看不到。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# 本脚本住在 <skill>/docs/ 里
HERE = Path(__file__).resolve().parent
SKILL = HERE.parent

REFERENCES = ["RULES.md", "BUILD.md", "FAILURES.md"]
SCRIPTS = ["build.sh", "check.py", "build_font.py", "newdeck.py", "setpages.py", "parts.py"]
ASSETS = ["template.html", "demo.html"]

FONT_MARK = "@font-face{font-family:Slide"

# ── 自包含检查 ────────────────────────────────────────────────────────
# 这些字样出现在会被加载的文件里，就说明它指向了 skill 之外的东西。
# 宿主机环境（Chrome 路径、~/.cache/…、/tmp/…）**不在**此列 —— 那是环境，不是 skill 的文件。
FORBIDDEN = [
    (re.compile(r"_theme"),            "指向开发工作区 —— 不属于 skill"),
    (re.compile(r"Share-AI-tools"),    "指向本机绝对路径"),
    (re.compile(r"/Users/"),           "指向本机绝对路径"),
    (re.compile(r"\bPLAN\.md\b"),      "开发档案，不在 skill 里"),
    (re.compile(r"\bPACKAGING\.md\b"), "开发档案，不在 skill 里"),
    (re.compile(r"SKILL-NOTES"),       "开发档案，不在 skill 里"),
    (re.compile(r"_旧版"),             "指向 skill 之外的旧文件"),
]


class Fail(Exception):
    pass


def scanned_files() -> list[Path]:
    """会被 π 加载 / 被 agent 按相对路径使用的文件 —— 自包含检查扫的就是这些。

    不含 `README.md` 和 `docs/`：它们是给开发者的，π 不加载。
    """
    files = [SKILL / "SKILL.md"]
    files += [SKILL / "references" / f for f in REFERENCES]
    files += [SKILL / "scripts" / f for f in SCRIPTS]
    return [f for f in files if f.is_file()]


def check_layout() -> None:
    missing = []
    if not (SKILL / "SKILL.md").is_file():
        missing.append("SKILL.md")
    for f in REFERENCES:
        if not (SKILL / "references" / f).is_file():
            missing.append(f"references/{f}")
    for f in SCRIPTS:
        if not (SKILL / "scripts" / f).is_file():
            missing.append(f"scripts/{f}")
    for f in ASSETS:
        if not (SKILL / "assets" / f).is_file():
            missing.append(f"assets/{f}")
    if not (SKILL / "assets" / "tests").is_dir():
        missing.append("assets/tests/")
    if missing:
        raise Fail(f"缺文件：{missing}")
    print("  ✓ 布局         SKILL.md · references/ · scripts/ · assets/")


def check_frontmatter() -> str:
    """最少限度地校验 SKILL.md 的 frontmatter —— pi 不认的话会整个不加载。"""
    t = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    if not t.startswith("---\n"):
        raise Fail("SKILL.md 没有 frontmatter")
    end = t.index("\n---", 4)
    fm = t[4:end]
    name = re.search(r"^name:\s*(.+)$", fm, re.M)
    desc = re.search(r"^description:\s*>\s*\n((?:[ \t]+.+\n)+)", fm, re.M)
    desc_inline = re.search(r"^description:\s*(?!>)(.+)$", fm, re.M)
    d = (desc.group(1) if desc else (desc_inline.group(1) if desc_inline else "")) or ""
    if not name:
        raise Fail("SKILL.md 缺 name")
    n = name.group(1).strip()
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", n) or len(n) > 64:
        raise Fail(f"name 不合法：{n!r}（只能小写字母数字和连字符）")
    if not d.strip():
        raise Fail("SKILL.md 缺 description —— pi 不会加载没有 description 的 skill")
    if len(d) > 1024:
        raise Fail(f"description 超长：{len(d)} > 1024")
    print(f"  ✓ 头部         name={n!r} · description {len(d)} 字符")
    return n


def check_fonts_stripped() -> None:
    """assets 必须是剥过字体的版本 —— 带字体的 template.html 是 441 KB。"""
    for f in ASSETS:
        if FONT_MARK in (SKILL / "assets" / f).read_text(encoding="utf-8"):
            raise Fail(f"assets/{f} 里还有内联字体 —— 它必须是剥过的版本")
    print(f"  ✓ 字体已剥     {', '.join(ASSETS)}")


def check_self_contained() -> None:
    """不许引用 skill 之外的文件。这条是机械检查，不靠记。"""
    files = scanned_files()
    bad = []
    for p in files:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            # 同一行可能同时命中多条规则，只报一次
            hits = list(dict.fromkeys(why for rx, why in FORBIDDEN if rx.search(line)))
            if hits:
                bad.append((p.relative_to(SKILL), i, "；".join(hits), line.strip()[:70]))
    if bad:
        print("\n  ✗ 发现指向 skill 之外的引用：")
        for rel, i, why, text in bad:
            print(f"      {rel}:{i}  [{why}]  {text}")
        raise Fail(f"{len(bad)} 行外部引用 —— skill 必须能整个搬走")
    print(f"  ✓ 自包含       {len(files)} 个文件里没有任何外部引用")


def main() -> int:
    print("  skill 自检")
    try:
        check_layout()
        check_frontmatter()
        check_fonts_stripped()
        check_self_contained()
    except Fail as e:
        sys.stdout.flush()          # 别让 stderr 抢在缓冲的 stdout 前面
        print(f"\n  ✗ {e}", file=sys.stderr)
        return 1
    print("\n  ✓ 全绿。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

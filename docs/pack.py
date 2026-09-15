#!/usr/bin/env python3
"""pack.py —— 验证并安装这个 skill。

    python3 docs/pack.py [--out ~/.pi/agent/skills/jerryslide] [--dry-run]

读什么
──────
只读**本脚本所在目录的上一级** —— 也就是这个 skill 的根目录（含 `SKILL.md` 的那个）。
不读、也不需要任何 skill 之外的文件。

    <skill>/                     ← 唯一输入
      SKILL.md
      references/  scripts/  assets/
      docs/                      ← 本脚本住在这里（不装）

它做的事
────────
  1. 校验 `SKILL.md` 的 frontmatter —— pi 不认的话整个 skill 不加载
  2. 校验必需文件齐（`references/` `scripts/` `assets/`）
  3. 校验 `assets/` 是**剥过字体的版本** —— 带字体的 `template.html` 是 441 KB，
     装进 skill 又重、又会掩盖「忘了重跑字体」这个坑
  4. **校验自包含** —— `SKILL.md` / `references/` / `scripts/` 里不许出现指向
     skill 之外的文件引用（`_theme/`、本机绝对路径、开发档案名…）。
     **skill 必须能整个搬走**，换台机器、换个用户一样能跑
  5. 装到 `--out`
  6. 自证：在**装出来的那一份**上跑 setpages / check / parts 的自检

为什么第 4 步值得机械化
────────────────────────
「skill 引用了一个外面的文件」是典型的**静默失败** ——
它在作者自己的机器上永远是对的，只有换台机器、换个用户、
或者把源目录挪走/删掉的时候才断。**靠记是记不住的，所以让它变成一条命令。**
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # <skill>/docs/
SRC = HERE.parent                               # <skill>/ —— 唯一输入
DEFAULT_OUT = Path.home() / ".pi" / "agent" / "skills" / "jerryslide"

REFERENCES = ["RULES.md", "BUILD.md", "FAILURES.md"]
SCRIPTS = ["build.sh", "check.py", "build_font.py", "newdeck.py", "setpages.py", "parts.py"]
ASSETS = ["template.html", "demo.html"]
EXECUTABLES = ["build.sh", "newdeck.py", "setpages.py", "parts.py", "check.py", "build_font.py"]

# 只装这些；其余（docs/、README.md、.gitignore…）留在仓库里，不进 skill
NOT_INSTALLED = ["docs/", "README.md", ".gitignore", ".git/"]

FONT_MARK = "@font-face{font-family:Slide"

# ── 自包含检查 ────────────────────────────────────────────────────────
# 这些字样出现在会被安装的文件里，就说明它指向了 skill 之外的东西。
# 宿主机环境（Chrome 路径、~/.cache/…、/tmp/…）**不在**此列 —— 那是环境，不是 skill 的文件。
FORBIDDEN = [
    (re.compile(r"_theme"),          "指向开发工作区 —— 不属于 skill"),
    (re.compile(r"Share-AI-tools"),  "指向本机绝对路径"),
    (re.compile(r"/Users/"),         "指向本机绝对路径"),
    (re.compile(r"\bPLAN\.md\b"),    "开发档案，不在 skill 里"),
    (re.compile(r"\bPACKAGING\.md\b"), "开发档案，不在 skill 里"),
    (re.compile(r"SKILL-NOTES"),     "开发档案，不在 skill 里"),
    (re.compile(r"_旧版"),           "指向 skill 之外的旧文件"),
]


class Fail(Exception):
    pass


def scan_files() -> list[Path]:
    """会被安装的文件 —— 自包含检查扫的就是这些。"""
    files = [SRC / "SKILL.md"]
    files += [SRC / "references" / f for f in REFERENCES]
    files += [SRC / "scripts" / f for f in SCRIPTS]
    return [f for f in files if f.is_file()]


def check_frontmatter(p: Path) -> dict:
    """最少限度地校验 SKILL.md 的 frontmatter —— pi 不认的话会整个不加载。"""
    t = p.read_text(encoding="utf-8")
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
    return {"name": n, "desc_chars": len(d)}


def check_layout() -> None:
    missing = [str(f) for f in (SRC / "SKILL.md",) if not f.is_file()]
    for f in REFERENCES:
        if not (SRC / "references" / f).is_file():
            missing.append(f"references/{f}")
    for f in SCRIPTS:
        if not (SRC / "scripts" / f).is_file():
            missing.append(f"scripts/{f}")
    for f in ASSETS:
        if not (SRC / "assets" / f).is_file():
            missing.append(f"assets/{f}")
    if not (SRC / "assets" / "tests").is_dir():
        missing.append("assets/tests/")
    if missing:
        raise Fail(f"skill 里缺：{missing}")


def check_fonts_stripped() -> None:
    """assets 必须是剥过字体的版本 —— 否则装出来的 skill 会胖 10 倍，
    而且「忘了重跑字体」这个坑会被掩盖。"""
    for f in ASSETS:
        h = (SRC / "assets" / f).read_text(encoding="utf-8")
        if FONT_MARK in h:
            raise Fail(f"assets/{f} 里还有内联字体 —— 它必须是剥过的版本")


def check_self_contained() -> None:
    """不许引用 skill 之外的文件。这条是**机械检查**，不靠记。"""
    bad = []
    for p in scan_files():
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for rx, why in FORBIDDEN:
                if rx.search(line):
                    bad.append((p.relative_to(SRC), i, why, line.strip()[:70]))
    if bad:
        print("\n  ✗ 发现指向 skill 之外的引用：")
        for rel, i, why, text in bad:
            print(f"      {rel}:{i}  [{why}]  {text}")
        raise Fail(f"{len(bad)} 处外部引用 —— skill 必须能整个搬走")
    print(f"  ✓ 自包含        {len(scan_files())} 个文件里没有任何外部引用")


def install(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    for d in ("references", "scripts", "assets"):
        (out / d).mkdir(parents=True)

    shutil.copy2(SRC / "SKILL.md", out / "SKILL.md")
    print("  ✓ SKILL.md")

    for f in REFERENCES:
        shutil.copy2(SRC / "references" / f, out / "references" / f)
    print(f"  ✓ references/   {' · '.join(REFERENCES)}")

    for f in SCRIPTS:
        shutil.copy2(SRC / "scripts" / f, out / "scripts" / f)
    for f in EXECUTABLES:
        (out / "scripts" / f).chmod(0o755)
    print(f"  ✓ scripts/      {len(SCRIPTS)} 个")

    for f in ASSETS:
        shutil.copy2(SRC / "assets" / f, out / "assets" / f)
    shutil.copytree(SRC / "assets" / "tests", out / "assets" / "tests")
    print(f"  ✓ assets/       {' · '.join(ASSETS)} · tests/")


def self_test(out: Path) -> None:
    print("\n  自证：在装出来的那一份上跑")
    tests = [
        ("scripts/setpages.py", ["--self-test"], "10/10"),
        ("scripts/check.py",    ["--self-test"], "自检全部符合预期"),
    ]
    for rel, args, want in tests:
        r = subprocess.run([sys.executable, str(out / rel), *args],
                           capture_output=True, text=True, cwd=out / "scripts")
        got = (r.stdout + r.stderr).strip().split("\n")[-1].strip()
        ok = want in got
        print(f"    {'✓' if ok else '✗'} {rel:<22} {got[:60]}")
        if not ok:
            raise Fail(f"{rel} 装完跑不过")

    r = subprocess.run([sys.executable, str(out / "scripts" / "parts.py"), "--summary"],
                       capture_output=True, text=True, cwd=out / "scripts")
    got = (r.stdout + r.stderr).strip()
    ok = r.returncode == 0 and "失败 0" in got
    print(f"    {'✓' if ok else '✗'} scripts/parts.py        {got[:60]}")
    if not ok:
        raise Fail("parts.py 在 skill 布局下跑不过 —— find_asset 没覆盖到 assets/")


def report(out: Path) -> None:
    leaked = [str(p.relative_to(out)) for p in out.rglob("*")
              if p.is_file() and (p.name in ("PLAN.md", "SKILL-NOTES.md", "PACKAGING.md")
                                  or "docs" in p.relative_to(out).parts)]
    if leaked:
        raise Fail(f"不该进 skill 的东西进去了：{leaked}")
    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    n = sum(1 for p in out.rglob("*") if p.is_file())
    print(f"\n  ✓ 没进 skill 的： {', '.join(NOT_INSTALLED)}")
    print(f"  ✓ 共 {n} 个文件 · {total/1024:.0f} KB")


def main() -> int:
    ap = argparse.ArgumentParser(prog="docs/pack.py", description="验证并安装这个 skill")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help=f"目标目录（默认 {DEFAULT_OUT}）")
    ap.add_argument("--dry-run", action="store_true", help="只校验，不写任何文件")
    a = ap.parse_args()
    out = Path(a.out).expanduser()

    try:
        print(f"\n  源：{SRC}")

        check_layout()
        print("  ✓ 文件齐        SKILL.md · references/ · scripts/ · assets/")

        meta = check_frontmatter(SRC / "SKILL.md")
        print(f"  ✓ SKILL.md      name={meta['name']!r} · description {meta['desc_chars']} 字符")

        check_fonts_stripped()
        print(f"  ✓ 字体已剥      {', '.join(ASSETS)}")

        check_self_contained()

        if a.dry_run:
            print("\n  （--dry-run：没有写任何文件）")
            return 0

        print(f"\n  装到 → {out}\n")
        install(out)
        self_test(out)
        report(out)
        print(f"\n  装好了。用 `/skill:{meta['name']}` 唤起。")
        return 0

    except Fail as e:
        print(f"\n  ✗ {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""pack.py —— 把 `_theme/` 组装成 pi skill。

    python3 pack.py [--out ~/.pi/agent/skills/slides] [--dry-run]

为什么要这个脚本而不是手工拷
────────────────────────────
1. **`assets/` 里的模板必须剥掉内联字体。** 带字体的 `template.html` 是 441 KB，
   其中 400 KB 是 base64 —— 放进 skill 又重，又会掩盖「忘了重跑字体」这个坑。
   剥掉之后：**不跑 `build_font.py` 就出不了 PDF**，从机制上不可能忘记。
2. **开发档案不能进 skill。** `PLAN.md` / `SKILL-NOTES.md` / `PACKAGING.md` 是
   给「做这套东西的人」看的，不是给「用它做 deck 的人」看的。
3. **打包完要能自证。** 脚本最后会跑 `check.py --self-test`、`setpages.py --self-test`、
   `parts.py`，确认装出来的东西是活的。

布局对照
────────
    _theme/                       →   jerryslide/
      SKILL.md                          SKILL.md
      RULES.md BUILD.md FAILURES.md  →  references/
      *.py *.sh                      →  scripts/
      template.html demo.html tests/ →  assets/
      PLAN.md SKILL-NOTES.md PACKAGING.md  ✗ 不进
      template.pages.html                  ✗ 开发中间产物，不进
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = Path.home() / ".pi" / "agent" / "skills" / "jerryslide"

REFERENCES = ["RULES.md", "BUILD.md", "FAILURES.md"]
SCRIPTS = ["build.sh", "check.py", "build_font.py", "newdeck.py", "setpages.py", "parts.py"]
ASSETS = ["template.html", "demo.html"]
NOT_SHIPPED = ["PLAN.md", "SKILL-NOTES.md", "PACKAGING.md", "template.pages.html"]

FONT_FACE = re.compile(r"@font-face\{font-family:Slide[^}]*\}")


class Fail(Exception):
    pass


def strip_fonts(src: Path, dst: Path) -> tuple[int, int]:
    """剥掉内联 @font-face，返回 (原字节, 新字节)。"""
    h = src.read_text(encoding="utf-8")
    if "@font-face{font-family:Slide" not in h:
        raise Fail(f"{src.name} 里没有内联字体 —— 它本来就是剥过的？")
    out = FONT_FACE.sub("", h)
    if "@font-face{font-family:Slide" in out:
        raise Fail(f"{src.name} 剥完还有残留 @font-face")
    # 剥完的 HTML 必须还是一份能解析的文档
    for must in ('<div id="stage">', "</div><!-- /#stage -->", "<div id=\"notes\">"):
        if must not in out:
            raise Fail(f"{src.name} 剥完缺了 {must!r} —— 正则吃多了")
    dst.write_text(out, encoding="utf-8")
    return len(h.encode()), len(out.encode())


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
    if "disable-model-invocation: true" in fm:
        d += "  [disable-model-invocation: true —— 只能用 /skill:%s 唤起]" % n
    return {"name": n, "desc_chars": len(d)}


def main() -> int:
    ap = argparse.ArgumentParser(prog="pack.py", description="把 _theme/ 组装成 pi skill")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help=f"目标目录（默认 {DEFAULT_OUT}）")
    ap.add_argument("--dry-run", action="store_true", help="只说会做什么，不写")
    a = ap.parse_args()
    out = Path(a.out).expanduser()

    try:
        print(f"  打包 → {out}\n")

        # ── 0 · 源文件齐不齐 ──────────────────────────────────────
        missing = [f for f in REFERENCES + SCRIPTS + ASSETS + ["SKILL.md"]
                   if not (HERE / f).exists()]
        if missing:
            raise Fail(f"_theme/ 里缺：{missing}")
        if not (HERE / "tests").is_dir():
            raise Fail("_theme/tests/ 不存在")

        meta = check_frontmatter(HERE / "SKILL.md")
        print(f"  ✓ SKILL.md      name={meta['name']!r} · description {meta['desc_chars']} 字符")

        if a.dry_run:
            print("\n  （--dry-run：没有写任何文件）")
            return 0

        # ── 1 · 目录骨架 ─────────────────────────────────────────
        if out.exists():
            shutil.rmtree(out)
        for d in ("references", "scripts", "assets"):
            (out / d).mkdir(parents=True)

        # ── 2 · SKILL.md ─────────────────────────────────────────
        shutil.copy2(HERE / "SKILL.md", out / "SKILL.md")
        print(f"  ✓ SKILL.md")

        # ── 3 · references/ ──────────────────────────────────────
        for f in REFERENCES:
            shutil.copy2(HERE / f, out / "references" / f)
        print(f"  ✓ references/   {' · '.join(REFERENCES)}")

        # ── 4 · scripts/ ─────────────────────────────────────────
        for f in SCRIPTS:
            shutil.copy2(HERE / f, out / "scripts" / f)
        (out / "scripts" / "build.sh").chmod(0o755)
        for f in ("newdeck.py", "setpages.py", "parts.py", "check.py", "build_font.py"):
            (out / "scripts" / f).chmod(0o755)
        print(f"  ✓ scripts/      {len(SCRIPTS)} 个")

        # ── 5 · assets/（剥字体）─────────────────────────────────
        for f in ASSETS:
            before, after = strip_fonts(HERE / f, out / "assets" / f)
            print(f"  ✓ assets/{f:<14} {before/1024:6.0f} KB → {after/1024:5.0f} KB（剥掉内联字体）")
        shutil.copytree(HERE / "tests", out / "assets" / "tests")

        # ── 6 · 自证：装出来的东西是活的 ─────────────────────────
        print("\n  自证：")
        tests = [
            (["--self-test"], "scripts/setpages.py", "10/10"),
            (["--self-test"], "scripts/check.py", "自检全部符合预期"),
        ]
        for args, rel, want in tests:
            r = subprocess.run([sys.executable, str(out / rel), *args],
                               capture_output=True, text=True, cwd=out / "scripts")
            got = (r.stdout + r.stderr).strip().split("\n")[-1].strip()
            ok = want in got
            print(f"  {'✓' if ok else '✗'} {rel:<22} {got[:60]}")
            if not ok:
                raise Fail(f"{rel} 装完跑不过")

        r = subprocess.run([sys.executable, str(out / "scripts" / "parts.py"), "--summary"],
                           capture_output=True, text=True, cwd=out / "scripts")
        got = (r.stdout + r.stderr).strip()
        ok = r.returncode == 0 and "失败 0" in got
        print(f"  {'✓' if ok else '✗'} scripts/parts.py        {got[:60]}")
        if not ok:
            raise Fail("parts.py 在 skill 布局下跑不过 —— find_asset 没覆盖到 assets/")

        # ── 7 · 不该进去的东西确实没进去 ─────────────────────────
        leaked = [f for f in NOT_SHIPPED if (out / f).exists()]
        leaked += [str(p.relative_to(out)) for p in out.rglob("*")
                   if p.is_file() and p.name in NOT_SHIPPED]
        if leaked:
            raise Fail(f"开发档案漏进 skill 了：{leaked}")
        total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
        n = sum(1 for p in out.rglob("*") if p.is_file())
        print(f"\n  ✓ 开发档案没漏进来（{', '.join(NOT_SHIPPED)}）")
        print(f"  ✓ 共 {n} 个文件 · {total/1024:.0f} KB")
        print(f"\n  装好了。用 `/skill:{meta['name']}` 唤起。")
        return 0

    except Fail as e:
        print(f"\n  ✗ {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

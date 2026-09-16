#!/usr/bin/env python3
"""parts.py — 零件清单：从 CSS 生成，不手写。

为什么需要它
────────────
手写的零件清单一定会漂移。实测（四次冷启动测试）：

| 零件 | CSS 定义 | 模板示范 | RULES 提到 |
|---|---|---|---|
| `.rows.tight` | ✓ | **✗ 11 页里一次都没有** | ✓ |
| `.caveat` | ✓ | 1 次（半藏着） | **✗** |

**RULES 只点名 22 个类，CSS 里有 60 多个。** 于是四个 agent 全都靠 `grep` 去 CSS 里翻零件 ——
而**定义了却没有一页示范的零件，等于不存在**（没人想得到用它）。

三样东西对齐，才算零件库：

    定义（CSS）  ×  示范（哪几页用了）  ×  文档（RULES 里有没有）

零件是**选择器**，不是类名。`.row .k` 里的 `.k` 不是零件（它必须待在 `.row` 里），
`.rows.tight` 里的 `.tight` 也不是（它是变体，必须跟着 `.rows`）。这个脚本按选择器
的形状分成四类：

| 类型 | 长什么样 | 例子 |
|---|---|---|
| **顶级** | `.foo` | `.rows` `.caveat` `.cards` |
| **变体** | `.Base.foo` | `.row.hi` `.card.acc` `.rows.tight` |
| **子元素** | `.Base .foo` | `.row .k` `.ti .tn` `.lnode .ln` |
| **界面** | 选择器里有 `#id` | `#notes` `#help .box`（放映器，作者不写） |

描述从 **CSS 注释**里取（紧挨在规则前面的那一条）—— 所以它跟定义在一起，不会漂移。

用法
────
    ./scripts/parts.py                 # 体检：只列有问题的
    ./scripts/parts.py --list          # 完整清单
    ./scripts/parts.py --emit          # 重新生成 references/RULES.md 里的零件表
    ./scripts/parts.py --check         # 有漂移就退出码 1（给 build.sh 用）

references/RULES.md 里被生成的段落夹在这两个标记之间 —— **不要手改那一段**：

    <!-- BEGIN PARTS -->
    <!-- END PARTS -->
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

def find_asset(name: str) -> Path:
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

RULES = None   # 由 find_asset 定位
NEWDECK = HERE / "newdeck.py"

BEGIN = "<!-- BEGIN PARTS -->"
END = "<!-- END PARTS -->"

CLASS_RE = re.compile(r"\.([a-z][a-z0-9-]*)(?![a-zA-Z0-9_-])")


class Fail(Exception):
    pass


# ══════════════════════════════════════════════════════════════════════
#  CSS 解析
# ══════════════════════════════════════════════════════════════════════

def strip_comments(css: str) -> str:
    """把注释换成等长空白 —— 保住偏移量，行号才准。"""
    def rep(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    return re.sub(r"/\*.*?\*/", rep, css, flags=re.S)


def strip_at_blocks(css: str) -> str:
    """去掉 @media / @supports 的整块（连同花括号），避免嵌套打乱规则解析。"""
    out, i = [], 0
    while i < len(css):
        m = re.compile(r"@[\w-]+[^{;]*\{").search(css, i)
        if not m:
            out.append(css[i:])
            break
        out.append(css[i:m.start()])
        j, depth = m.end(), 1
        while j < len(css) and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        # ⚠️ 必须**等长**填充（换行保留换行，其余换空格）。
        # 早先写成 "\n" * 行数 —— 偏移量全部错位，于是 _desc_for 从错位的
        # 位置去原始 css 里找注释，整张表「用途」列全空。
        out.append("".join("\n" if ch == "\n" else " " for ch in css[m.start():j]))
        i = j
    return "".join(out)


def _clean_comment(body: str) -> str:
    """注释正文 → 一行说明。"""
    lines = [l.strip(" \t*·═─—-") for l in body.split("\n")]
    lines = [l for l in lines if l and set(l) - set("═─— -")]
    if not lines:
        return ""
    txt = lines[0]
    for pre in ("原型 · ", "零件 · ", "SLIDE DESIGN SYSTEM · ", "SLIDE SYSTEM · "):
        if txt.startswith(pre):
            txt = txt[len(pre):]
    return txt[:56]


def _leading_desc(css: str, sel_start: int) -> str:
    """选择器**前面**紧贴着的那条注释。只在中间只有空白时才算。"""
    m = re.search(r"/\*((?:(?!\*/).)*?)\*/([ \t\r\n]*)$", css[:sel_start], re.S)
    return _clean_comment(m.group(1)) if m else ""


def _trailing_desc(css: str, rule_end: int) -> str:
    """规则 `}` **同一行**后面的那条注释。

    ⚠️ 模板里 `①②③④⑤` 是这么写的：

        .ttl{…}                     /* ① 标题行 */
        .ttl .bar{…}

    它贴在 .ttl 自己的收尾大括号那一行，**不是** .ttl .bar 的前导注释。
    只认前导注释会把它判给下一条，说明全部错位一行。
    """
    m = re.match(r"[ \t]*/\*((?:(?!\*/).)*?)\*/[ \t]*(?=\n|$)", css[rule_end:], re.S)
    return _clean_comment(m.group(1)) if m else ""


def parse_rules(css: str) -> list[dict]:
    """[{sel, line, desc}] —— 每个选择器一条（逗号拆开）。

    注释归属分两趟，因为模板里两种写法都有：

        .ttl{…}                     /* ① 标题行 */    ← 行尾注释，属于 .ttl
        /* ── 编号条目表 ── */
        .rows{…}                                     ← 前导注释，属于 .rows

    单趟做必然错：前导注释那一趟会把上一行的行尾注释也算进来。
    所以**先认领行尾的**（更具体），前导的不能再用已被认领的。
    """
    naked = strip_at_blocks(strip_comments(css))
    raw = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", naked):
        sel_raw = m.group(1).strip()
        if not sel_raw or "@" in sel_raw:
            continue
        lead_ws = len(m.group(1)) - len(m.group(1).lstrip())
        raw.append({"start": m.start(), "end": m.end(),
                    "sel_start": m.start() + lead_ws, "sel": sel_raw})

    # ① 行尾注释（规则 } 同一行后面那条）
    for r in raw:
        r["desc"] = _trailing_desc(css, r["end"])
    claimed = {r["desc"] for r in raw if r["desc"]}

    # ② 前导注释 —— 只收没被行尾认领过的
    for r in raw:
        if r["desc"]:
            continue
        d = _leading_desc(css, r["sel_start"])
        r["desc"] = "" if d in claimed else d

    out = []
    for r in raw:
        line = naked[: r["start"]].count("\n") + 1
        for one in r["sel"].split(","):
            one = " ".join(one.split())
            if one:
                out.append({"sel": one, "line": line, "desc": r["desc"]})
    return out


# ══════════════════════════════════════════════════════════════════════
#  类 → 形状
# ══════════════════════════════════════════════════════════════════════

def classify(rules: list[dict]) -> dict[str, dict]:
    """{类名: {kind, base, line, desc, sels}}"""
    info: dict[str, dict] = {}

    def touch(c):
        return info.setdefault(c, {"bare": False, "variants": set(), "children": set(),
                                   "context": set(), "ui": False, "line": 10**9,
                                   "desc": "", "sels": []})

    for r in rules:
        sel, line, desc = r["sel"], r["line"], r["desc"]
        if "#" in sel:
            for c in CLASS_RE.findall(sel):
                d = touch(c); d["ui"] = True
            continue
        compounds = sel.split()
        for ci, comp in enumerate(compounds):
            classes = CLASS_RE.findall(comp)
            if not classes:
                continue
            last = ci == len(compounds) - 1
            if last and len(classes) == 1 and len(compounds) == 1:
                d = touch(classes[0]); d["bare"] = True
            else:
                for k, c in enumerate(classes):
                    d = touch(c)
                    if k > 0:                       # .Base.C —— 变体
                        d["variants"].add(classes[0])
                    elif not last:                  # 祖先段：它是后代们的「修饰」
                        head = CLASS_RE.findall(compounds[0])
                        if head:
                            d["context"].add(head[0])
                    elif len(compounds) > 1:        # 后代段里的基类
                        head = CLASS_RE.findall(compounds[0])
                        if head:
                            d["children"].add(head[0])
            for c in classes:
                d = touch(c)
                if line < d["line"]:
                    d["line"] = line
                if desc and not d["desc"]:
                    d["desc"] = desc
                if len(d["sels"]) < 4:
                    d["sels"].append(sel)
    for c, d in info.items():
        if d["ui"]:
            d["kind"] = "界面"
        elif d["bare"]:
            d["kind"] = "顶级"
        elif d["variants"]:
            d["kind"] = "变体"
        elif d["children"]:
            d["kind"] = "子元素"
        elif d["context"]:
            # 只作为「祖先」出现过，比如 .end .args .arg —— 写在 <section> 上，
            # 自己不画任何东西，作用是切换后代的样子。
            d["kind"] = "修饰"
        else:
            d["kind"] = "子元素"
        d["base"] = sorted(d["variants"] or d["children"])[0] if (d["variants"] or d["children"]) else ""
    return info


# ══════════════════════════════════════════════════════════════════════
#  页面用法
# ══════════════════════════════════════════════════════════════════════

def page_usage(src: str) -> dict[str, list[int]]:
    stage = re.search(r'<div id="stage">(.*?)</div><!-- /#stage -->', src, re.S)
    if not stage:
        raise Fail("找不到 #stage —— 这不是这套模板的产物")
    out: dict[str, list[int]] = {}
    n = 0
    for sec in re.split(r"(?=<section\b)", stage.group(1)):
        if "<section" not in sec:
            continue
        n += 1
        end = sec.find("</section>")
        sec = sec[: end + 10] if end > 0 else sec
        for m in re.finditer(r'class="([^"]*)"', sec):
            for c in m.group(1).split():
                out.setdefault(c, [])
                if n not in out[c]:
                    out[c].append(n)
    return out


def js_classes(src: str) -> set[str]:
    used = set(re.findall(r"""classList\.\w+\(['"]([\w-]+)""", src))
    used |= set(re.findall(r"""querySelector(?:All)?\(['"][^'"]*?\.([a-z][\w-]*)""", src))
    return used


def stripped_template() -> str:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "t.html"
        r = subprocess.run([sys.executable, str(NEWDECK), str(out)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise Fail(f"newdeck.py 失败：{r.stderr.strip()[:300]}")
        return out.read_text(encoding="utf-8")


def documented() -> set[str]:
    if not RULES or not RULES.exists():
        return set()
    t = RULES.read_text(encoding="utf-8")
    t = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), "", t, flags=re.S)
    return set(CLASS_RE.findall(t))


def survey() -> dict:
    src = stripped_template()
    m = re.search(r"<style>(.*?)</style>", src, re.S)
    css = m.group(1)
    # 行号要对上文件，不是对上 <style> 里的相对行 —— 不然表里写的 L 值找不到东西
    offset = src[: m.start(1)].count("\n")
    info = classify(parse_rules(css))
    pages = page_usage(src)
    js = js_classes(src)
    doc = documented()

    file_lines = src.split("\n")

    def snap(cls: str, line: int) -> int:
        """把行号校正到「这一行里真的有这个类」的那一行。

        偏移量算法差一行是常事（<style> 后面有没有换行、@font-face 怎么折行都会影响），
        与其算准，不如**自验证**：在 ±4 行里找第一条含这个类的规则行。
        找不到就原样返回 —— 不假装知道。
        """
        pat = re.compile(r"\." + re.escape(cls) + r"(?![a-zA-Z0-9_-])")
        for off in (0, -1, 1, -2, 2, -3, 3, -4, 4, -5, 5, -6, 6, -8, 8, -10, 10):
            i = line - 1 + off
            if 0 <= i < len(file_lines) and pat.search(file_lines[i]):
                return i + 1
        return 0            # 校正不到就留空 —— 编一个假行号比没有更坏

    rows = []
    for c, d in info.items():
        used = pages.get(c, [])
        rows.append({
            "cls": c, "kind": d["kind"], "base": d["base"],
            "line": snap(c, d["line"] + offset),
            "desc": d["desc"], "pages": used, "in_js": c in js, "doc": c in doc,
            "sel": d["sels"][0] if d["sels"] else "",
        })
    rows.sort(key=lambda r: ({"顶级": 0, "修饰": 1, "变体": 2, "子元素": 3, "界面": 4}[r["kind"]], r["line"]))
    return {"rows": rows, "undefined": sorted(set(pages) - set(info))}


def author_parts(s: dict) -> list[dict]:
    """给作者用的零件（去掉放映器 / 界面）。"""
    return [r for r in s["rows"] if r["kind"] != "界面" and not r["in_js"]]


def diagnose(s: dict) -> tuple[list[str], list[str]]:
    """返回 (失败, 警告)。

    **失败** = 机械的、无歧义的：
      · 定义了但没有任何一页示范 → 这个零件没人发现得了，等于不存在
      · 页面在用但 CSS 没定义        → 渲染出来是裸文字，页面上看不出来是「漏了」

    **警告** = 判断题：RULES 正文没提到。
      生成的表已经把每个零件列出来了，所以这不是「查不到」，而是「没有使用指引」。
      它是待办清单，不是闸门 —— 机械检查说不出「这个零件该在什么时候用」。
    """
    fail, warn = [], []
    for r in author_parts(s):
        if r["kind"] == "子元素":
            continue                        # 子元素跟着父级出现，不单独示范
        if not r["pages"]:
            fail.append(f"`{r['cls']}`（L{r['line']}，{r['kind']}）"
                        f"定义了，但**没有任何一页示范** —— 没人发现得了它")
    for c in s["undefined"]:
        fail.append(f"`{c}` 页面在用，**CSS 里没有定义**")
    for r in author_parts(s):
        if r["kind"] == "子元素":
            continue
        if not r["doc"]:
            warn.append(f"`{r['cls']}`（L{r['line']}）RULES 正文没提")
    return fail, warn


# ══════════════════════════════════════════════════════════════════════
#  生成
# ══════════════════════════════════════════════════════════════════════

def _L(n: int) -> str:
    return f"L{n}" if n else "—"


def emit_table(s: dict) -> str:
    parts = author_parts(s)
    top = [r for r in parts if r["kind"] == "顶级"]
    var = [r for r in parts if r["kind"] == "变体"]
    mod = [r for r in parts if r["kind"] == "修饰"]
    kid = [r for r in parts if r["kind"] == "子元素"]
    out = [BEGIN,
           "<!-- 由 `./scripts/parts.py --emit` 生成 · 不要手改这一段 -->",
           "",
           f"**{len(top)} 个顶级零件 · {len(mod)} 个页级修饰 · {len(var)} 个变体 · {len(kid)} 个子元素**"
           f"（另有 {len(s['rows']) - len(parts)} 个是放映器界面，作者不写）",
           "",
           "「定义」那一列的行号是 **`newdeck.py` 剥过调参面板的版本**里的行号 —— ",
           "也就是你起手拿到的那份文件。`assets/template.html` 没剥面板，行号不一样。",
           "",
           "「用途」取自 CSS 里那条规则的注释（前导或行尾）—— 所以它跟定义在一起，",
           "**改 CSS 的时候顺手改注释，表就不会漂移**。",
           "",
           "#### 顶级零件（直接写）",
           "",
           "| 零件 | 定义 | 示范页 | 用途 |",
           "|---|---|---|---|"]
    for r in top:
        pg = " · ".join(map(str, r["pages"])) if r["pages"] else "**✗ 无**"
        out.append(f"| `.{r['cls']}` | {_L(r['line'])} | {pg} | {r['desc']} |")
    out += ["", "#### 页级修饰（写在 `<section class=\"slide X\">` 上）", "",
            "| 写法 | 定义 | 示范页 | 用途 |", "|---|---|---|---|"]
    for r in mod:
        pg = " · ".join(map(str, r["pages"])) if r["pages"] else "**✗ 无**"
        out.append(f"| `.slide.{r['cls']}` | {_L(r['line'])} | {pg} | {r['desc']} |")
    out += ["", "#### 变体（必须跟着基类写）", "",
            "| 写法 | 定义 | 示范页 | 用途 |", "|---|---|---|---|"]
    for r in var:
        pg = " · ".join(map(str, r["pages"])) if r["pages"] else "**✗ 无**"
        out.append(f"| `.{r['base']}.{r['cls']}` | {_L(r['line'])} | {pg} | {r['desc']} |")
    out += ["", "#### 子元素（必须待在父级里）", "",
            "| 写法 | 定义 | 示范页 | 用途 |", "|---|---|---|---|"]
    for r in kid:
        pg = " · ".join(map(str, r["pages"])) if r["pages"] else "**✗ 无**"
        out.append(f"| `.{r['base']} .{r['cls']}` | {_L(r['line'])} | {pg} | {r['desc']} |")
    out += ["", END]
    return "\n".join(out)


def write_rules(block: str) -> bool:
    t = RULES.read_text(encoding="utf-8")
    pat = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)
    if not pat.search(t):
        raise Fail(f"references/RULES.md 里没有 {BEGIN} … {END} —— 先放一个占位。")
    new = pat.sub(lambda m: block, t, count=1)
    if new == t:
        return False
    RULES.write_text(new, encoding="utf-8")
    return True


# ══════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(prog="parts.py", description="零件清单：定义 × 示范 × 文档")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--emit", action="store_true", help="重新生成 references/RULES.md 的零件表")
    g.add_argument("--check", action="store_true", help="有漂移就退出码 1")
    g.add_argument("--list", action="store_true", help="完整清单")
    g.add_argument("--summary", action="store_true", help="一行摘要")
    args = ap.parse_args()

    global RULES
    try:
        RULES = find_asset("RULES.md")
        s = survey()
    except Fail as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1

    parts = author_parts(s)
    fail, warn = diagnose(s)

    if args.summary:
        print(f"{len(parts)} 个零件 · 失败 {len(fail)} · 警告 {len(warn)}")
        return 1 if fail else 0

    if args.list:
        print(f"  {len(s['rows'])} 个类："
              f"顶级 {sum(1 for r in parts if r['kind']=='顶级')} · "
              f"变体 {sum(1 for r in parts if r['kind']=='变体')} · "
              f"子元素 {sum(1 for r in parts if r['kind']=='子元素')} · "
              f"界面/放映器 {len(s['rows'])-len(parts)}\n")
        print(f"  {'零件':<20} {'类型':<8} {'定义':<7} {'示范页':<14} RULES  用途")
        for r in parts:
            pg = "·".join(map(str, r["pages"])) or "✗ 无"
            print(f"  .{r['cls']:<19} {r['kind']:<8} L{r['line']:<6} {pg:<14} "
                  f"{'✓' if r['doc'] else '✗'}      {r['desc'][:34]}")
        if s["undefined"]:
            print(f"\n  页面在用但没定义：{' '.join('.'+c for c in s['undefined'])}")
        print()
        return 1 if (fail and args.check) else 0

    if args.emit:
        ch = write_rules(emit_table(s))
        print(f"  {'✓ 已更新' if ch else '· 已是最新'} references/RULES.md 的零件表 "
              f"（{len(parts)} 个零件）")
        if fail:
            print(f"\n  ✗ {len(fail)} 条必须修（表已生成，但零件还是看不见）：")
            for b in fail:
                print(f"       · {b}")
            print("\n      → 给这个零件加一页示范，或从 CSS 里删掉它。")
        if warn:
            print(f"\n  · RULES 正文没提到的（{len(warn)} 个）—— 待办，不是错误：")
            for w in warn:
                print(f"       · {w}")
        return 0

    if warn:
        print(f"  · RULES 正文没提到（{len(warn)} 个）—— 待办：")
        for w in warn:
            print(f"       · {w}")
    if fail:
        print(f"\n  ✗ {len(fail)} 条必须修（共 {len(parts)} 个零件）：")
        for b in fail:
            print(f"       · {b}")
        return 1
    print(f"  ✓ {len(parts)} 个零件全部有示范页"
          + (f"；RULES 正文还差 {len(warn)} 个" if warn else "，全部写进 RULES"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""setpages.py — 把页面从 deck 里抽出来 / 贴回去。

为什么需要它
────────────
一份 deck 约 440 KB，其中约 400 KB 是内联字体的 base64。要改页面就得编辑这个
大文件，而 `edit` 工具需要唯一锚点 —— 太难受。实测（冷启动测试）结果是：
agent 会自己发明一套「stage.html ＋ 一段 python 切片脚本」的流程，而且每次都
要重新写一遍；那段脚本锚点一旦漂移，会**静默吞掉内容**。

这个脚本把「页面」和「其余一切」切开：

    deck.html   =  外壳（CSS · 字体 · JS · 放映器）  +  #stage 里的页面

页面抽到一个几 KB 的 `.pages.html` 里改，改完贴回去。**边界以外保证一个字节不动。**

用法
────
    ./scripts/setpages.py deck.html --extract [pages.html]   抽出页面（默认 <deck>.pages.html）
    ./scripts/setpages.py deck.html [pages.html]             把页面贴回 deck
    ./scripts/setpages.py deck.html --list                   只列出页面清单，不写
    ./scripts/setpages.py deck.html --check                  只体检，不写

典型流程
────────
    ./scripts/newdeck.py out.html          # 从模板起一副骨架（带 12 页示范）
    ./scripts/setpages.py out.html --extract
    $EDITOR out.pages.html                 # 改页面
    ./scripts/setpages.py out.html         # 贴回去
    ./scripts/build.sh out.html            # 字体 → PDF → 验收

退出码：0 = 成功 · 1 = 检查没过（**没有写任何文件**）
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# ── 边界：这两个标记是 deck 结构的一部分，由 newdeck.py / template.html 保证 ──
MARK_A = '<div id="stage">'
MARK_B = '</div><!-- /#stage -->'

# 页面文件的默认后缀
PAGES_SUFFIX = ".pages.html"

SECTION_OPEN = re.compile(r'<section\b[^>]*\bclass="[^"]*\bslide\b[^"]*"', re.I)
SECTION_ANY_OPEN = re.compile(r'<section\b', re.I)
SECTION_CLOSE = re.compile(r'</section\s*>', re.I)


class Fail(Exception):
    """检查失败 —— 打印原因，绝不写文件。"""


# ── <title> 住在外壳里（#stage 之外），但它是「这份 deck 的内容」────────────────
# 三次冷启动测试里，三个 agent 都用不同办法伸手去改它（忘了 / 写 heredoc / edit）。
# 同一个问题三种解法 = 这里缺一个入口。它不是页面，所以 setpages.py 得单独管。
TITLE_RE = re.compile(r"<title>.*?</title>", re.S)


def get_title(h: str) -> str:
    a, b = split_deck(h)
    inner = h[a:b]
    if TITLE_RE.search(inner):
        raise Fail("页面区里出现了 <title> —— 它属于外壳，不该在页面文件里。")
    hits = TITLE_RE.findall(h)
    if len(hits) != 1:
        raise Fail(f"外壳里的 <title> 不是恰好一个（找到 {len(hits)} 个）。")
    return re.sub(r"</?title>", "", hits[0])


def set_title(h: str, title: str) -> str:
    """只换外壳里那一个 <title>。页面区和外壳其余部分逐字节不动。"""
    a, b = split_deck(h)
    before, inner, after = h[:a], h[a:b], h[b:]
    if TITLE_RE.search(inner):
        raise Fail("页面区里出现了 <title> —— 它属于外壳。")
    new = "<title>" + (title.replace("&", "&amp;").replace("<", "&lt;")
                             .replace(">", "&gt;")) + "</title>"
    if TITLE_RE.search(before):
        before = TITLE_RE.sub(lambda m: new, before, count=1)
    elif TITLE_RE.search(after):
        after = TITLE_RE.sub(lambda m: new, after, count=1)
    else:
        raise Fail("外壳里找不到 <title>。")
    return before + inner + after


# ══════════════════════════════════════════════════════════════════════
#  读
# ══════════════════════════════════════════════════════════════════════

def split_deck(h: str) -> tuple[int, int]:
    """把 deck 切成 (边界起点, 边界终点)。页面区 = h[a:b]。"""
    na, nb = h.count(MARK_A), h.count(MARK_B)
    if na != 1:
        raise Fail(
            f"找不到唯一的 {MARK_A!r}（出现 {na} 次）。\n"
            f"     这份 deck 不是 newdeck.py / template.html 的产物，"
            f"或者它的结构被改过。\n"
            f"     用 `grep -c '{MARK_A}' <deck>` 确认一下。")
    if nb != 1:
        raise Fail(f"找不到唯一的 {MARK_B!r}（出现 {nb} 次）。")
    a = h.index(MARK_A) + len(MARK_A)
    b = h.index(MARK_B)
    if b < a:
        raise Fail("边界标记顺序反了 —— 文件结构已损坏。")
    return a, b


def check_pages(pages: str, where: str) -> list[str]:
    """检查页面文件本身。返回页面标题列表。"""
    body = pages.strip()
    if not body:
        raise Fail(f"{where}：空的。")
    if MARK_A in body or MARK_B in body:
        raise Fail(
            f"{where}：里面出现了边界标记 —— 贴回去会把 deck 结构切坏。\n"
            f"     页面文件只该有 <section class=\"slide\"> 块，"
            f"不该有外壳（#stage / CSS / 字体 / JS）。")
    if not SECTION_ANY_OPEN.search(body):
        raise Fail(
            f"{where}：一个 <section> 都没有。\n"
            f"     页面文件应该是一串 <section class=\"slide\">…</section>。")
    no, nc = len(SECTION_ANY_OPEN.findall(body)), len(SECTION_CLOSE.findall(body))
    if no != nc:
        raise Fail(
            f"{where}：<section> 没有配平 —— 开 {no} 个，闭 {nc} 个。\n"
            f"     多半是漏了 </section>，或某个标签被截断了。")
    if not SECTION_OPEN.search(body):
        raise Fail(
            f"{where}：有 <section> 但没有任何一个带 class=\"slide\"。\n"
            f"     deck 的放映器靠这个类名找页面，缺了它这一页不会显示。")

    # 页面区不该出现外壳才有的东西
    for tag in ("<style", "<script", "@font-face"):
        if tag in body:
            raise Fail(
                f"{where}：出现了 {tag}…> —— 那是外壳的东西，不属于页面。\n"
                f"     页面里写死 CSS 会让令牌失同步（RULES 冻结线）。")

    titles = []
    for m in SECTION_OPEN.finditer(body):
        seg = body[m.start():]
        end = seg.find("</section>")
        seg = seg[:end] if end > 0 else seg[:2000]
        h1 = re.search(r'<h1[^>]*>(.*?)</h1>', seg, re.S)
        # h1 必有（check.py 强制）—— 这里不再有 kicker 兜底
        titles.append(re.sub(r"<[^>]+>", "", h1.group(1)).strip() if h1 else "")
    return titles


def list_pages(pages: str) -> list[tuple[int, str, str]]:
    """[(序号, class, 标题), …]"""
    body = pages.strip()
    out = []
    for i, m in enumerate(SECTION_OPEN.finditer(body), 1):
        cls = re.search(r'class="([^"]*)"', m.group(0)).group(1)
        seg = body[m.start():]
        end = seg.find("</section>")
        seg = seg[:end] if end > 0 else seg[:2000]
        h1 = re.search(r'<h1[^>]*>(.*?)</h1>', seg, re.S)
        if h1:
            t = re.sub(r"<br\s*/?>", " ", h1.group(1))
            t = re.sub(r"<[^>]+>", "", t).strip()
        else:
            # 缺 h1 是内容缺陷（RULES.md：每个内容页都要有论点），
            # 但这里只是给人看的列表，不得以 traceback 崩掉。
            # 2026-09-17 真实崩过：原写成 t = re.sub(..., h1.group(1))，
            # None 判断只护住了下一行。
            t = "（无 h1）"
        out.append((i, cls, t))
    return out


# ══════════════════════════════════════════════════════════════════════
#  写
# ══════════════════════════════════════════════════════════════════════

def splice(h: str, pages: str) -> str:
    """把 pages **原样**贴进 h 的两个边界之间。

    注意是原样（不做 strip / 不补换行）。只要规整一个字节，
    「抽出来再贴回去逐字节相同」这条保证就没了 —— 而那正是这个工具存在的理由。
    """
    a, b = split_deck(h)
    return h[:a] + pages + h[b:]


def verify_splice(old: str, new: str, n_expect: int) -> None:
    """贴完后的自检。任何一条不过就抛 Fail，**文件不落盘**。"""
    a, b = split_deck(old)
    a2, b2 = split_deck(new)
    h_pages = new[a2:b2]
    # ① 边界之前逐字节相同
    if new[:a2] != old[:a]:
        raise Fail("边界之前的字节被改动了 —— 外壳必须原样。")
    # ② 边界之后逐字节相同
    if new[b2:] != old[b:]:
        raise Fail("边界之后的字节被改动了 —— 外壳必须原样。")
    # ③ 页面数对得上
    got = len(SECTION_OPEN.findall(new[a2:b2]))
    if got != n_expect:
        raise Fail(f"贴完页面数不对：期望 {n_expect}，实得 {got}。")
    # ④ 幂等 —— 同样的输入再贴一次结果必须相同
    if splice(new, new[a2:b2]) != new:
        raise Fail("不幂等 —— 再贴一次结果会变。这是边界计算错了。")
    # ⑤ 页面区不能含外壳标记（否则下次边界会算错）
    if MARK_A in h_pages or MARK_B in h_pages:
        raise Fail("页面区里出现了边界标记 —— 下次读写会算错边界。")


# ══════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════

def _rel(p: Path) -> str:
    """相对当前目录的路径 —— 安装后脚本在 scripts/ 里，写裸文件名会误导。"""
    try:
        return os.path.relpath(p, os.getcwd())
    except ValueError:
        return str(p)


def _self() -> str:
    return f"{sys.executable} {_rel(Path(__file__).resolve())}"


def _sibling(name: str) -> str:
    return _rel(Path(__file__).resolve().parent / name)


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="setpages.py",
        description="把页面从 deck 里抽出来 / 贴回去（边界以外一个字节都不动）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("典型流程")[1].split("退出码")[0].rstrip() if "典型流程" in __doc__ else "")
    ap.add_argument("deck", nargs="?", help="deck.html")
    ap.add_argument("pages", nargs="?", help=f"页面文件（默认 <deck>{PAGES_SUFFIX}）")
    ap.add_argument("--extract", "-x", action="store_true",
                    help="抽出页面到页面文件（不指定 pages 时用默认名）")
    ap.add_argument("--list", "-l", action="store_true", help="只列页面清单")
    ap.add_argument("--check", action="store_true", help="只体检，不写")
    ap.add_argument("--force", "-f", action="store_true",
                    help="--extract 时：pages 文件里有未贴回的改动也照样覆盖（放弃那些改动）")
    ap.add_argument("--self-test", action="store_true", help="跑反向用例")
    ap.add_argument("--title", metavar="TEXT",
                    help="改外壳里的 <title>（唯一会碰外壳内容的地方，只换这一个标签）")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()
    if not args.deck:
        ap.error("需要 deck.html（或加 --self-test）")

    deck = Path(args.deck)
    if not deck.exists():
        print(f"✗ 没有这个文件：{deck}", file=sys.stderr)
        return 1
    pages_path = Path(args.pages) if args.pages else deck.with_suffix(PAGES_SUFFIX)
    # 三种模式互斥里挑一个
    if sum([args.extract, args.list, args.check, bool(args.title)]) > 1:
        print("✗ --extract / --list / --check / --title 只能用一个", file=sys.stderr)
        return 1

    try:
        h = deck.read_text(encoding="utf-8")
        a, b = split_deck(h)
        inner = h[a:b].strip()
        titles = check_pages(inner, f"{deck} 的页面区")

        # ── --list ───────────────────────────────────────────────
        if args.list:
            print(f"  {deck} — {len(titles)} 页 · title: {get_title(h)!r}")
            print(f"  {'页':>3}  {'class':<28} 标题")
            for i, cls, t in list_pages(inner):
                print(f"  {i:>3}  {cls:<28} {t}")
            return 0

        # ── --extract ────────────────────────────────────────────
        if args.extract:
            raw = h[a:b]                    # 原样，含两端空白
            if pages_path.exists():
                old = pages_path.read_text(encoding="utf-8")
                if old != raw and not args.force:
                    # 和当前页面区一致 = 早已贴回，覆盖无损失；
                    # 不一致 = 里面有没贴回去的改动，覆盖就丢了。
                    print(f"✗ {pages_path} 里有没贴回去的改动，不覆盖。", file=sys.stderr)
                    print(f"    先贴回去（{_self()} {deck}），或确认放弃后加 --force。",
                          file=sys.stderr)
                    return 1
            pages_path.write_text(raw, encoding="utf-8")
            # 贴回去必须与原文逐字节相同 —— 这是本工具的硬保证
            if splice(h, raw) != h:
                raise Fail("抽出再贴回的结果与原文件不同 —— 这个 bug 不能放过去。")
            print(f"  ✓ 抽出 {len(titles)} 页（title: {get_title(h)!r}）→ {pages_path} "
                  f"({pages_path.stat().st_size/1024:.0f} KB)")
            print(f"    改完运行： {_self()} {deck}")
            return 0

        # ── --title ──────────────────────────────────────────────
        if args.title:
            old = get_title(h)
            new = set_title(h, args.title)
            a2, b2 = split_deck(new)
            if new[:a2] != h[:a] or new[b2:] != h[b:]:
                # 只有 <title> 那一段允许不同
                if TITLE_RE.sub("", new[:a2]) != TITLE_RE.sub("", h[:a]) or new[b2:] != h[b:]:
                    raise Fail("改 title 时动到了外壳的别处。")
            if new[a2:b2] != h[a:b]:
                raise Fail("改 title 时动到了页面区。")
            deck.write_text(new, encoding="utf-8")
            print(f"  ✓ title:\n      旧 {old!r}\n      新 {args.title!r}")
            return 0

        # ── --check ──────────────────────────────────────────────
        if args.check:
            raw = h[a:b]
            if splice(h, raw) != h:
                raise Fail("抽出再贴回的结果与原文件不同。")
            print(f"  ✓ {deck}：结构完整，{len(titles)} 页，可安全读写")
            return 0

        # ── 贴回去 ───────────────────────────────────────────────
        if not pages_path.exists():
            print(f"✗ 没有 {pages_path}。先跑： ./scripts/setpages.py {deck} --extract",
                  file=sys.stderr)
            return 1
        pages = pages_path.read_text(encoding="utf-8")
        new_titles = check_pages(pages, str(pages_path))
        new = splice(h, pages)
        verify_splice(h, new, len(new_titles))
        deck.write_text(new, encoding="utf-8")

        old_n, new_n = len(titles), len(new_titles)
        delta = f"（{old_n} → {new_n} 页）" if old_n != new_n else f"（{new_n} 页）"
        print(f"  ✓ 已贴回 {deck} {delta}")
        print(f"  {'页':>3}  {'class':<28} 标题")
        for i, cls, t in list_pages(pages):
            print(f"  {i:>3}  {cls:<28} {t}")
        print(f"\n  下一步： {_sibling('build.sh')} {deck}")
        return 0

    except Fail as e:
        print(f"✗ {e}", file=sys.stderr)
        print("  —— 没有写任何文件。", file=sys.stderr)
        return 1




# ══════════════════════════════════════════════════════════════════════
#  自检（./scripts/setpages.py --self-test）
# ══════════════════════════════════════════════════════════════════════

_SHELL = """<!doctype html>
<html><head><style>.slide{color:red}</style></head><body>
<div id="stage">
@@INNER@@</div><!-- /#stage -->
<script>/* 放映器 —— 贴页时绝不能被碰 */ var x = 1;</script>
</body></html>"""

_GOOD_INNER = """
<section class="slide"><h1>甲</h1></section>
<section class="slide end"><h1>乙</h1></section>
"""

# (名字, 页面内容, 期望通过?, 期望错误里出现的关键字)
_CASES = [
    ("ok",            _GOOD_INNER,                                            True,  ""),
    ("ok_no_blank",   _GOOD_INNER.strip(),                                    True,  ""),
    ("empty",         "\n   \n",                                              False, "空的"),
    ("no_section",    "<p>我忘了写 section</p>",                              False, "一个 <section> 都没有"),
    ("unbalanced",    '<section class="slide"><h1>缺闭合</h1>',               False, "没有配平"),
    ("no_slide_cls",  '<section class="card"><h1>类名写错</h1></section>',    False, 'class="slide"'),
    ("has_marker",    _GOOD_INNER + '</div><!-- /#stage -->',                 False, "边界标记"),
    ("has_style",     _GOOD_INNER + '<style>.slide{color:blue}</style>',      False, "<style"),
    ("has_script",    _GOOD_INNER + '<script>alert(1)</script>',              False, "<script"),
]


def _self_test() -> int:
    ok = 0
    print(f"  setpages.py 自检（{len(_CASES)} 个用例）\n")
    # 一份正常的 deck —— 用例只改「要贴的页面」，不改 deck。
    # 这和 main() 里的顺序一致：先 check_pages(页面)，再 splice(deck)。
    deck = _SHELL.replace("@@INNER@@", _GOOD_INNER)
    for name, inner, should_pass, keyword in _CASES:
        try:
            titles = check_pages(inner, "case")
            new = splice(deck, inner)
            verify_splice(deck, new, len(titles))
            got, err = True, ""
        except Fail as e:
            got, err = False, str(e)
        except Exception as e:                       # noqa: BLE001
            got, err = False, f"{type(e).__name__}: {e}"

        good = (got == should_pass) and (should_pass or keyword in err)
        ok += good
        mark = "✓" if good else "✗"
        verdict = "如预期" if got == should_pass else "**判断错了**"
        detail = "" if should_pass else f"   ← {err.splitlines()[0][:70]}"
        print(f"  {mark} {name:<14} {'通过' if got else '拒绝'}  {verdict}{detail}")
        if not good:
            print(f"      期望 {'通过' if should_pass else f'含 {keyword!r}'}，实得：{err[:170]!r}")
        if got and should_pass:
            a, b = split_deck(new)
            if splice(new, new[a:b]) != new:
                print("      ✗ 往返不是逐字节相同！")

    # 外壳保真：贴页后 <script> 必须一字节没动
    deck = _SHELL.replace("@@INNER@@", "\n<section class=\"slide\"><h1>新</h1></section>\n")
    new = splice(_SHELL.replace("@@INNER@@", _GOOD_INNER), deck[deck.index(MARK_A)+len(MARK_A):deck.index(MARK_B)])
    shell_kept = "<script>/* 放映器" in new and ".slide{color:red}" in new
    ok += shell_kept
    print(f"\n  {'✓' if shell_kept else '✗'} 外壳保真：贴页后 <style>/<script> 一字节没动")
    print(f"\n  {ok}/{len(_CASES)+1} 通过")
    return 0 if ok == len(_CASES) + 1 else 1


if __name__ == "__main__":
    sys.exit(main())

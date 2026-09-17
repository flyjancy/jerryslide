#!/usr/bin/env python3
"""scriptcheck.py —— 讲稿对账：讲稿必须与 deck 逐页对齐，且文风受 check.py 同一套约束。

用法：
    ./scripts/scriptcheck.py <deck.html> <讲稿.md>

为什么要有这个脚本：讲稿是**唯一**同时挂着两边的东西 ——
它引用 deck 的每一页，又受 §7 文风约束。两边都会漂：
  · 改了 deck 的标题，讲稿还是旧的 —— 讲的人照着念，念的是别的页；
  · 讲稿是口头脚本，写的时候最容易冒出填充语（§7 豁免备注，但不豁免讲稿）。
眼睛看不出来（两份东西各自都通顺），所以机械查。

查五件事：
  1. 页数一致：deck 的 <section> 数 == 讲稿 `### n ·` 数 == 标题对照表行数
  2. 每页一节，编号递增，标题与 deck 的 h1 **逐页**对齐
     （章节页允许 `章节页 03 —— ` 前缀；`<br>`、`**` 不算差异）
  3. 每节必须有 `**标题意思**` 与 `**讲**` —— 没标题意思的讲稿，
     讲的人还是看不懂英文标题（这就是写讲稿的理由）
  4. 文风：check.py 的 PROSE_BANNED 零命中（`「」` 里的引用豁免，同 §7）
  5. 标题对照表：行数与 deck 对齐，每行的英文标题也对齐

退出码：0 全过；1 有问题。
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check import PROSE_BANNED  # 文风词表只有一份，不复制

OK, BAD = "  ✓", "  ✗"


def norm_title(s):
    """标题归一化：<br> → 空格、去标签、去 markdown 强调、去空白。"""
    s = re.sub(r"<br\s*/?>", " ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("**", "").replace("*", "")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def deck_titles(path):
    """deck 每页的标题（按页序）；同时返回页数。"""
    src = open(path, encoding="utf-8").read()
    secs = re.findall(r'<section class="slide.*?</section>', src, re.S)
    titles = []
    for s in secs:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", s, re.S)
        titles.append(norm_title(m.group(1)) if m else "")
    return titles


def script_pages(text):
    """讲稿的逐页块：[(序号, 标题, 块文本)]。"""
    out = []
    for m in re.finditer(r"^### (\d+) · (.*?)\s*$", text, re.M):
        start = m.end()
        nxt = re.search(r"^### \d+ · ", text[start:], re.M)
        body = text[start:start + nxt.start()] if nxt else text[start:]
        out.append((int(m.group(1)), m.group(2).strip(), body))
    return out


def table_rows(text):
    """标题对照表的数据行：跳过表头与分隔行。"""
    m = re.search(r"^##[^\n]*标题对照表[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        return None
    rows = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or set(cells[0]) <= set("-: "):
            continue
        if cells[0] in ("页", "页数"):
            continue
        rows.append(cells)
    return rows


def strip_quotes(s):
    """引用豁免：`「…」` 里的字是别人的原话，不查文风（同 §7）。"""
    return re.sub(r"「[^」]*」", "", s)


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip())
        return 2
    deck, script = argv[1], argv[2]
    for p in (deck, script):
        if not os.path.exists(p):
            print(f"✗ 找不到文件：{p}")
            return 2
    text = open(script, encoding="utf-8").read()
    titles = deck_titles(deck)
    pages = script_pages(text)
    rows = table_rows(text)
    bad = 0

    print(f"讲稿对账：{os.path.basename(deck)} ↔ {os.path.basename(script)}")

    # ① 页数
    n_tbl = len(rows) if rows is not None else -1
    if rows is None:
        print(f"{BAD} 没有找到「标题对照表」小节（## 标题对照表）—— 讲的人靠它看英文标题的意思")
        bad += 1
    elif len(titles) == len(pages) == n_tbl:
        print(f"{OK} 页数一致：deck {len(titles)} 页 = 讲稿 {len(pages)} 节 = 对照表 {n_tbl} 行")
    else:
        print(f"{BAD} 页数不一致：deck {len(titles)} · 讲稿 {len(pages)} · 对照表 {n_tbl}")
        bad += 1

    # ② 逐页编号与标题
    want = list(range(1, len(titles) + 1))
    got = [p[0] for p in pages]
    if got == want:
        print(f"{OK} 页码连续：1…{len(pages)}")
    else:
        print(f"{BAD} 页码不对：{got}（应为 1…{len(titles)}，不许跳号或多页）")
        bad += 1

    mism = []
    for i, (n, t, _body) in enumerate(pages):
        if i >= len(titles):
            break
        t = re.sub(r"^章节页\s*\d+\s*——\s*", "", t)          # 章节页前缀不算差异
        t = re.sub(r"^章节页\s*[0-9]+\s*[—–-]+\s*", "", t)
        if norm_title(t) != titles[i]:
            mism.append((n, titles[i], norm_title(t)))
    if mism:
        print(f"{BAD} 有 {len(mism)} 页标题与 deck 不一致（讲的人会念错页）：")
        for n, a, b in mism:
            print(f"       第 {n} 页  deck「{a}」 vs 讲稿「{b}」")
        bad += 1
    elif pages:
        print(f"{OK} 逐页标题与 deck 对齐（{len(pages)}/{len(titles)}）")

    # ③ 每节的必备字段
    missing = [n for n, _t, body in pages
               if "**标题意思**" not in body or "**讲**" not in body]
    if missing:
        print(f"{BAD} 这些页缺「**标题意思**」或「**讲**」：{missing} —— "
              f"没有标题意思的讲稿，讲的人还是看不懂英文标题")
        bad += 1
    elif pages:
        print(f"{OK} 每页都有「标题意思」与「讲」")

    # ④ 文风（引用豁免）
    hits = {}
    for i, (n, t, body) in enumerate(pages, 1):
        for w, kind in PROSE_BANNED:
            if w in strip_quotes(t) or w in strip_quotes(body):
                hits.setdefault(f"{w}（{kind}）", []).append(n)
    if hits:
        print(f"{BAD} 文风：{len(hits)} 个词命中 §7 禁用清单")
        for w, ps in hits.items():
            print(f"       {w}：第 {'、'.join(map(str, ps))} 页")
        bad += 1
    else:
        print(f"{OK} 文风：{len(PROSE_BANNED)} 个禁用词零命中（引用豁免后）")

    # ⑤ 对照表逐行
    if rows:
        tbl_bad = []
        for i, cells in enumerate(rows):
            if i >= len(titles) or len(cells) < 2:
                continue
            tbl = norm_title(cells[1])
            tbl = re.sub(r"^\d+\s+", "", tbl)   # 对照表里章节行带编号（`**01 Title**`）不算差异
            if tbl != titles[i]:
                tbl_bad.append((i + 1, titles[i], tbl))
        if tbl_bad:
            print(f"{BAD} 对照表有 {len(tbl_bad)} 行与 deck 不一致：")
            for n, a, b in tbl_bad:
                print(f"       第 {n} 页  deck「{a}」 vs 对照表「{b}」")
            bad += 1
        else:
            print(f"{OK} 对照表逐行与 deck 对齐（{len(rows)} 行）")

    print()
    if bad:
        print(f"✗ 发现 {bad} 个问题。")
        return 1
    print("✓ 讲稿与 deck 对齐，文风合规。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

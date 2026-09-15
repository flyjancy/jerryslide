#!/usr/bin/env python3
"""
把字体子集化并内联进 deck —— 一次性解决三件事：

  1. 字体装没装      deck 自带字形，任何机器打开都长一样
  2. 跨机器换行      字体固定 → 换行固定 → 余量测量有意义
  3. PDF 的 Type 3   静态内联字体导出成 CID TrueType，
                     系统字体（PingFang 等可变字体）只能导出 Type 3

用法：
    python3 build_font.py deck.html                 # 原地内联
    python3 build_font.py deck.html -o out.html     # 另存
    python3 build_font.py deck.html --report        # 只报字号大小

依赖：
    pip install fonttools brotli

首次运行会下载 Noto Sans SC 变量字体（16.9 MB）到 ~/.cache/slide-fonts/。
之后离线可用。

⚠️ 版权：
    思源黑体 / Noto Sans SC 是 SIL OFL，可自由内联与分发。
    Calibri 是 Microsoft 授权字体（随 Office 安装）—— 内联仅限有 Office 授权的内部用途，
    对外分发请改用 Carlito（OFL，与 Calibri 度量兼容）或去掉 Calibri 只留思源黑体。
"""

import argparse
import base64
import os
import re
import subprocess
import sys
from pathlib import Path

# 缓存目录。允许用 SLIDE_FONT_DIR 覆盖 —— build.sh 的 --doctor 靠环境变量把
# 字体指向别处来测「缺字体」这一条；不认的话它会报「下载失败」而其实成功了。
CACHE = Path(os.environ.get("SLIDE_FONT_DIR") or (Path.home() / ".cache" / "slide-fonts"))
NOTO_VF = CACHE / "NotoSansSC-VF.ttf"
NOTO_URL = "https://github.com/google/fonts/raw/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf"

# 子集里始终保留的字符 —— 保证以后改文案不会突然缺字
ALWAYS = (
    "".join(chr(c) for c in range(0x20, 0x7F))          # ASCII
    + "—–…“”‘’「」『』·×÷≥≤≈±°①②③④⑤⑥⑦⑧⑨⑩"
    + "（）《》、。，；：？！"
    + "0123456789"
    + "一二三四五六七八九十百千万亿"
)

# 拉丁字体：--latin 选一个。families 里 None = 拉丁也用思源黑体。
FALLBACK = ('"PingFang SC","Hiragino Sans GB","Microsoft YaHei",'
            '"Helvetica Neue",Arial,sans-serif')

LATIN_CHOICES = {
    #  名字             内联 family        .ttc 里取哪几个字面
    "calibri":        ("SlideCalibri", None),
    "helvetica":      ("SlideHelv",    "/System/Library/Fonts/Helvetica.ttc"),
    "helvetica-neue": ("SlideHelvN",   "/System/Library/Fonts/HelveticaNeue.ttc"),
    "arimo":          ("SlideArimo",    "Arimo"),      # OFL，Helvetica 度量兼容
    "noto":           (None,            None),   # 拉丁也用思源黑体
}

ARIMO_VF = CACHE / "Arimo-VF.ttf"
ARIMO_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/arimo/Arimo%5Bwght%5D.ttf"

CALIBRI_CANDIDATES = [
    Path("/Applications/Microsoft Word.app/Contents/Resources/DFonts"),
    Path("/Library/Fonts/Microsoft"),
    Path.home() / "Library/Fonts",
]


def need(mod, hint):
    try:
        return __import__(mod)
    except ImportError:
        sys.exit(f"✗ 缺少 {mod}。装一下：\n    {hint}")


def get_noto_vf():
    if NOTO_VF.exists() and NOTO_VF.stat().st_size > 1_000_000:
        return NOTO_VF
    CACHE.mkdir(parents=True, exist_ok=True)
    print(f"  ↓ 下载思源黑体（Noto Sans SC 变量字体，首次运行才会有）…")
    subprocess.run(["curl", "-sL", "--max-time", "600", "-o", str(NOTO_VF), NOTO_URL], check=True)
    if NOTO_VF.stat().st_size < 1_000_000:
        sys.exit("✗ 下载失败（可能没网）。手动放到 " + str(NOTO_VF))
    return NOTO_VF


def find_calibri():
    for d in CALIBRI_CANDIDATES:
        r, b = d / "Calibri.ttf", d / "Calibrib.ttf"
        if r.exists() and b.exists():
            return r, b
    return None, None


def latin_sources(choice):
    """返回 [(路径, ttc 索引或 None, 字重)]。空列表 = 拉丁用思源黑体。
       None = 选定的字体找不到。"""
    if choice == "noto":
        return []
    if choice == "calibri":
        r, b = find_calibri()
        return None if not r else [(r, None, 400), (b, None, 700)]
    if choice == "arimo":
        # 变量字体 → 先实例化到 400/700 静态
        if not ARIMO_VF.exists():
            CACHE.mkdir(parents=True, exist_ok=True)
            print("    ↓ 下载 Arimo（OFL，Helvetica 度量兼容）…")
            subprocess.run(["curl", "-sL", "--max-time", "300",
                            "-o", str(ARIMO_VF), ARIMO_URL], check=True)
        from fontTools.ttLib import TTFont
        from fontTools.varLib import instancer
        out = []
        for w in (400, 700):
            dst = CACHE / "build" / f"arimo-{w}.ttf"
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                f = TTFont(str(ARIMO_VF))
                instancer.instantiateVariableFont(f, {"wght": w}, inplace=True,
                                                  updateFontNames=True)
                f.save(str(dst)); f.close()
            out.append((dst, None, w))
        return out
    ttc = Path(LATIN_CHOICES[choice][1])
    if not ttc.exists():
        return None
    # 从 .ttc 里取 Regular 与 Bold：先按 name 找，找不到就用前两个字面
    from fontTools.ttLib import TTCollection
    faces = TTCollection(str(ttc), lazy=True).fonts
    idx = {}
    for i, t in enumerate(faces):
        sub = t["name"].getDebugName(2) or ""
        if sub in ("Regular", "Bold") and "Italic" not in sub and "Oblique" not in sub:
            idx.setdefault(sub, (i, t))
    if "Regular" in idx and "Bold" in idx:
        return [(ttc, idx["Regular"][0], 400), (ttc, idx["Bold"][0], 700)]
    return [(ttc, 0, 400), (ttc, 1, 700)]


def deck_chars(path):
    """取出 deck 里所有可见文字。不含 <style> / <script>。"""
    h = Path(path).read_text(encoding="utf-8")
    h = re.sub(r"<style[^>]*>.*?</style>", " ", h, flags=re.S)
    h = re.sub(r"<script[^>]*>.*?</script>", " ", h, flags=re.S)
    h = re.sub(r"<!--.*?-->", " ", h, flags=re.S)
    h = re.sub(r"<[^>]+>", " ", h)
    h = h.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return set(h) | set(ALWAYS)


def make_woff2(ttf_path, text, out_path):
    from fontTools import subset
    from fontTools.ttLib import TTFont
    opts = subset.Options()
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    opts.drop_tables = []
    font = subset.load_font(str(ttf_path), opts)
    s = subset.Subsetter(options=opts)
    s.populate(text=text)
    s.subset(font)
    subset.save_font(font, str(out_path), opts)
    font.close()
    return out_path.stat().st_size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("deck", nargs="?", help="deck.html（--fetch-only 时不需要）")
    ap.add_argument("--fetch-only", action="store_true",
                    help="只把源字体下到缓存，不切片、不改任何文件（给 build.sh --doctor --fix 用）")
    ap.add_argument("-o", "--out")
    ap.add_argument("--report", action="store_true", help="只报大小，不改文件")
    ap.add_argument("--latin", default="noto", choices=sorted(LATIN_CHOICES),
                    help="拉丁用哪套字体（noto = 拉丁也用思源黑体）")
    ap.add_argument("--no-latin", dest="latin", action="store_const", const="noto",
                    help="同 --latin noto")
    a = ap.parse_args()

    latin_fam = LATIN_CHOICES[a.latin][0]
    FONT_STACK = ((f'"{latin_fam}",' if latin_fam else "") + '"SlideNotoSC",' + FALLBACK)

    if a.fetch_only:
        # 只下源字体。切片用的 fontTools/brotli 这里**不要求** ——
        # 环境没装齐时也该能先把 16.9 MB 的字体拿到手。
        f = get_noto_vf()
        print(f"  ✓ 源字体就位：{f}  {f.stat().st_size / 1048576:.1f} MB")
        return

    if not a.deck:
        ap.error("需要 deck.html（或者加 --fetch-only）")
    need("fontTools", "pip install fonttools brotli")
    need("brotli", "pip install fonttools brotli")

    tmp = CACHE / "build"
    tmp.mkdir(parents=True, exist_ok=True)

    chars = deck_chars(a.deck)
    cjk = "".join(sorted(c for c in chars if ord(c) > 0x2000))
    latin = "".join(sorted(c for c in chars if ord(c) <= 0x2000))
    print(f"  deck 字符：{len(chars)} 个（CJK {len(cjk)} · 拉丁/符号 {len(latin)}）")

    faces, total = [], 0

    # ── 中文：思源黑体，变量字体实例化到 400 / 700 再子集 ──
    vf = get_noto_vf()
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
    for w in (400, 700):
        static = tmp / f"ns-{w}.ttf"
        if not static.exists():
            f = TTFont(str(vf))
            instancer.instantiateVariableFont(f, {"wght": w}, inplace=True, updateFontNames=True)
            f.save(str(static))
            f.close()
        sub = tmp / f"ns-{w}.woff2"
        n = make_woff2(static, "".join(chars), sub)
        b64 = base64.b64encode(sub.read_bytes()).decode()
        total += len(b64)
        faces.append(f"@font-face{{font-family:SlideNotoSC;font-style:normal;"
                     f"font-weight:{w};font-display:block;"
                     f"src:url(data:font/woff2;base64,{b64}) format('woff2')}}")
        print(f"    思源黑体 {w}: {n/1024:7.1f} KB")

    # ── 拉丁：只留拉丁与符号 ──
    src = latin_sources(a.latin)
    if src:
        for path, num, w in src:
            face = path
            if num is not None:                      # .ttc → 先抽出单个字面
                from fontTools.ttLib import TTFont as _TF
                face = tmp / f"{path.stem}-{num}.ttf"
                if not face.exists():
                    _TF(str(path), fontNumber=num, lazy=False).save(str(face))
            sub = tmp / f"{a.latin}-{w}.woff2"
            n = make_woff2(face, latin or ALWAYS, sub)
            b64 = base64.b64encode(sub.read_bytes()).decode()
            total += len(b64)
            faces.append(f"@font-face{{font-family:{latin_fam};font-style:normal;"
                         f"font-weight:{w};font-display:block;"
                         f"src:url(data:font/woff2;base64,{b64}) format('woff2')}}")
            print(f"    {a.latin} {w}:{'' if w==400 else ' ' * 6}{n/1024:7.1f} KB")
    else:
        print(f"    ✓ 拉丁用思源黑体（--latin {a.latin}）")

    print(f"  ──────────────────────────────")
    print(f"  内联后增加：{total/1024:.0f} KB")

    if a.report:
        return

    h = Path(a.deck).read_text(encoding="utf-8")
    # ⚠️ 必须用 [^}]* 而不是 .*? —— 每个 @font-face 只以单个 } 结束，
    #    用 .*?\}\} 会从第一个 @font-face 一路吃到文件后面的 }} ，
    #    把中间整个样式表删掉。base64 字母表不含 } ，所以 [^}]* 安全。
    h = re.sub(r"@font-face\{font-family:Slide[^}]*\}", "", h)
    # 手工定位插入点，并把紧随 <style> 的空白全部吃掉 ——
    # 否则每跑一次就多留几个换行，文件会越跑越大（不是幂等）。
    i = h.index("<style")
    j = h.index(">", i) + 1
    k = j
    while k < len(h) and h[k] in " \t\r\n":
        k += 1
    h = h[:j] + "\n" + "\n".join(faces) + "\n" + h[k:]
    # ⚠️ 字体栈在源码里跳行写的，必须 DOTALL，否则静默匹配不到 ——
    #    嵌入成功但没人用，PDF 里还是 PingFang，而命令输出看不出任何异常。
    if "--font-title" not in h:
        sys.exit("✗ 剥离旧 @font-face 时误删了样式表。已中止，文件未被修改。")
    for tok in ("--font-title", "--font-body"):
        h, n = re.subn(rf"{re.escape(tok)}:.*?;", f"{tok}: {FONT_STACK};", h,
                       count=1, flags=re.S)
        if n != 1:
            sys.exit(f"✗ 没找到 {tok}，字体栈没被替换。嵌入的字体不会生效。")
    if h.count("@font-face{font-family:Slide") < 2 or "SlideNotoSC" not in h:
        sys.exit("✗ @font-face 没写进去。")
    out = a.out or a.deck
    Path(out).write_text(h, encoding="utf-8")
    print(f"  ✓ 写入 {out}（{Path(out).stat().st_size/1024:.0f} KB）")


if __name__ == "__main__":
    main()

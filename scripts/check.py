#!/usr/bin/env python3
"""
check.py —— slide 发布前自检

用法：
    ./scripts/check.py assets/template.html
    ./scripts/check.py deck.html --pdf out.pdf
    ./scripts/check.py --self-test            # 跑 assets/tests/ 下的反向用例

退出码：0 = 全部通过，1 = 有问题

────────────────────────────────────────────────────────────────────────
设计要点（都是踩过的坑，不要改回去）

1. 溢出用「几何位置」判，不用「子元素高度累加」。
   累加在横排 flex 里会把并排的列加起来，虚高成 2 倍。
   而且 align-items:stretch 会把子项盒子拉成容器高 —— 盒子不溢出，
   文字溢出到盒子外面被裁掉，任何基于盒子的判据都测不到。
   所以要比 getBoundingClientRect 的位置，并且用 Range 量文本节点。

2. 同一次测量里 `need`（内容高度）仍用累加，但横排取 max 而不是 sum。
   这两件事分开：溢出判定用几何，余量报告用尺寸。

3. 静态检查要剥掉 @media print 块。
   `@media print{ .shot{box-shadow:none!important} }` 是修复本身，
   不是违规 —— 曾经把它数成违规，误判了唯一一份验证干净的产物。
   box-shadow / linear-gradient 是「手段」，「导出的 PDF 里 /SMask = 0」才是「目的」。
   静态检查只是代理指标，代理错了就该让路。

4. 余量门槛 48px，不是 8px。
   中英混排的换行在不同中文字体下会差 ±1 行（±48px）：
   CJK 是 1em 等宽、行高写死，所以纯中文不受影响，
   但拉丁部分的宽度差会挤动中英边界。余量小于一行 = 换台机器就溢�出。
"""

import json
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import html as htmlmod

# ⚠️ 和 build.sh 的 find_chrome 保持字面同步 —— 两边不一致时，
# build.sh ② 能导出 PDF、③ 却在这里断掉（只有 google-chrome-stable /
# chromium-browser 的 Linux 机器上真实会发生）。
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    shutil.which("google-chrome") or "",
    shutil.which("google-chrome-stable") or "",
    shutil.which("chromium") or "",
    shutil.which("chromium-browser") or "",
]

# chrome-headless-shell 是 Chromium 官方的独立 headless 二进制，**不注册
# WindowServer**，所以 Dock 不会跳。主程序的 --headless=new 仍会以 uiElement=0
# 注册 —— Dock 每次调用插一个临时格子、半秒后再删掉，整排图标左右弹一下。
# 一份 deck 要跑十几次，很显眼。两者出图像素级一致（9 页逐页 0 差异），
# 所以能换就换。见 build.sh 顶部同一段注释。
HEADLESS_SHELL_CANDIDATES = [
    os.environ.get("CHROME_HEADLESS_SHELL") or "",
    *sorted(glob.glob(os.path.expanduser(
        "~/.cache/chrome-headless-shell/*/chrome-headless-shell"))),
    shutil.which("chrome-headless-shell") or "",
    "/opt/homebrew/bin/chrome-headless-shell",
    "/usr/local/bin/chrome-headless-shell",
]

MIN_SLACK_LINES = 1.92   # 导语多折一行的高度 = --fs-lead(1.2u) × --lh-body(1.6)。
                         # 见文件头第 4 条。乘 u 在运行时算。
INK_TOL = 0.5            # 墨迹中心距页面中线的上限（px）。见 RULES §4「居中对象是墨迹」。

PROBE = r"""
<script>
window.onerror = function(m, s, l){
  document.documentElement.setAttribute('data-probe-err', m + ' @line ' + l);
};
/* 同步执行，不用 requestAnimationFrame：带大量 base64 图片的页面
   首帧可能晚于 --dump-dom，探针会静默不跑。getBoundingClientRect()
   本身就会强制布局，不需要等帧。 */
(function(){
 try{
  var sc = Math.min(window.innerWidth/1280, window.innerHeight/720);
  /* ⚠️ 不要假设 deck 自己的 fit() 已经跑了。
     它可能没跑（JS 报错——删个元素就可能让整个 IIFE 死在半路），
     也可能根本没有 JS。那种情况下舞台没缩放，再拿 sc 去除，
     量出来的数会系统性偏小 1/sc 倍（视口 1400×813 时是 9.4%）。
     改成读**真正生效的 transform 矩阵**。 */
  var st = document.getElementById('stage'), applied = 1;
  if (st){
    var t = getComputedStyle(st).transform;
    if (t && t !== 'none'){
      var mm = t.match(/matrix\(([^,]+),/);
      if (mm){ var a = parseFloat(mm[1]); if (isFinite(a) && a > 0) applied = a; }
    }
  }
  var sc = applied;
  var o = {scale:+applied.toFixed(4), expect:+Math.min(window.innerWidth/1280, window.innerHeight/720).toFixed(4),
           vw:window.innerWidth, vh:window.innerHeight, slides:[], ink:[]};
  o.u = getComputedStyle(document.documentElement).getPropertyValue('--u').trim();

  document.querySelectorAll('.slide').forEach(function(s, i){
    var prev = s.style.display; s.style.display = 'flex';
    var b = s.querySelector('.body');
    var it = {p:i+1, body:0, extent:0, overV:0, overH:0, over:0, fills:false};
    if (b){
      var bb = b.getBoundingClientRect();
      it.body = +(bb.height/sc).toFixed(1);
      /* 纵向比 .body 盒子：它下面紧跟着落点句和页脚，越界就是碰撞。
         横向比「幻灯片物理边缘」而不是内容框：页边距是空白，
         色带/强调块向外渗一点是有意的设计手法（例如 Reviewer deck
         用 margin:0 -19px 让底色行的文字与上下行对齐，
         渗进 76px 的页边距里还剩 57px）。
         只有真的顶到画面边缘、会被裁掉，才算错。 */
      var sr = s.getBoundingClientRect();
      var worstV = 0, worstH = 0, deepest = 0;
      function probe(r){
        if (!r.width && !r.height) return;
        worstV  = Math.max(worstV,  r.bottom - bb.bottom);
        worstH  = Math.max(worstH,  r.right  - sr.right);
        deepest = Math.max(deepest, r.bottom - bb.top);
      }
      b.querySelectorAll('*').forEach(function(el){
        if (parseFloat(getComputedStyle(el).flexGrow) > 0) it.fills = true;
        probe(el.getBoundingClientRect());
      });
      /* 文本节点单独量：stretch 之下越界的是文字，不是盒子 */
      var rg = document.createRange();
      b.querySelectorAll('*').forEach(function(el){
        for (var n = el.firstChild; n; n = n.nextSibling){
          if (n.nodeType !== 3 || !n.nodeValue.trim()) continue;
          rg.selectNodeContents(n);
          probe(rg.getBoundingClientRect());
        }
      });
      it.overV = +(worstV/sc).toFixed(1);
      it.overH = +(worstH/sc).toFixed(1);
      it.over  = +(Math.max(worstV, worstH)/sc).toFixed(1);
      it.extent= +(deepest/sc).toFixed(1);
    }
    /* 标题行数 —— 量出来的，不靠数 <br>。
       内容页标题必须一行（红竖块高 2.4u ＝ 一行；两行时竖块只够第一行，
       而且白白吃掉 67px）。封面例外（.cover h1 是 84px 的品牌标题，
       旁边没有竖块，两行是设计）。 */
    var h1 = s.querySelector('h1');
    if (h1){
      var cs = getComputedStyle(h1);
      var lh = parseFloat(cs.lineHeight);
      if (!isFinite(lh) || lh <= 0) lh = parseFloat(cs.fontSize) * 1.16;
      var hh = h1.getBoundingClientRect().height / sc;
      it.h1lines = Math.max(1, Math.round(hh / lh));
      it.h1br = /<br\s*\/?>/i.test(h1.innerHTML);
      it.cover = s.classList.contains('cover');
    } else {
      it.h1lines = 0;
    }
    /* 墨迹居中 —— 同一套算法在 scripts/inkcenter.py（由它写补偿值），两边必须一致。
       CSS 居中的是**字宽盒子**（含尾部字距），人眼看的是**墨迹外沿**；两者差在
       字形左右边距，短字符串尤其明显（Q & A 与页脚那句差 3–4px）→ 两段空隙不等宽。
       见 RULES §4「居中对象是墨迹，不是字宽盒子」。 */
    var sr2 = s.getBoundingClientRect();
    [['.qa','qa'], ['.foot .ftitle','ft']].forEach(function(t){
      s.querySelectorAll(t[0]).forEach(function(el){
        var rg2 = document.createRange(); rg2.selectNodeContents(el);
        var rs2 = rg2.getClientRects(), l2 = 1e9, r2 = -1e9;
        for (var k = 0; k < rs2.length; k++){
          if (!rs2[k].width && !rs2[k].height) continue;
          l2 = Math.min(l2, rs2[k].left); r2 = Math.max(r2, rs2[k].right);
        }
        if (l2 > r2) return;
        var cs2 = getComputedStyle(el);
        var cvv = document.createElement('canvas'), cx2 = cvv.getContext('2d');
        cx2.font = cs2.fontStyle + ' ' + cs2.fontWeight + ' ' + cs2.fontSize + ' ' + cs2.fontFamily;
        if ('letterSpacing' in cx2) cx2.letterSpacing = cs2.letterSpacing;
        var mm = cx2.measureText(el.textContent);
        /* 墨迹中心相对字宽盒子中心的偏移（canvas 的 actualBoundingBox* 是墨迹外沿）*/
        var inkOff = ((-mm.actualBoundingBoxLeft + mm.actualBoundingBoxRight) / 2)
                   - ((r2 - l2) / sc) / 2;
        var inkCenter = ((l2 + r2) / 2 - sr2.left) / sc + inkOff;
        o.ink.push({p:i+1, key:t[1], off:+(inkCenter - sr2.width/sc/2).toFixed(3)});
      });
    });
    o.slides.push(it);
    s.style.display = prev;
  });

  document.documentElement.setAttribute('data-probe', JSON.stringify(o));
 }catch(e){
  document.documentElement.setAttribute('data-probe-err', 'CAUGHT: ' + e.message);
 }
})();
</script>
"""

FORBIDDEN = [
    ("box-shadow",
     "Chrome 用软掩码(/SMask + /Luminosity)实现模糊阴影，不支持的阅读器会渲染成实心灰块。"
     "屏幕样式里可以用，但必须在 @media print 里关掉。"),
    ("linear-gradient",
     "同理会产生软掩码（图片型）。用纯色 + background-size 复刻色带。"),
]


def find_chrome():
    # headless shell 优先 —— 它不会让 Dock 跳
    for c in (*HEADLESS_SHELL_CANDIDATES, *CHROME_CANDIDATES):
        if c and os.path.exists(c):
            return c
    sys.exit("✗ 找不到 Chrome / chrome-headless-shell。请改 check.py 里的 CHROME_CANDIDATES。")


def headless_flag(chrome):
    """chrome-headless-shell 自带 headless，不接受 --headless=new；主程序必须显式给。"""
    if "chrome-headless-shell" in os.path.basename(chrome):
        return []
    return ["--headless=new"]


def strip_print_blocks(css):
    """剥掉 @media print{...}，返回 (剩余, 被剥掉的块)。

    print 块里的 box-shadow:none 是修复不是违规。被剥掉的块要**原样返回**
    —— 不能只回传剩余再让调用方按长度切片：print 块在文件中间时
    切片会错位，handled 判断就不可靠了。
    """
    out, removed, i = [], [], 0
    while True:
        m = re.search(r"@media\s+print\s*\{", css[i:])
        if not m:
            out.append(css[i:])
            break
        out.append(css[i:i + m.start()])
        j = i + m.end()
        depth = 1
        while j < len(css) and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        removed.append(css[i + m.start():j])
        i = j
    return "".join(out), removed


def static_checks(src, path=None):
    n = 0
    print("静态检查")

    pats = [r'src="http', r'href="http', r'url\(http', r'@import', r'<script\s+src']
    hits = [p for p in pats if re.search(p, src)]
    if hits:
        print(f"  ✗ 外部引用：{hits}   —— 必须零依赖，离线可用")
        n += 1
    else:
        print("  ✓ 零外部引用（可离线）")

    block = re.search(r"<style>(.*?)</style>", src, re.S)
    css = re.sub(r"/\*.*?\*/", "", block.group(1), flags=re.S) if block else ""
    raw_css = block.group(1) if block else ""
    screen_css, print_blocks = strip_print_blocks(css)
    print_css = "".join(print_blocks)
    # @media print 里把阴影关掉了 —— 那是对的做法，不是违规
    handled = {t: bool(re.search(re.escape(t) + r"\s*:\s*none", print_css)) for t, _ in FORBIDDEN}

    for token, why in FORBIDDEN:
        in_screen = len(re.findall(re.escape(token), screen_css))
        if in_screen and handled[token]:
            print(f"  ✓ 屏幕样式用了 {token}，但 @media print 里关掉了")
            print(f"      —— 这是对的。真判据是导出 PDF 的 /SMask = 0，用 --pdf 查。")
        elif in_screen:
            print(f"  ✗ 屏幕样式里出现 {token}（{in_screen} 处）—— {why}")
            n += 1
        else:
            print(f"  ✓ 未使用 {token}")

    if not re.search(r"var\(--u\)", raw_css):
        print("  ·  没找到 --u（这份 deck 不是这套模板建的，容量数字仅供参考）")

    # 边界完整性 —— setpages.py 靠这两个标记切页，多了少了都是结构坏了。
    # 多一个标记 = 有一页的半截内容漏到外面了；少一个 = 外壳被切掉一块。
    # 页面上完全看不出来，但 setpages.py 下次读写就会算错边界。
    n_a, n_b = src.count('<div id="stage">'), src.count("</div><!-- /#stage -->")
    if n_a == 1 and n_b == 1:
        print("  ✓ #stage 边界唯一（setpages.py 可安全读写）")
    else:
        print(f"  ✗ #stage 边界不唯一：'<div id=\"stage\">' ×{n_a}·"
              f"'</div><!-- /#stage -->' ×{n_b}）")
        print("      —— 应为各 1。结构已被破坏，setpages.py 会拒绝读写。")
        n += 1

    # 结构顺序：① 封面 → ② 目录。
    # 顺序错了页面上「看着都正常」—— 2026-09-17 真发生过：插目录时的锚点字串是 'Why'，
    # 命中了封面标题 "Why your plan needs a smarter reviewer"，目录被插到封面之前；
    # 而目检那一步又正好把封面看成了目录 —— 两道防线同时失效（FAILURES.md F8c）。
    secs = re.findall(r'<section class="slide.*?</section>', src, re.S)
    if secs and re.search(r"var\(--u\)", raw_css):
        m0 = re.search(r'<section class="([^"]*)"', secs[0])
        first_is_cover = bool(m0) and "cover" in m0.group(1)
        second_is_toc = len(secs) > 1 and 'class="toc"' in secs[1]
        if first_is_cover and second_is_toc:
            print("  ✓ 结构顺序：① 封面 → ② 目录")
        else:
            if not first_is_cover:
                print("  ✗ 第 1 页不是封面 —— 封面必须排在最前（RULES.md 页型表）")
            elif len(secs) < 2:
                print("  ✗ 没有第 2 页 —— 每份 deck 必有目录页（RULES.md 页型表）")
            else:
                print("  ✗ 第 2 页不是目录 —— 目录必须紧跟封面（RULES.md 页型表）")
            n += 1

        # 每个内容页都要有 h1（这页的论点）。省掉它的页只剩图与骨架，
        # 观众不知道在看什么 —— 2026-09-17 真发生过：把实拍页做成纯图页，
        # 页面上「看着还行」，但那页没有论点。
        no_h1 = [i for i, s in enumerate(secs, 1) if not re.search(r'<h1[^>]*>', s)]
        if no_h1:
            print(f"  ✗ 这些页没有 h1：{no_h1} —— 每个内容页都要有论点（RULES.md 骨架）")
            n += 1
        else:
            print("  ✓ 每页都有 h1")

        # 已废弃的零件：页眉（.head / .kicker / .hline）2026-09-17 被标题行 .ttl 取代。
        # 老模板里它们长得像「标题」，混回来就又变成一页两个标题。
        # 最后一页必须是收尾页（.end ＋ 正中 .qa）。讲完停在屏幕上的是它 ——
        # 提问期间观众一直看的就是这一页，所以它有固定形态：标题 Thanks ＋ Q & A。
        last = secs[-1]
        last_cls = re.search(r'<section class="([^"]*)"', last).group(1)
        if "end" in last_cls and 'class="qa"' in last:
            print("  ✓ 收尾页：.end ＋ 正中 Q & A")
        else:
            print(f"  ✗ 最后一页不是收尾页（class='{last_cls}'）—— 每份 deck 的最后一页"
                  f"必须是 .end ＋ 标题 Thanks ＋ 正中 .qa（RULES.md 页型表）")
            n += 1

        dead = [k for k in ('class="head"', 'class="kicker"', 'class="hline"') if k in src]
        if dead:
            print(f"  ✗ 用了已废弃的页眉零件：{dead} —— 改用 .ttl（红竖块 ＋ 红 h1），"
                  f"标题行不再要小节标签（RULES.md 骨架）")
            n += 1

    # title —— 三次冷启动测试里，一个 agent 把模板的 title 原样交付了。
    # <title> 住在外壳里，页面上完全看不见，但窗口标题/浏览器标签/PDF 元数据都用它。
    TPL_TITLE = "幻灯片规范 · 零件库"
    m = re.search(r"<title>(.*?)</title>", src, re.S)
    t = m.group(1).strip() if m else ""
    import os as _os
    is_tpl = _os.path.basename(path or "") in ("template.html", "demo.html")
    if not t:
        print("  ✗ 没有 <title> —— 交付前补上（setpages.py --title '…'）")
        n += 1
    elif t == TPL_TITLE and not is_tpl:
        print(f"  ✗ title 还是模板的（{t!r}）—— 交付物里不该有开发档案")
        print("      改：./scripts/setpages.py <deck> --title '这份 deck 的全名'")
        n += 1
    else:
        print(f"  ✓ title：{t!r}")

    print()
    return n


def overflow_check(chrome, path):
    src = open(path, encoding="utf-8").read()
    if "</body>" not in src:
        sys.exit("✗ 找不到 </body>，无法注入探针。")
    tmp = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8")
    tmp.write(src.replace("</body>", PROBE + "\n</body>"))
    tmp.close()
    try:
        r = subprocess.run(
            [chrome, *headless_flag(chrome), "--disable-gpu", "--window-size=1400,900",
             "--virtual-time-budget=3000", "--dump-dom", "file://" + tmp.name],
            capture_output=True, text=True, timeout=120)
        m = re.search(r'data-probe="(.*?)"', r.stdout, re.S)
        if not m:
            err = re.search(r'data-probe-err="(.*?)"', r.stdout, re.S)
            why = htmlmod.unescape(err.group(1)) if err else "探针没被执行"
            sys.exit(f"✗ 探针失败：{why}")
        data = json.loads(htmlmod.unescape(m.group(1)))
    finally:
        os.unlink(tmp.name)

    print(f"逐页容量（--u = {data['u'] or '未定义'}，视口 {data['vw']}×{data['vh']}，缩放已校正，")
    # ⚠️ deck 自己的 fit() 没跑 = 它的 JS 死在半路了。
    # 后果不只是缩放：初始的 apply()、页码、深链接全部不执行。
    exp = data.get('expect', 1)
    if abs(data['scale'] - exp) > 0.01 and abs(exp - 1) > 0.01:
        print(f"\n   ⚠️  舞台没有被缩放（实际 {data['scale']} · 预期 {exp}）。"
              f"\n       deck 的 fit() 没有执行 —— 它的 JS 很可能报错死了。"
              f"\n       在浏览器里后果是：舞台不居中、页码不填、#N 深链接失效。"
              f"\n       下面所有数字已按**实际生效的** {data['scale']} 校正，是可信的；"
              f"\n       但请先去修 JS。\n")
    # 门槛从 u 推出来，不写死。u 未定义（这份 deck 不是这套模板建的）时
    # 不拿它判死 —— 只把余量列出来给人看，硬溢出仍然算错。
    try:
        u_px = float(re.match(r"([\d.]+)px", data["u"].strip()).group(1))
        min_slack = MIN_SLACK_LINES * u_px
        thr = f"余量门槛 {min_slack:.0f}px（= 导语一行 {MIN_SLACK_LINES}×u）"
    except Exception:
        min_slack = None
        thr = "u 未定义 —— 余量仅列出，不判死（只拦硬溢出）"
    print("          " + thr + "）")
    print("   页   正文区     内容伸到    余量      越界(纵/横)")
    bad = 0
    for s in data["slides"]:
        if not s["body"]:
            print(f"  {s['p']:3d}      —          —         —")
            continue
        slack = s["body"] - s["extent"]
        dim = f"{s['overV']:+7.1f} /{s['overH']:+7.1f}"
        if s["over"] > 0.5:
            note = "✗ 溢出"
            bad += 1
        elif s["fills"] and slack < 1:
            note = "✓ 弹性元素填满"
        elif min_slack is not None and slack < min_slack:
            note = (f"△ 余量 {slack:.1f}px < {min_slack:.0f}px："
                    f"换台机器字体换行差一行就会溢出")
            bad += 1
        elif slack < 20:
            note = f"△ 余量偏少（这份 deck 没有 u，不判死）"
        elif s["body"] > 0 and slack / s["body"] > 0.45:
            # 「太空」也是缺陷 —— 但它是判断题，所以只警告不判失败。
            # 45% 这条线来自实测：模板自己 11 页最高 42%，四份冷启动产物最高 40%，
            # 唯一视觉上明显半页空着的那页是 51%。见 FAILURES F9。
            note = (f"⚠️  余量 {slack / s['body'] * 100:.0f}%（>45%）—— 半页空着："
                    f"加点内容，或换原型")
        else:
            note = "✓"
        print(f"  {s['p']:3d}  {s['body']:7.1f}  {s['extent']:9.1f}  {slack:+8.1f}   {dim}   {note}")

    # 标题行数：内容页必须一行。红竖块高 2.4u ＝ h1 一行，两行时竖块只够第一行，
    # 而且白白吃掉一行标题的高度（67px）。封面例外。
    multi = [s for s in data["slides"]
             if s.get("h1lines", 0) >= 2 and not s.get("cover")]
    brs = [s["p"] for s in multi if s.get("h1br")]
    if multi:
        pages = "、".join(str(s["p"]) for s in multi)
        print(f"  ✗ 标题两行：页 {pages}")
        if brs:
            print(f"      —— 页 {'、'.join(map(str, brs))} 是写作时手动折的（<h1> 里有 <br>）")
        print("      改成一行：删字，不要缩字号（铁律 5）。红竖块只对齐一行。")
        bad += 1
    else:
        print("  ✓ 标题全部一行（封面除外）")

    bad += ink_check(data, src)
    print()
    return bad


def ink_check(data, src):
    """墨迹居中：.qa 与 .foot .ftitle 的墨迹中心必须落在页面中线上。

    CSS 居中的对象是**字宽盒子**，人眼看的却是**墨迹** —— 两者差在字形左右
    边距（Q 左肩 14.8px ≠ A 右肩 13.8px）。短字符串放大这个不对称：
    「Q&A 到页脚左端」与「A 到页脚右端」差 2×(0.83+0.66) ≈ 3px，实测 4px；
    空隙短时占 6%，看得出来。补偿值由 scripts/inkcenter.py 量测写入。
    见 RULES §4「居中对象是墨迹，不是字宽盒子」。

    分两档（渐进铺开）：登记过 --ink-dx-* 的 deck **硬判**；没登记的只警告
    —— 模板与 demo 的字体被剥掉了，量出来的是回退字体的边距，不该拿它判死。
    """
    items = data.get("ink") or []
    if not items:
        return 0
    names = {"qa": "Q & A（.qa）", "ft": "页脚 deck 名（.foot .ftitle）"}
    by = {}
    for it in items:
        by.setdefault(it["key"], []).append(it)
    print("墨迹居中（居中对象是墨迹，不是字宽盒子）")
    bad = 0
    for key in [k for k in ("qa", "ft") if k in by]:
        worst = max(by[key], key=lambda x: abs(x["off"]))
        off, page = worst["off"], worst["p"]
        registered = re.search(r"--ink-dx-" + re.escape(key) + r"\s*:", src)
        if abs(off) <= INK_TOL:
            print(f"  ✓ {names[key]}：墨迹中心距页面中线 {off:+.3f}px（最差页 {page}）")
        elif registered:
            print(f"  ✗ {names[key]}：墨迹偏 {off:+.3f}px（第 {page} 页）> {INK_TOL}px")
            print("      已登记 --ink-dx-%s 但残差超限 —— 文案或字号变过？重跑：" % key)
            print("      \"$SKILL/scripts/inkcenter.py\" <deck.html>   然后重跑 build.sh")
            bad += 1
        else:
            print(f"  ⚠️  {names[key]}：墨迹偏 {off:+.3f}px（未登记补偿，不判死）")
            print("      新 deck 跑一次：\"$SKILL/scripts/inkcenter.py\" <deck.html>")
            print("      （模板/demo 的字体已被剥掉，量出来的是回退字体，忽略本条）")
    return bad


# 填充语 / 夸张词 / 黑话 —— 「占了位置但不装信息」的词。
# 规范借自 read-paper skill 的写作规范（2026-09-17）：目标是让读者一眼抓住结论，
# 不是展示文采。幻灯片比论文报告更狠一层 —— **观众没有回读的机会**。
# 引用别人的原话时可以保留：用「」/“” 括起来即豁免（同 read-paper 的「标明是引用」）。
# 为什么查**可见文字**而不查讲者备注：备注是口头脚本，口语词在说话时是自然的；
# 这一条管的是观众看到的字。见 RULES §7「文风」。
PROSE_BANNED = [
    ("值得注意的是", "填充语"), ("需要注意的是", "填充语"), ("不难看出", "填充语"),
    ("众所周知", "填充语"), ("综上所述", "填充语"), ("总而言之", "填充语"),
    ("毋庸置疑", "填充语"), ("显而易见", "填充语"), ("一言以蔽之", "填充语"),
    ("颠覆性", "夸张"), ("革命性", "夸张"), ("史上最", "夸张"), ("无与伦比", "夸张"),
    ("极致", "夸张"), ("完美", "夸张"), ("神器", "夸张"), ("秒杀", "夸张"),
    ("逆天", "夸张"), ("炸裂", "夸张"), ("爆表", "夸张"),
    ("说白了", "口语"), ("搞一下", "口语"), ("搞定", "口语"),
    ("赋能", "黑话"), ("闭环", "黑话"), ("抓手", "黑话"), ("打法", "黑话"),
    ("颗粒度", "黑话"), ("组合拳", "黑话"),
]


def visible_text(src):
    """观众看到的字：去掉讲者备注、style/script、注释、标签。"""
    s = re.sub(r'data-notes=".*?"', "", src, flags=re.S)
    s = re.sub(r"<style>.*?</style>", "", s, flags=re.S)
    s = re.sub(r"<script>.*?</script>", "", s, flags=re.S)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    return re.sub(r"<[^>]+>", " ", s)


def prose_check(src):
    """文风：不用填充语、夸张词、黑话（read-paper 的写作规范，见 RULES §7）。

    这类词的危险不在难听，在于**占位置**：一页只能放这么多字，
    「值得注意的是」占掉的那七个字本可以是证据。
    """
    txt = visible_text(src)
    quoted = "".join(re.findall(r"「[^」]*」|“[^”]*”", txt))    # 引用豁免
    hits = []
    for word, kind in PROSE_BANNED:
        n = txt.count(word) - quoted.count(word)
        if n > 0:
            hits.append((word, kind, n))
    print("文风（只看观众看到的字）")
    if not hits:
        print("  ✓ 没有填充语 / 夸张词 / 黑话")
    else:
        for word, kind, n in hits:
            print(f"  ✗ {kind}：「{word}」（{n} 处）—— 占位置不装信息，删掉或换成人话")
        print("      引用别人的原话可以保留：用「」括起来即豁免。")
        print("      清单在 check.py 的 PROSE_BANNED，规范见 RULES §7「文风」。")
    return 1 if hits else 0


def font_coverage_check(src):
    """内联字体里有没有缺字。

    子集是按「当时 deck 里出现的字符」算的。之后改文案如果引入了新字符，
    它不在子集里 → 静默掉回系统字体 → PDF 里多出一堆 Type 3。
    这个错误在屏幕上几乎看不出来（回退字体长得差不多），必须机械检查。

    依赖 fontTools+brotli；没装就跳过，不报错。
    """
    import base64
    import io
    faces = re.findall(
        r"@font-face\{font-family:(Slide[\w-]+);[^}]*?base64,([A-Za-z0-9+/=]+)", src)
    if not faces:
        print("字体检查：没有内联字体（build_font.py 还没跑）")
        print("  △ 界面上的字体来自系统，换台机器会变；PDF 里可能出 Type 3\n")
        return 0
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        print("字体检查：跳过（没装 fontTools）\n")
        return 0

    covered = set()
    for _, b64 in faces:
        try:
            covered |= set(TTFont(io.BytesIO(base64.b64decode(b64)), lazy=True).getBestCmap())
        except Exception as e:
            print(f"  ✗ 解码内联字体失败：{e}\n")
            return 1

    # deck 里真正会显示的字符：剔除 style/script/注释/标签
    body = re.sub(r"<style[^>]*>.*?</style>", " ", src, flags=re.S)
    body = re.sub(r"<script[^>]*>.*?</script>", " ", body, flags=re.S)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    # UI 层（调参面板等）打印时不显示，缺字无所谓
    body = re.sub(r'<div id="(?:panel|pbtn|help|notes)".*?</div>\s*(?=<)', " ", body, flags=re.S)
    body = re.sub(r"<[^>]+>", " ", body)
    # 数字实体（&#x2500;）渲染出的字符才是要查的 —— 不解码的话，
    # 缺字会以 ASCII 实体文本的形态静默漏检（F4 的一个入口）。
    body = htmlmod.unescape(body)
    used = {c for c in body if ord(c) > 0x20 and c not in "\n\t"}
    miss = sorted(c for c in used if ord(c) not in covered)

    fams = sorted({f for f, _ in faces})
    print(f"字体检查：内联 {len(faces)} 个子集（{'、'.join(fams)}），"
          f"覆盖 {len(covered)} 个码位")
    if miss:
        print(f"  ✗ 有 {len(miss)} 个字符不在子集里：")
        print(f"      {''.join(miss[:40])}")
        for c in miss[:6]:
            print(f"      U+{ord(c):04X}  {c!r}")
        print(f"  → 这些字会掉回系统字体（屏幕上可能看不出来），")
        print(f"     PDF 里会变成 Type 3。修法：重新跑 build_font.py")
        print()
        return 1
    print(f"  ✓ {len(used)} 个在用的字符全部命中\n")
    return 0


def pdf_check(path):
    print(f"PDF 检查：{path}")
    if not os.path.exists(path):
        print("  ✗ 文件不存在\n")
        return 1
    raw = open(path, "rb").read()
    n = 0
    # /SMask 不一定来自 CSS：带 alpha 通道的位图也会产生合法 /SMask。
    # PDF 里有位图时无法用字节扫描区分两者 —— 降级为警告并给出修法；
    # 没有位图时 /SMask 只能来自阴影/渐变，仍判失败。
    has_images = b"/Subtype /Image" in raw or b"/Subtype/Image" in raw
    for tok in (b"/SMask", b"/Luminosity"):
        c = raw.count(tok)
        if tok == b"/SMask" and c and has_images:
            print(f"  △ {tok.decode():12s} {c}   —— PDF 里有位图，无法区分软掩码和图片透明通道")
            print(f"      若打印发灰：把截图压平成无 alpha 再嵌入")
        else:
            print(f"  {'✓' if c == 0 else '✗'} {tok.decode():12s} {c}"
                  + ("" if c == 0 else "   —— 软掩码，某些阅读器会渲染成灰块"))
            n += (c > 0)

    def tool(name, args):
        try:
            return subprocess.run([name] + args, capture_output=True, text=True).stdout
        except FileNotFoundError:
            return ""

    info = tool("pdfinfo", [path])
    pages = re.search(r"^Pages:\s+(\d+)", info, re.M)
    size = re.search(r"^Page size:\s+(.+)$", info, re.M)
    if pages:
        print(f"  · 页数      {pages.group(1)}")
    if size:
        ok = "960 x 540" in size.group(1)
        print(f"  {'✓' if ok else '✗'} 页面尺寸  {size.group(1).strip()}"
              + ("" if ok else "   —— 应为 960 x 540 pts"))
        n += (not ok)

    fonts = tool("pdffonts", [path])
    if fonts:
        t3 = len(re.findall(r"\bType 3\b", fonts))
        tot = max(0, len(fonts.strip().splitlines()) - 2)
        if t3:
            print(f"  ✗ 字体      {tot} 个子集，其中 {t3} 个是 Type 3")
            print(f"              └ 静态内联字体应该全部导成 CID TrueType。")
            print(f"                出现 Type 3 通常说明：有文字掉回了系统字体（看上面的字体检查），")
            print(f"                或界面元素没在 @media print 里隐藏。")
            n += 1
        else:
            print(f"  ✓ 字体      {tot} 个子集，无 Type 3")
    print()
    return n


def self_test():
    """跑 assets/tests/ 下的反向用例。§7-2 要求：对故意改坏的文件必须报错。"""
    here = os.path.dirname(os.path.abspath(__file__))
    # 反向用例住在 assets/tests/ —— 从脚本所在目录依次找几个可能的位置
    tdir = next((d for d in (os.path.join(here, "tests"),
                             os.path.join(here, "..", "assets", "tests"),
                             os.path.join(here, "assets", "tests"))
                 if os.path.isdir(d)), None)
    if not tdir:
        print("✗ 找不到 assets/tests/ 目录（--self-test 要用）")
        return 1
    chrome = find_chrome()
    expect = {"bad_A.html": True, "bad_B.html": True, "bad_C.html": True,
              "good.html": False}
    fails = 0
    print("自检：反向用例")
    for fn in sorted(os.listdir(tdir)):
        if not fn.endswith(".html"):
            continue
        path = os.path.join(tdir, fn)
        src = open(path, encoding="utf-8").read()
        n = static_checks(src, path)
        n += prose_check(src)
        n += overflow_check(chrome, path)
        should_fail = expect.get(fn, True)
        got = n > 0
        ok = got == should_fail
        print(f"  {'✓' if ok else '✗'} {fn}: 期望 {'报错' if should_fail else '通过'}，"
              f"实际 {'报错' if got else '通过'}")
        print()
        fails += (not ok)
    if fails:
        print(f"✗ 自检失败 {fails} 项")
        return 1
    print("✓ 自检全部符合预期")
    return 0


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    if sys.argv[1] == "--self-test":
        sys.exit(self_test())

    path = sys.argv[1]
    pdf = None
    if "--pdf" in sys.argv:
        i = sys.argv.index("--pdf")
        pdf = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
    if not os.path.exists(path):
        sys.exit(f"✗ 找不到 {path}")

    src = open(path, encoding="utf-8").read()
    bad = static_checks(src, path)
    bad += prose_check(src)
    bad += font_coverage_check(src)
    bad += overflow_check(find_chrome(), path)
    if pdf:
        bad += pdf_check(pdf)

    if bad:
        print(f"✗ 发现 {bad} 个问题，不要发布。")
        sys.exit(1)
    print("✓ 全部通过。")


if __name__ == "__main__":
    main()

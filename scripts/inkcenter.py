#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按「墨迹」居中（ink / optical centering）—— 量测并写入 deck 的光学补偿值。

为什么需要它
────────────
CSS 居中的对象是「**字宽盒子**」(advance box)：从第一个字形的起点到最后一个
字形的**步进宽度**（含最后一个字后面的 letter-spacing）。人眼看的却是「**墨迹**」
——真正落墨的那点像素。两者差在字形的左右边距 (side bearing)：

    "Q & A"  左肩 14.8px、右肩 13.8px（含尾部字距）→ 墨迹比盒子中心偏 **+0.5px**
    页脚长句 左肩  4.6px、右肩  7.6px             → 墨迹比盒子中心偏 **−1.5px**

于是「Q&A 到页脚左端」和「A 到页脚右端」两段空隙差 2×2.0 = 4px —— 短字符串
（Q 是圆形字，左肩大；A 右肩小）放大了这个不对称，长句把它平均掉。屏幕上看不出来
的 4px 在 1280 舞台上是 0.14u，但两段空隙**上下对齐**时人眼比的是比例：
空隙 98px 时差 4px = 4%，空隙 58px 时差 3px = 6%（Reviewer 尾页更刺眼）。

怎么补
──────
用 canvas `measureText().actualBoundingBoxLeft/Right` 量出真正的墨迹外沿（亚像素，
与光栅化结果差 <0.5px），算出「墨迹中心 − 字宽盒子中心」的偏移 `inkOff`，
再算出让墨迹落在页面中线上的平移量，写进 deck 的 CSS 变量：

    .qa            { transform: translateX(var(--ink-dx-qa, 0px)) }
    .foot .ftitle  { transform: translateX(var(--ink-dx-ft, 0px)) }

数值落在 `:root` 的「光学居中（墨迹）」标记块里，由本脚本管理，**禁止手改**。
transform 只改绘制位置，不动布局（不影响 flex/grid 排布，也不影响页脚三栏）。

用法
────
    inkcenter.py <deck.html>           量测 → 写入 → 复测（幂等，值不变则不动文件）
    inkcenter.py <deck.html> --dry-run 只量测并打印表格
    inkcenter.py <deck.html> --json    机器可读输出（供 check.py 断言用）

退出码：0 = 已居中（残差 ≤0.25px）；1 = 需要补偿 / 挂钩缺失 / 文案不一致。
"""
import html as htmlmod
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    shutil.which("google-chrome") or "",
    shutil.which("google-chrome-stable") or "",
    shutil.which("chromium") or "",
    shutil.which("chromium-browser") or "",
]

# (选择器, CSS 变量名, 人读名)
TARGETS = [
    (".qa", "qa", "Q & A"),
    (".foot .ftitle", "ft", "页脚 deck 名"),
]

STEP = 0.25        # 写入值的量化步长（px）
TOL = 0.25         # 复测残差上限（px）：墨迹中心距页面中线
CONSIST = 0.3      # 同一 key 在多页出现时，各页量出的补偿量必须一致到这个精度。
                   # 同一个字符串应该完全一致（实测抖动 0）；不同字符串差 0.5px 以上

BEGIN = "/* ── 光学居中（墨迹） begin"
END = "/* ── 光学居中（墨迹） end"

PROBE = r"""
<script>
window.onerror=function(m,s,l){document.documentElement.setAttribute('data-probe-err',m+' @line '+l);};
(function(){
try{
 var st=document.getElementById('stage'), sc=1;
 if(st){var t=getComputedStyle(st).transform;
   if(t&&t!=='none'){var mm=t.match(/matrix\(([^,]+),/); if(mm){var a=parseFloat(mm[1]); if(isFinite(a)&&a>0) sc=a;}}}
 var TARGETS=__TARGETS__;
 var out=[];
 document.querySelectorAll('.slide').forEach(function(s,i){
   var prev=s.style.display; s.style.display='flex';
   var sr=s.getBoundingClientRect(), center=sr.width/sc/2;
   TARGETS.forEach(function(t){
     s.querySelectorAll(t[0]).forEach(function(el){
       var rg=document.createRange(); rg.selectNodeContents(el);
       var rs=rg.getClientRects(), l=1e9, r=-1e9;
       for(var k=0;k<rs.length;k++){
         if(!rs[k].width && !rs[k].height) continue;
         l=Math.min(l,rs[k].left); r=Math.max(r,rs[k].right);
       }
       if(l>r) return;
       /* 元素自身已有的 translateX（上一次补偿）—— 量出来的 adv 已经含它 */
       var tx=0, tr=getComputedStyle(el).transform;
       if(tr && tr!=='none'){var m2=tr.match(/matrix\(([^)]+)\)/);
         if(m2){var p=m2[1].split(','); if(p.length===6) tx=parseFloat(p[4]);}}
       var cs=getComputedStyle(el);
       var cv=document.createElement('canvas'), cx=cv.getContext('2d');
       cx.font=cs.fontStyle+' '+cs.fontWeight+' '+cs.fontSize+' '+cs.fontFamily;
       if('letterSpacing' in cx) cx.letterSpacing=cs.letterSpacing;
       var m=cx.measureText(el.textContent);
       var aL=m.actualBoundingBoxLeft, aR=m.actualBoundingBoxRight;
       out.push({slide:i+1, sel:t[0], key:t[1], center:+center.toFixed(3),
                 adv:+(((l+r)/2-sr.left)/sc).toFixed(3), advW:+((r-l)/sc).toFixed(3),
                 tx:+tx.toFixed(3), canvasW:+m.width.toFixed(3),
                 inkW:+((aR+aL)).toFixed(3),
                 inkOff:+(((-aL+aR)/2)-((r-l)/sc)/2).toFixed(3),
                 text:el.textContent});
     });
   });
   s.style.display=prev;
 });
 document.documentElement.setAttribute('data-probe', JSON.stringify({scale:sc, items:out}));
}catch(e){document.documentElement.setAttribute('data-probe-err','CAUGHT: '+e.message);}
})();
</script>
"""


def find_chrome():
    for c in CHROME_CANDIDATES:
        if c and os.path.exists(c):
            return c
    sys.exit("✗ 找不到 Chrome。请改 inkcenter.py 里的 CHROME_CANDIDATES。")


def measure(chrome, path):
    """跑探针，返回 {key: {'need': 平移量, 'items': [...]}}。"""
    src = open(path, encoding="utf-8").read()
    if "</body>" not in src:
        sys.exit("✗ 找不到 </body>，无法注入探针。")
    probe = PROBE.replace("__TARGETS__", json.dumps([[t[0], t[1]] for t in TARGETS]))
    tmp = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8")
    tmp.write(src.replace("</body>", probe + "\n</body>"))
    tmp.close()
    try:
        r = subprocess.run(
            [chrome, "--headless=new", "--disable-gpu", "--window-size=1400,900",
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

    groups = {}
    for it in data["items"]:
        # 没有补偿时：adv(含旧 tx) − tx + inkOff
        it["adv0"] = round(it["adv"] - it["tx"], 3)      # 未补偿的字宽盒子中心（稳定，便于对照）
        it["need"] = round(it["center"] - (it["adv0"] + it["inkOff"]), 4)
        # 复测：当前实际落点
        it["now"] = round(it["adv"] + it["inkOff"] - it["center"], 4)
        groups.setdefault(it["key"], []).append(it)
    return data["scale"], groups


def quantize(v):
    return round(round(v / STEP) * STEP, 2)


def fmt(v):
    return f"{v:+.2f}".replace("+0.00", "0.00")


def patch_root(src, values, notes):
    """把补偿值写进 :root 的标记块（幂等：先删旧块再插新块）。"""
    m = re.search(r":root\s*\{", src)
    if not m:
        sys.exit("✗ 找不到 :root{ } —— 这份 deck 不是用本模板建的？")
    depth, i = 1, m.end()
    while i < len(src) and depth:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    head, body, tail = src[:m.end()], src[m.end():i - 1], src[i - 1:]

    if BEGIN in body:                       # 已有块 → 整块替换
        # ⚠️ 局部变量千万别叫 tail —— 外层 tail 是**文件**剩余部分，覆盖它会把
        # 整个文件截断（第一次就踩了）。
        i0 = body.index(BEGIN)
        seg = body[i0:]
        m2 = re.search(re.escape(END) + r"[^\n]*\n?", seg)
        i1 = i0 + (m2.end() if m2 else len(seg))
        body = body[:i0].rstrip() + "\n" + body[i1:]
    # 清掉历史残留：只有破折号 + */ 的碎行（半途失败的插入会留下它）。
    # CSS 里游离的 */ 是语法错误，会吃掉它后面第一条声明。
    body = re.sub(r"\n[ \t]*─+[ \t]*\*/[ \t]*", "", body)
    lines = [
        "  " + BEGIN + " ──────────────────────────────────────────",
        "     居中对象是**墨迹**，不是字宽盒子：字形左右边距不对称，短字符串尤其明显。",
        "     数值由 scripts/inkcenter.py 量测写入，**勿手改**；文案/字号变了就重跑它。 */",
    ]
    for _sel, key, txt in TARGETS:
        if key not in values:
            continue
        decl = f"--ink-dx-{key}: {values[key]}px;"
        lines.append(f"  {decl:<25}/* {txt}：{notes[key]} */")
    lines.append("  " + END + " ──────────────────────────────────────────── */")
    block = "\n".join(lines)
    body = body.rstrip()
    if not body.endswith("\n"):
        body += "\n"
    return head + body + block + "\n" + tail


def check_hooks(src):
    missing = [t for t in TARGETS
               if not re.search(r"translateX\(\s*var\(\s*--ink-dx-" + t[1], src)]
    if missing:
        sels = "、".join(t[0] for t in missing)
        sys.exit(f"✗ CSS 里缺少挂钩：{sels} 的规则上没有\n"
                 f"    transform: translateX(var(--ink-dx-<key>, 0px))\n"
                 f"  先把挂钩补进 CSS（模板与各 deck 的 <style> 都要有），再跑本脚本。")


def main():
    args = [a for a in sys.argv[1:]]
    as_json = "--json" in args
    # --json 也是**只读**：只量不写，输出机器可读结果（check.py / 排查用）。
    # 早先的版本 --json 会写文件 —— 那是个坑，调用方想看一眼却被改了 deck。
    dry = ("--dry-run" in args) or as_json
    paths = [a for a in args if not a.startswith("--")]
    if len(paths) != 1:
        sys.exit(__doc__.strip().split("用法\n────\n")[-1].split("\n\n")[0].strip())
    path = paths[0]

    chrome = find_chrome()
    src = open(path, encoding="utf-8").read()
    hooks = {key: bool(re.search(r"translateX\(\s*var\(\s*--ink-dx-" + key, src))
             for _sel, key, _t in TARGETS}
    registered = {key: bool(re.search(r"--ink-dx-" + key + r"\s*:", src))
                  for _sel, key, _t in TARGETS}
    if not dry:
        check_hooks(src)

    scale, groups = measure(chrome, path)
    if not groups:
        print("（本 deck 里没有需要光学居中的元素：.qa / .foot .ftitle 都没出现）")
        return 0

    values, notes, bad = {}, {}, 0
    residuals, counts = {}, {}
    for sel, key, txt in TARGETS:
        items = groups.get(key)
        if not items:
            continue
        needs = [it["need"] for it in items]
        spread = max(needs) - min(needs)
        if spread > CONSIST:
            # 拒绝写入：写了就是一页对、另一页错，而且 check.py 会报「已登记但超限」，
            # 很难看出真因。按 RULES §4，页脚中槽是 **deck 全名，每页相同**。
            print(f"✗ {sel} 在各页量出的补偿量不一致（差 {spread:.2f}px > {CONSIST}px）")
            by_text = {}
            for it in items:
                by_text.setdefault(it["text"], []).append(it["slide"])
            if len(by_text) > 1:
                print(f"   {txt} 在本 deck 里有 {len(by_text)} 种文案：")
                for t, pg in sorted(by_text.items(), key=lambda kv: -len(kv[1])):
                    pages = "、".join(str(p) for p in sorted(pg))
                    print(f"     第 {pages} 页：「{t}」（墨迹原偏 "
                          f"{fmt([i for i in items if i['text'] == t][0]['inkOff'])}px）")
                print(f"   页脚中槽的语义是 **deck 全名，每页相同**（RULES §4）——"
                      f"先改成同一个字符串，再跑本脚本。")
                print("   （模板/零件库这种多文案的地方才需要；真 deck 不该出现。）")
            else:
                print(f"   同一文案在同一页上量出了不同值 —— 字号或排版被逐页改过？")
            return 1
        it = items[0]
        v = quantize(it["need"])
        values[key] = v
        notes[key] = (f"墨迹原偏 {fmt(it['inkOff'])}px → 平移 {fmt(v)}px"
                      f"（字宽盒子中心 {it['adv0']:.2f}，页面中线 {it['center']:.2f}）")
        residuals[key] = max(abs(x["now"]) for x in items)
        counts[key] = len(items)

    if not as_json:
        print(f"逐页光学居中（舞台缩放 {scale:.3f}，页面中线 {groups[list(groups)[0]][0]['center']:.2f}）")
        print("   目标             字宽盒子中心  墨迹偏移   需要平移   写入    页数")
        for sel, key, txt in TARGETS:
            items = groups.get(key)
            if not items:
                continue
            it = items[0]
            print(f"   {sel:<16} {it['adv0']:>10.2f} {fmt(it['inkOff']):>10} "
                  f"{fmt(it['need']):>10} {fmt(values[key]):>8}px {len(items):>6}")
        print()

    if dry:
        if as_json:
            print(json.dumps({"registered": registered, "hooks": hooks,
                              "values": values, "residuals": residuals,
                              "worst": round(max(residuals.values()) if residuals else 0.0, 3),
                              "pages": counts}, ensure_ascii=False))
        return 1 if (not all(hooks.values()) or
                     any(abs(r) > TOL for r in residuals.values())) else 0

    new = patch_root(src, values, notes)
    if new != src:
        open(path, "w", encoding="utf-8").write(new)
    scale2, groups2 = measure(chrome, path)
    worst = 0.0
    if not as_json:
        print("复测（墨迹中心距页面中线）")
    for sel, key, txt in TARGETS:
        for it in groups2.get(key, []):
            res = it["now"]
            worst = max(worst, abs(res))
            if not as_json:
                flag = "✓" if abs(res) <= TOL else "✗"
                print(f"   {flag} {txt:<12} 第 {it['slide']:>2} 页  残差 {res:+.3f}px")
    if worst > TOL:
        print(f"\n✗ 仍有 {worst:.3f}px 偏差（上限 {TOL}px）—— 检查挂钩是否真的生效。")
        return 1
    if as_json:
        print(json.dumps({"registered": registered, "hooks": hooks,
                          "values": values, "residuals": residuals,
                          "worst": round(worst, 3), "pages": counts},
                         ensure_ascii=False))
    else:
        print(f"\n✓ 已按墨迹居中（最大残差 {worst:.3f}px）。"
              f"⚠️ 改过文字/字号后必须重跑本脚本，并重跑 build.sh。")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

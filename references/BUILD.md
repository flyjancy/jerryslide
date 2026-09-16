# BUILD.md · 怎么跑一份 deck

> **这份文档只讲操作。**
> 该做成什么样 → `references/RULES.md` · 做错了会怎样 → `references/FAILURES.md`
>
> **路径约定**：本 skill 的目录记为 `$SKILL`（`SKILL.md` 所在目录）。
> 本文所有 `scripts/…`、`assets/…`、`references/…` 都相对于 `$SKILL`。
> **命令在你的 deck 工作目录里执行**；deck 用路径指过去，不要写进 skill 目录。

---

## 0 · 环境

| | 位置 | 注意 |
|---|---|---|
| Python + fontTools + brotli + Pillow | `~/.cache/slide-venv/bin/python` | 缓存在家目录，重建才要联网 |
| Chrome | `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` | headless 出 PDF 和截图 |
| poppler | `pdffonts` / `pdfinfo` | 只在验收时用 |
| 缓存源字体 | `~/.cache/slide-fonts/` | `NotoSansSC-VF.ttf`（16.9 MB）· `Arimo-VF.ttf` |

### ⚠️ 最脆的一环

**`~/.cache/slide-venv` 一丢，`build_font.py` 就跑不了，整套流程断掉。**

```bash
python3 -m venv ~/.cache/slide-venv
~/.cache/slide-venv/bin/pip install fonttools brotli pillow
```

一条命令也能补（顺手把源字体也下好）：

```bash
"$SKILL/scripts/build.sh" --doctor --fix
```

系统 `python3` 也有 fontTools（`pip3 install --user --break-system-packages`），
但 venv 里有 **brotli**（写 WOFF2 要用）和 **Pillow**（做字体对比图要用）。

---

## 1 · 一条命令（推荐）

```bash
"$SKILL/scripts/build.sh" 我的deck.html
```

它依次做三件事，任何一步失败就停：

| | | |
|---|---|---|
| ① | **字体子集化 + 内联** | 改过任何文字都必须跑 —— 这是反复踩过的坑（F4） |
| ② | **导出 PDF** | Chrome headless |
| ③ | **验收** | `check.py`，含字形覆盖 |

**为什么要包成脚本**：漏跑 ① 的后果是**静默的** ——
新字不在子集里、掉回系统字体，**屏幕上完全看不出来**，PDF 里才变成 Type 3。
人工记不住，所以让它变成一条命令。

`~/.cache/slide-venv` 丢了会自动重建（`--doctor --fix`）。

---

## 1b · 手动五步（要看中间产物时用）

```bash
SKILL=<本 skill 目录>                    # SKILL.md 所在目录
SCRIPTS="$SKILL/scripts"
ASSETS="$SKILL/assets"
CH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PY="$HOME/.cache/slide-venv/bin/python"
```

### ① 起手：newdeck.py

```bash
"$SCRIPTS/newdeck.py" my-deck.html "这份 deck 的全名"
```

**不要从空白文件写。** 令牌、六锚点骨架、打印规则、UI 隐藏规则全在模板里。
从空白写必然漏掉其中几条，而且漏掉的都是**看不出来**的那几条（见 `references/FAILURES.md`）。
用 `newdeck.py` 而不是 `cp`：它顺手把 `<title>` 设好（F8b），
并自检模板没被改坏 —— 面板残留、结构回退都会被拦下。

### ② 写页：用 `setpages.py`，别直接改大文件

```bash
"$SCRIPTS/setpages.py" deck.html --extract    # 页面抽到 deck.pages.html（几 KB）
$EDITOR deck.pages.html                       # 改页面 —— 在几 KB 的文件里改
"$SCRIPTS/setpages.py" deck.html              # 贴回去（检查全过才落盘）
"$SCRIPTS/setpages.py" deck.html --list       # 只列页面清单，不写
```

**为什么不能直接编辑 deck：** 一份 deck 约 440 KB，其中约 400 KB 是内联字体的
base64。要在里面给每一页找唯一锚点很难受 —— 而**锚点找错是静默吞内容的**。
（冷启动测试里，一个没有任何提示的 agent 自己发明了一套「stage 文件 + 切片脚本」，
四个回合里把同一段 Python 重写了四遍。`setpages.py` 就是把那段固化成工具。）

它保证一件事：

> **边界以外逐字节不动。** 抽出来再贴回去，得到的文件与原文件 `md5` 相同。

**这是硬保证，不是尽力而为** —— 写盘前自检五项（外壳前后逐字节比对、页面数、幂等、
页面区不含边界标记、`<section>` 配平），**任何一条不过就不写文件**。

反向用例：`"$SCRIPTS/setpages.py" --self-test`（10 个）

**`<title>` 也归它管。** 标题住在外壳里（`#stage` 之外），但它是「这份 deck 的内容」：

```bash
"$SCRIPTS/setpages.py" deck.html --title "这份 deck 的全名"   # 只换那一个标签
"$SCRIPTS/newdeck.py" out.html "这份 deck 的全名"             # 或者起手就设好
```

（`check.py` 会在 `<title>` 还是模板默认值的时候**判失败** —— 页面上完全看不见，
但浏览器标签、窗口标题、PDF 元数据都用它。见 `FAILURES F8b`。）

---

### ②b 写页时的纪律：只动页面，不动令牌

**可以动**：用哪个原型、几页、文字怎么写
**不许动**：`--u`、颜色、字体栈、间距、字号阶梯

### ③ 字体：改完文字**必须**重跑

```bash
$PY "$SCRIPTS/build_font.py" my-deck.html              # 默认 --latin noto（纯思源黑体）
$PY "$SCRIPTS/build_font.py" my-deck.html --latin arimo # 想换窄拉丁时才用
```

**幂等**，可以无脑重跑。**不跑就会静默掉字**（见 `references/FAILURES.md` F4）。

**`--latin calibri` 会把 Microsoft 授权字体嵌进 deck** —— 仅限有 Office 授权的
内部用途；对外分发用 `arimo`（OFL）或默认 `noto`。

### ④ 验收

```bash
"$CH" --headless=new --disable-gpu --no-pdf-header-footer \
      --virtual-time-budget=6000 --print-to-pdf=/tmp/my-deck.pdf \
      "file://$PWD/my-deck.html"

$PY "$SCRIPTS/check.py" my-deck.html --pdf /tmp/my-deck.pdf
```

**`check.py` 通过 = 可以交付。** 不通过别往下走。

### ⑤ 逐页截图（看效果 / 做对比图）

```bash
for n in $(seq 1 9); do
  "$CH" --headless=new --disable-gpu --hide-scrollbars \
    --force-device-scale-factor=1 --window-size=1280,720 --virtual-time-budget=6000 \
    --screenshot=/tmp/s$n.png "file://$PWD/my-deck.html#$n"
done
```

`#N` 是第 N 页。**窗口必须是 `1280,720`**，不是 `1280,807`（见 F6）。

---

## 2 · 四个坑

前两个**会静默产出错的东西**，后两个会立刻报错。

### 坑 1 · 改完文字不重跑 `build_font.py`

内联字体是**按实际出现的字符子集化的**。改文案引入新字 → 这个字不在子集里 →
**静默掉回系统字体**（PingFang）→ PDF 里冒出一堆 Type 3，体积翻几倍。

**屏幕上完全看不出来**，因为换回来的也是黑体，字形几乎一样。

```bash
$PY "$SCRIPTS/build_font.py" my-deck.html      # 改完字就跑，无脑跑
$PY "$SCRIPTS/check.py" my-deck.html           # 会告诉你有没有缺字
```

### 坑 2 · 动令牌

令牌是一个**互相咬合的推算系统**：`--u` 决定字号、字号决定容量、容量决定一页能放多少行。

把 `--u` 从 28 调到 26，九个字号全变，**但页面内容不变 → 当场溢出**。
`check.py` 会抓，但如果你只看某几页截图，看不出来。

**容量表在 `references/RULES.md §6`。要用之前先查。**

### 坑 3 · 截图窗口给了 `1280,807`

舞台在视口里**垂直居中**。窗口高 807 时舞台落在 `y = 43.5 .. 763.5`，
裁 `(0,0,1280,720)` 就把页脚切掉了。

**一律用 `--window-size=1280,720`**，舞台正好铺满。

### 坑 4 · 忘了 `--no-pdf-header-footer`

Chrome 会加上 URL 和日期。**这个肉眼可见**，不算隐蔽，但很容易忘。

---

## 3 · 什么算通过

```
静态检查
  ✓ 零外部引用（可离线）
  ✓ 未使用 box-shadow
  ✓ 未使用 linear-gradient
字体检查：内联 N 个子集，覆盖 M 个码位
  ✓ K 个在用的字符全部命中
逐页容量（余量门槛 54px）
  ✓ 每页余量 ≥ 54px
PDF 检查
  ✓ /SMask       0
  ✓ /Luminosity  0
  ✓ 页面尺寸      960 x 540 pts
  ✓ 字体          无 Type 3
```

**`check.py` 只覆盖了一部分规则。** 它查不出来的（必须靠人/agent 记住）：

- **线宽**（≥2px 分隔线）
- **颜色对比度**（正文 ≥ 4.5:1）
- **列宽**（`.row .k` 最多 4 个中文字）
- **内容质量**（有没有论点、有没有落点句）

> 这几条还**没进 `check.py`**。规则靠"记住"是迟早会失效的 —— 这是这套 skill 已知的缺口。

---

## 4 · 自检

```bash
"$SKILL/scripts/check.py" --self-test     # 跑 assets/tests/ 下 4 个反向用例，退出码 0/1
```

`assets/tests/bad_A/B/C.html` 各自故意违规一种，`good.html` 应该通过。
**改了 `check.py` 之后必须跑这个** —— 确认它还能抓到问题。

---

## 5 · 相关文件

| 文件 | 作用 |
|---|---|
| `assets/template.html` | 零件库，**12 页**展示所有原型 |
| `assets/demo.html` | 用模板做的 demo，9 页 |
| `scripts/check.py` | 机械验收（静态 + 字形覆盖 + 几何 + PDF） |
| `scripts/build_font.py` | 字体子集化 + 内联（幂等） |
| `assets/tests/` | 反向用例 |
| `scripts/newdeck.py` | 从模板起手（设 title + 自检模板干净） |
| `scripts/setpages.py` | 页面抽出／贴回（边界以外一字节不动） |
| `scripts/parts.py` | 零件清单（定义 × 示范 × 文档），从 CSS 生成 |

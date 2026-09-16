# PACKAGING.md · 打包成 pi skill 的计划

> **状态：✅ 已执行完毕（五组全做完，验收全过）。** 保留了这份文件作为记录。
> 这份文件只讲「怎么装」，不讲「规范是什么」（那是 `RULES.md`）。

---

## 0 · 结论：什么能直接装

| | 体积 | 能不能直接装 |
|---|---|---|
| `RULES.md` | 50 KB | ✓ |
| `BUILD.md` | 8 KB | ✓ |
| `FAILURES.md` | 20 KB | ✓ |
| 5 个 Python 脚本 | 76 KB | ✓ 但要改路径 |
| `build.sh` | 3 KB | △ **Chrome 路径写死 macOS** |
| `tests/` | 28 KB | ✓ |
| `template.html` | **441 KB → 36 KB** | ✓ **必须剥掉内联字体** |
| `demo.html` | **393 KB → 29 KB** | ✓ 同上 |
| `SKILL-NOTES.md` / `PLAN.md` / `PACKAGING.md` | 33 KB | ✗ **开发档案，不进 skill** |
| 16.9 MB 的源字体 | — | ✗ 不进 skill（见 §3） |

**剥掉字体后整个 skill 约 400 KB。**

---

## 1 · 四个决定（已按建议定下）

### ✅ 决定 ① 入口：**强**（`disable-model-invocation: true`）

agent **看不见**这个 skill，只能你打 `/skill:jerryslide` 唤起。

**理由**：这套规范的前提很窄（**屏幕共享 · 对方窗口 ≥800px · 中英混排 · 红色**）。前提之外它**不是中性的，它会主动害人** —— 拿去做投影 deck，`u` 的推导整个是错的。

而且 pi 文档自己写着 *"models don't always do this; use prompting or `/skill:name` to force it"* —— 模型不一定会加载 skill。

**代价**：你永远得记得这个命令存在。

### ✅ 决定 ②「半页空着」：**保持警告**

45% 这条线是量出来的（模板最高 42%，唯一视觉明显空的那页 51%），但**它终究是判断题** —— 机械检查说不出「这页留白是设计还是忘了写」。

### ✅ 决定 ③ 环境：**紧凑（不打包字体）+ `--doctor` 预检**

见 §3。核心是**把环境问题从"跑到第三步才崩"变成"开工前就知道"**。

### ✅ 决定 ④ skill 名字：**`jerryslide`**

（最初定为 `slides`，后来改名。）最短，前提写进 `description`。

---

## 2 · 目标结构

pi 的约定：目录 + `SKILL.md`，其余自由。惯例用 `scripts/` `references/` `assets/`。

```
jerryslide/
├── SKILL.md                    ← 新建 · 常驻 context · 目标 ≤ 200 行
├── references/                 ← 按需加载
│   ├── RULES.md                ← 50 KB，唯一真相源
│   ├── BUILD.md                ← 8 KB
│   └── FAILURES.md             ← 20 KB ★ skill 里最值钱的一份
├── scripts/
│   ├── build.sh                ← 改：Chrome 探测 · 加 --doctor
│   ├── check.py                ← 改：找 assets/ · 用 venv
│   ├── build_font.py           ← 改：缓存目录
│   ├── newdeck.py              ← 改：找 assets/
│   ├── setpages.py             ← 无需改（除了路径）
│   └── parts.py                ← 改：找 assets/template.html
└── assets/
    ├── template.html           ← 无字体版 36 KB
    ├── demo.html               ← 无字体版 29 KB
    └── tests/                  ← 28 KB
```

**分工原则：**

| | 放哪 | 为什么 |
|---|---|---|
| **知识**（1px 线为什么消失） | `references/RULES.md` | 长，按需读 |
| **铁律**（不许用 1px 线） | `SKILL.md` | 必须常驻，否则会忘 |
| **流程**（跑哪条命令） | `SKILL.md` | 同上 |
| **失败目录** | `SKILL.md` 放速查表 + 指 `references/FAILURES.md` | 两个都要 |

---

## 3 · 字体与环境：先把事实讲清楚

### 三样东西，别混淆

| | 大小 | 什么时候用 |
|---|---|---|
| **思源黑体完整版** | **16.9 MB** | **切片的原料**。在 `~/.cache/slide-fonts/` 里放**一次** |
| **每份 deck 内联的子集** | **284–530 KB** | 每做一份 deck 生成一份 |
| 你那份 deck 的 HTML | ~450 KB | 主要就是这个子集 |

**中文有几千个字，完整字体就必须 16.9 MB。但一份 deck 只用 ~300 个字** —— 所以每次从完整版切一小片（~300 KB）嵌进 HTML。**这 300 KB 是每份 deck 自己的**，所以文件才 450 KB 不是 17 MB。

### 下载只发生在缓存不在的时候

```python
def get_noto_vf():
    if NOTO_VF.exists() and NOTO_VF.stat().st_size > 1_000_000:
        return NOTO_VF        # ← 有缓存，直接用，不联网
    print("  ↓ 下载思源黑体…（首次运行才会有）")
    subprocess.run(["curl", ...])   # ← 只有这里下载
```

| 触发下载的情况 | |
|---|---|
| 这台机器第一次用 | 一次（~30 秒） |
| 换电脑 / 重装系统 | 再一次 |
| **把 skill 发给同事** | **他那台机器要一次** |
| 清理工具删了 `~/.cache/` | 再一次 |
| **当时没网 / GitHub 连不上** | **✗ 死** |

**日常做 deck 永远不会触发下载。**

### 那风险是什么：**它崩在错误的时间点**

```
① newdeck.py     起手                     ← 不需要字体
② setpages.py    写页面                   ← 不需要字体
   ┈┈┈┈ 你的主要工作量在这里，可能几十分钟 ┈┈┈┈
③ build.sh       字体 → PDF → 验收         ← 崩在这里 ✗
```

**页面全写完了，才发现环境没准备好。**

### 修法：`--doctor` —— 开工前先体检

**不打包字体（省 16.9 MB），但把检查前置。** 见 §6 规格。

---

## 4 · 要做的工作

按依赖排序。**每组都能单独验收。**

### 第 1 组 · 让脚本可移植（不改功能）

- [x] **1.1** `build.sh` 的 Chrome 路径 → 抽出和 `check.py` 一样的候选列表探测
  （`/Applications/Google Chrome.app/…` · `/Applications/Chromium.app/…` · `which google-chrome` · `which chromium`）
- [x] **1.2** 所有脚本找 `template.html` / `demo.html` → 改成 `assets/`
- [x] **1.3** **统一 Python**：现在 `build.sh` 用 venv，其他脚本用系统 `python3`。
  改成全部走 venv（`check.py` 的字体检查依赖 `fontTools`，系统 Python 未必有）
- [x] **1.4** **venv 从 `/tmp/slidenv` 挪到 `~/.cache/slide-venv`**
  —— `/tmp` 重启就没，而重建要 pip 联网。和 `slide-fonts` 放一起
- [x] **1.5** **验收**：把 skill 目录拷到 `/tmp/portable/`，剥掉字体，跑通 `./scripts/build.sh`

### 第 2 组 · 生成无字体版

- [x] **2.1** 写 `make_assets.py` 一次性生成 `assets/`（剥 `@font-face` + 拷 tests）
  —— 不做 `build_font.py --strip`，那是给运行期用的功能，没必要
- [x] **2.2** 确认剥离后 `newdeck.py` / `setpages.py` / `parts.py` 仍工作
- [x] **2.3** **副作用（好的）**：模板里没有字体 → **不跑 `build_font.py` 就出不了 PDF**
  → 从机制上不可能忘记重跑字体

### 第 3 组 · `--doctor` 与新环境路径

- [x] **3.1** `build.sh --doctor`（规格见 §6）
- [x] **3.2** `build.sh --doctor --fix`（只修能自动修的：建 venv · 下字体）
- [x] **3.3** `build.sh` 正常路径启动时**先静默跑一遍 doctor** —— 缺东西就立刻停并给指引

### 第 4 组 · 写 SKILL.md

- [x] **4.1** ≤200 行的 `SKILL.md`（骨架见 §5）
- [x] **4.2** 从 `RULES.md` 压出**铁律**（每条一句 why，不搬表格）
- [x] **4.3** 从 `FAILURES.md` 压出**速查表**（保留「屏幕上看得出吗」那一列）
- [x] **4.4** 写 `description`（≤1024 字符，含「不适用于」）
- [x] **4.5** `SKILL.md` 第一句就是 `./build.sh --doctor`

### 第 5 组 · 装上去并验收

- [x] **5.1** 装到 `~/.pi/agent/skills/jerryslide/`
- [x] **5.2** **冷启动第三次**：全新会话 → `/skill:jerryslide` + 一个题目 → 产出过 `check.py`
- [x] **5.3** **反向测试**：给一个**不该用**的场景（「做个投影用的 deck」）→ 看它拒绝
- [x] **5.4** **环境测试**：**临时把字体缓存改名** → 跑 `--doctor` → 看它是否在开工前就说清
- [x] **5.5** 记录结果，改 → 再测

---

## 5 · SKILL.md 的骨架（草稿，供 review）

目标 ≤ 200 行。**每一节都要能回答「为什么它必须常驻」** —— 常驻 context 的每一行都是税。

```markdown
---
name: jerryslide
description: >
  做在线会议共享屏幕用的 HTML slide（单文件、离线、可导 PDF）。
  中英混排、学术简约风。含模板、构建脚本、机械验收。
  不适用于：要 .pptx/Google Slides 的、纯英文受众的、投影仪现场演讲的。
disable-model-invocation: true
---

# 屏幕共享用的 slide

## 0. 第一步永远是 ./build.sh --doctor
## 1. 什么时候用 —— 什么时候**不**用          ← 最重要的一节
## 2. 工作流：四条命令
## 3. 铁律 N 条（每条一句 why）
## 4. 速查：令牌 / 零件 / 容量
## 5. 出错了查这张表 → references/FAILURES.md
## 6. 冻结线：什么能动，什么不能动
```

**「冻结线」那一节**（`SKILL-NOTES §4` 已拍板但没成文）：

> **页面结构你可以加，令牌值不要动。**

不写这条，agent 会「顺手优化」`--u`。

---

## 6 · `--doctor` 的规格

**目标：让环境问题在「开工前」暴露，而不是跑到第三步。**

### 检查五项

| # | 检查 | 失败时说什么 |
|---|---|---|
| 1 | `curl` 在不在 | 下载字体需要它 |
| 2 | `~/.cache/slide-venv` 在不在 | 「重建要联网（pip 装 fontTools/brotli/pillow）」 |
| 3 | venv 里 `fontTools` `brotli` `PIL` 能不能 import | 同 2 |
| 4 | `~/.cache/slide-fonts/NotoSansSC-VF.ttf`（>1 MB） | **「两种办法：① `--fix` 联网下载 16.9 MB ② 手动放到这个路径」** |
| 5 | Chrome（候选列表探测） | 列出找过的路径 + 怎么装 |

### 输出

```
$ ./build.sh --doctor

  ✓ curl
  ✓ venv          ~/.cache/slide-venv（fontTools 4.65 · brotli · pillow）
  ✗ 字体          缺 ~/.cache/slide-fonts/NotoSansSC-VF.ttf
        → 联网：./build.sh --doctor --fix        （下载 16.9 MB，约 30 秒）
        → 离线：手动把这个文件放到上面那个路径
              （任何 Noto Sans SC 变量字体都行，需 >1 MB）
  ✓ Chrome        /Applications/Google Chrome.app/Contents/MacOS/Google Chrome

  ✗ 1 项没准备好。**现在先补，别开始做 deck** ——
    第 ③ 步（导出 PDF）才崩的话，页面已经写完了。
```

### 约定

- **默认只读** —— 不建目录、不下载、不改文件
- **`--fix` 只做能自动做的两件**：建 venv（pip 联网）· 下载字体
- **`build.sh` 正常路径启动时先静默跑一遍**，缺东西就停 —— 这样不靠人记得

---

## 7 · 打包完怎么知道它成了

**判据（按重要性）：**

1. **冷启动第三次**：全新会话 → `/skill:jerryslide` → 一个题目 → **产出的 deck 过 `check.py`，且你愿意分享**
2. **反向测试**：给一个不该用的场景 → **它拒绝或明确警告**
3. **环境测试**：临时改名字体缓存 → **开工前就说清**，不是跑一半崩
4. **`--doctor` 之外**：agent 不该自己想到用这套规范（决定 ① 选强的结果）

---

## 8 · 明确不做 / 推迟

| | 为什么 |
|---|---|
| 打包 16.9 MB 源字体进 skill | 你日常不触发下载；17 MB 太重（换来的只是"别人的机器首次不联网"） |
| 拆出「通用编解码知识」成第二个 skill | **值得想，但不是现在** —— 「1px 线在 H.264 下消失」不止这套 deck 适用 |
| 支持纯中文 / 纯英文 | 现在中英混排是写死的假设 |
| 支持 `.pptx` 导出 | 不做。单文件 HTML + PDF 就是交付形态 |
| 带内联字体的 `template.html` | 剥掉。**剥掉顺带保证每次都必跑 `build_font.py`** |
| `SKILL-NOTES.md` / `PLAN.md` / `PACKAGING.md` 进 skill | 开发档案，不是操作说明 |

---

## 9 · 已定的四件事（review 完成）

| | 决定 | 理由 |
|---|---|---|
| **装哪** | **全局** `~/.pi/agent/skills/jerryslide/` | 所有项目都能用 |
| **放不放真 deck 范例** | **不放** | 它会被当成「标准」，而下一份主题完全不同。`demo.html` 已经够 |
| **`parts.py` 进不进** | **进，但 `SKILL.md` 不提** | 零成本，将来改模板用得上；做 deck 用不到 |
| **`demo.html` 进不进** | **进**（29 KB） | 它是「用模板做的成品」，`template.html` 是「零件展示」，看效果时不一样 |

**四个决定全部落定，计划可以执行。**

---

## 执行顺序

```
第 1 组（可移植性）    ← 先做。能单独验收，不碰内容
第 2 组（无字体版）    ← 依赖第 1 组
        ┈┈┈┈ 到这里停下来 review 一次 ┈┈┈┈
        脚本已能独立跑，SKILL.md 还没写，改主意成本最低
第 3 组（--doctor）
第 4 组（SKILL.md）    ← 依赖前三组定下来的路径和命令
第 5 组（装上测）
```

#!/usr/bin/env bash
#
# build.sh —— 一条命令跑完：字体 → PDF → 验收 → 零件清单
#
#   用法：  ./scripts/build.sh deck.html [自定义.pdf]
#           ./scripts/build.sh --doctor          体检环境（只读，不碰任何文件）
#           ./scripts/build.sh --doctor --fix    顺手把能自动补的补上（建 venv / 下字体）
#
#   PDF 默认写在 deck 旁边（同名 .pdf）。
#
# 为什么需要这个脚本：**改完文字忘了重跑 build_font.py** 是反复踩过的坑
# （记在 references/FAILURES.md F4）。漏跑的后果是静默的 —— 新字不在字体子集里，
# 掉回系统字体，**屏幕上完全看不出来**，PDF 里才变成 Type 3。
# 人工记不住，所以让它变成一条命令。
#
# 为什么有 --doctor：环境缺东西时，**它原来会崩在第三步（导出 PDF）**——
# 而第一二步（起手 + 写页面）已经把几十分钟的工作量花掉了。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 环境位置 ────────────────────────────────────────────────────────
# venv 从 /tmp 挪到 ~/.cache：/tmp 重启就没，而重建要 pip 联网。
# 和字体缓存放一起，一次装好长期有效。
VENV_DIR="${SLIDE_VENV_DIR:-$HOME/.cache/slide-venv}"
VENV="$VENV_DIR/bin/python"
FONT_DIR="${SLIDE_FONT_DIR:-$HOME/.cache/slide-fonts}"
NOTO="$FONT_DIR/NotoSansSC-VF.ttf"

# ── Chrome：和 check.py 同一套候选（macOS / Linux 都认）────────────────
find_chrome() {
  local c
  for c in \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "/Applications/Chromium.app/Contents/MacOS/Chromium" \
    "$(command -v google-chrome 2>/dev/null || true)" \
    "$(command -v google-chrome-stable 2>/dev/null || true)" \
    "$(command -v chromium 2>/dev/null || true)" \
    "$(command -v chromium-browser 2>/dev/null || true)"
  do
    [ -n "$c" ] && [ -x "$c" ] && { echo "$c"; return 0; }
  done
  return 1
}

# ── 优先 chrome-headless-shell（Chromium 官方的独立 headless 二进制）──────
# 为什么不直接用 Chrome 主程序：`--headless=new` 仍然会走 [NSApplication
# sharedApplication]，并以 uiElement=0 注册到 WindowServer。Dock 于是给每次
# 调用插一个临时格子、半秒后再删掉（--dump-dom 一次、--print-to-pdf 一次）。
# Dock 整排是居中的，多一格少一格都会左右弹一下 —— 一份 deck 要跑 build +
# check + inkcenter 十几次，跳得人以为系统出问题了。
# chrome-headless-shell 不注册 WindowServer，静默。
#
# 出图是像素级一致的：同一份 deck 两条路径各导一次，9 页逐页 0 像素差异，
# 只有 PDF 的 Creator 元数据字段不同。
# Chrome < 152 没有这个二进制，回落到主程序 —— 功能不变，只是 Dock 还会跳。
find_headless_shell() {
  local c
  for c in \
    "${CHROME_HEADLESS_SHELL:-}" \
    "$HOME"/.cache/chrome-headless-shell/*/chrome-headless-shell \
    "$(command -v chrome-headless-shell 2>/dev/null || true)" \
    "/opt/homebrew/bin/chrome-headless-shell" \
    "/usr/local/bin/chrome-headless-shell"
  do
    [ -n "$c" ] && [ -x "$c" ] && { echo "$c"; return 0; }
  done
  return 1
}

# ── 体检 ────────────────────────────────────────────────────────────
# 返回 0 = 全绿。任何一项没过就返回 1，并把「现在该怎么办」说出来。
doctor() {
  local fix="${1:-check}" bad=0

  echo "  环境体检"
  echo

  # 1 · curl（下字体要用）
  if command -v curl >/dev/null 2>&1; then
    echo "  ✓ curl"
  else
    echo "  ✗ curl          没有它就没法自动下字体"
    echo "        → 装上 curl，或者手动把字体放到 $NOTO"
    bad=1
  fi

  # 2+3 · venv 和里面的包
  if [ -x "$VENV" ] && "$VENV" -c 'import fontTools, brotli, PIL' 2>/dev/null; then
    local ver
    ver="$("$VENV" -c 'import fontTools;print(fontTools.version)' 2>/dev/null || echo '?')"
    echo "  ✓ venv          ${VENV_DIR}（fontTools $ver · brotli · pillow）"
  else
    if [ "$fix" = "fix" ]; then
      echo "  → 建 venv：$VENV_DIR"
      python3 -m venv "$VENV_DIR"
      "$VENV" -m pip install -q --upgrade pip
      "$VENV" -m pip install -q fonttools brotli pillow
      echo "  ✓ venv          建好了（fontTools · brotli · pillow）"
    else
      echo "  ✗ venv          缺 ${VENV_DIR}（或里面的包不全）"
      echo "        → ./scripts/build.sh --doctor --fix    （要联网 pip 装三个包）"
      bad=1
    fi
  fi

  # 4 · 字体
  if [ -f "$NOTO" ] && [ "$(wc -c <"$NOTO" | tr -d ' ')" -gt 1000000 ]; then
    echo "  ✓ 字体          $(printf '%.1f' "$(echo "scale=1; $(wc -c <"$NOTO")/1048576" | bc)") MB · $NOTO"
  else
    if [ "$fix" = "fix" ]; then
      if [ ! -x "$VENV" ]; then
        echo "  ✗ 字体          跳过下载 —— venv 还没建好，下完也没法切片"
        bad=1
      else
        echo "  → 下载思源黑体（16.9 MB，一次性，之后永久缓存）"
        "$VENV" "$SCRIPT_DIR/build_font.py" --fetch-only 2>&1 | sed 's/^/      /'
        [ -f "$NOTO" ] || { echo "  ✗ 字体          下载失败（可能没网）"; bad=1; }
      fi
    else
      echo "  ✗ 字体          缺 $NOTO"
      echo "        → 联网：./scripts/build.sh --doctor --fix        （下载 16.9 MB，约 30 秒）"
      echo "        → 离线：手动把任意 Noto Sans SC 变量字体放到上面那个路径（需 >1 MB）"
      bad=1
    fi
  fi

  # 5 · Chrome
  local ch
  if ch="$(find_headless_shell)"; then
    echo "  ✓ headless shell  $ch"
    echo "        （Dock 不会跳；主程序 --headless=new 每次会在 Dock 插一下）"
  elif ch="$(find_chrome)"; then
    echo "  ✓ Chrome        $ch"
    echo "        ⚠ 没有 chrome-headless-shell —— 每次导出 Dock 会跳一下。"
    echo "          装法见 references/BUILD.md「chrome-headless-shell」一节。"
  else
    echo "  ✗ Chrome        找不到（导出 PDF 要用它）"
    echo "        找过：/Applications/Google Chrome.app/… · /Applications/Chromium.app/…"
    echo "              which google-chrome · google-chrome-stable · chromium · chromium-browser"
    echo "              chrome-headless-shell · ~/.cache/chrome-headless-shell/*/chrome-headless-shell"
    bad=1
  fi

  echo
  if [ "$bad" -eq 0 ]; then
    echo "  ✓ 全部就绪。"
  else
    echo "  ✗ 有项目没准备好。**现在先补，别开始做 deck** ——"
    echo "    第 ③ 步（导出 PDF）才崩的话，页面已经写完了。"
  fi
  echo
  return "$bad"
}

# ══ 参数 ════════════════════════════════════════════════════════════
if [ "${1:-}" = "--doctor" ] || [ "${1:-}" = "--fix" ]; then
  MODE=check
  [ "${2:-}" = "--fix" ] && MODE=fix
  [ "${1:-}" = "--fix" ] && MODE=fix
  doctor "$MODE" || exit 1
  exit 0
fi
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
fi

DECK_IN="${1:-}"
if [ -z "$DECK_IN" ]; then
  echo "用法: ./scripts/build.sh <deck.html>" >&2
  echo "      ./scripts/build.sh --doctor [--fix]     体检环境" >&2
  exit 2
fi
if [ ! -f "$DECK_IN" ]; then
  echo "✗ 找不到文件：$DECK_IN" >&2
  exit 2
fi
DECK_DIR="$(cd "$(dirname "$DECK_IN")" && pwd)"
DECK="$DECK_DIR/$(basename "$DECK_IN")"
NAME="$(basename "${DECK%.html}")"
# PDF 默认落在 deck 旁边 —— 它是交付物，不该埋在临时目录里
OUT="${2:-$DECK_DIR/$NAME.pdf}"

# ── 开工前静默体检：缺东西就立刻停。不靠人记得先跑 --doctor ──
if ! doctor check >/dev/null 2>&1; then
  echo "✗ 环境没准备好 —— 先跑这个看清楚："
  echo
  doctor check || true
  exit 1
fi

echo "① 字体子集化 + 内联 —— 改过任何文字都必须跑这一步"
"$VENV" "$SCRIPT_DIR/build_font.py" "$DECK" | sed 's/^/   /'

echo "② 导出 PDF → $OUT"
# 优先 headless shell（不注册 WindowServer，Dock 不跳）；回落主程序则必须
# 显式给 --headless=new —— shell 自带 headless，不接受这个 flag。详见顶部。
if CHROME_BIN="$(find_headless_shell)"; then HEADLESS_FLAG=""; else
  CHROME_BIN="$(find_chrome)"; HEADLESS_FLAG="--headless=new"
fi
"$CHROME_BIN" ${HEADLESS_FLAG:+"$HEADLESS_FLAG"} --disable-gpu --no-pdf-header-footer \
          --virtual-time-budget=6000 --print-to-pdf="$OUT" \
          "file://$DECK" >/dev/null 2>&1
[ -s "$OUT" ] || { echo "✗ PDF 没生成" >&2; exit 1; }

# 全程用 venv 的 python —— check.py 的字形覆盖检查要 fontTools，
# 系统 python3 未必有（原来这里用 python3，是个静默降级的坑）。
echo "③ 验收"
"$VENV" "$SCRIPT_DIR/check.py" "$DECK" --pdf "$OUT"
[ $? -eq 0 ] || exit 1

# ④ 零件漂移 —— 查的是 template.html，所以只有给模板本身 / demo 跑才有意义。
#    按文件名判断，不按目录 —— 安装后模板在 assets/ 里。
case "$NAME" in
  template|demo)
    echo
    echo "④ 零件清单（定义 × 示范 × 文档）"
    "$VENV" "$SCRIPT_DIR/parts.py" || exit 1
    ;;
esac

echo
echo "✓ 完成：$OUT"

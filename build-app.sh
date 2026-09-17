#!/bin/bash
# 生成双击启动器 memmy 工作台.app（含图标）。可重复执行。
set -eu
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$DIR/memmy 工作台.app"

# 图标：有 PIL 就重新生成，没有就用仓库里现成的 icns
if [ ! -f "$DIR/icon/memmy.icns" ]; then
  if python3 -c "import PIL" 2>/dev/null; then
    (cd "$DIR/icon" && python3 make-icon.py && iconutil -c icns memmy.iconset -o memmy.icns)
  else
    echo "缺少 icon/memmy.icns 且本机没有 Pillow，启动器将使用系统默认图标"
  fi
fi

TMP="$(mktemp -t memmy-launcher).applescript"
cat > "$TMP" <<'APPLESCRIPT'
-- memmy 工作台启动器。跑同目录的 shell 脚本，不弹终端窗口。
on run
	set myPath to POSIX path of (path to me)
	set parentDir to do shell script "dirname " & quoted form of (text 1 thru -2 of myPath)
	try
		do shell script quoted form of (parentDir & "/启动工作台.command")
	on error errMsg
		display dialog "启动失败：" & return & return & errMsg ¬
			buttons {"好"} default button 1 with icon stop
	end try
end run
APPLESCRIPT

rm -rf "$APP"
osacompile -o "$APP" "$TMP"
rm -f "$TMP"

if [ -f "$DIR/icon/memmy.icns" ]; then
  cp "$DIR/icon/memmy.icns" "$APP/Contents/Resources/applet.icns"
fi
PB=/usr/libexec/PlistBuddy
PL="$APP/Contents/Info.plist"
$PB -c "Set :CFBundleIconFile applet" "$PL" 2>/dev/null || $PB -c "Add :CFBundleIconFile string applet" "$PL"
$PB -c "Set :CFBundleName memmy 工作台"  "$PL" 2>/dev/null || $PB -c "Add :CFBundleName string memmy 工作台" "$PL"
# 一次性启动器，不该常驻 Dock
$PB -c "Add :LSUIElement bool true" "$PL" 2>/dev/null || true

codesign --force --deep -s - "$APP" >/dev/null 2>&1 || true
touch "$APP"
echo "已生成 $APP"

#!/bin/bash
# NetPulse 启动 / 打包 / 部署脚本
# 主目标: ~/Documents/skills/NetPulse.app
#
# 用法:
#   ./run.sh                # 显示帮助
#   ./run.sh run            # 启动菜单栏 app (源码模式)
#   ./run.sh test           # 跑诊断引擎测试
#   ./run.sh check          # 跑一次诊断输出 JSON
#   ./run.sh build          # 打包 + 部署到 skills/
#   ./run.sh smoke          # 跑 .app bundle 的 smoke test
#   ./run.sh inspect        # 启动 NS_MENU_INSPECT 验证菜单结构

set -e
cd "$(dirname "$0")"

# 找虚拟环境
if [ -d "$HOME/.venv" ]; then
    source "$HOME/.venv/bin/activate"
elif [ -d "$HOME/venv" ]; then
    source "$HOME/venv/bin/activate"
elif [ -d ".venv" ]; then
    source ".venv/bin/activate"
else
    echo "❌ 找不到虚拟环境"
    echo "   python3 -m venv .venv && source .venv/bin/activate && pip install rumps py2app"
    exit 1
fi

# 部署位置
DEPLOY_TARGET="$HOME/Documents/skills/NetPulse.app"
DESKTOP_TARGET="$HOME/Desktop/NetPulse.app"

cmd="${1:-help}"

case "$cmd" in
    help|-h|--help)
        cat <<'EOF'
NetPulse 启动 / 打包脚本

用法:
  ./run.sh run       启动菜单栏 app (源码模式, 不打包)
  ./run.sh test      跑诊断引擎测试
  ./run.sh check     跑一次诊断输出 JSON
  ./run.sh build     打包成 .app 并部署到 ~/Documents/skills/
  ./run.sh smoke     跑 .app bundle 的 smoke test
  ./run.sh inspect   启动 NS_MENU_INSPECT 验证菜单结构

部署位置:
  主目标: ~/Documents/skills/NetPulse.app
  副本:   ~/Desktop/NetPulse.app
EOF
        ;;

    run)
        echo "🚀 启动 NetPulse (源码模式)..."
        exec python3 src/app.py
        ;;

    test)
        echo "🧪 跑测试套件..."
        python3 test_app.py
        python3 src/headless_test.py
        ;;

    check)
        echo "🔍 跑一次诊断..."
        python3 src/diagnostics.py | python3 -m json.tool
        ;;

    build)
        echo "📦 打包 + 部署..."
        # 杀掉任何运行中的 NetPulse(锁住 .app 不能覆盖)
        pkill -9 -f "NetPulse.app/Contents/MacOS/NetPulse" 2>/dev/null || true
        sleep 1

        # 重新打包
        rm -rf dist build
        python3 setup.py py2app 2>&1 | tail -2

        # 部署到主目标 (skills/)
        if [ -d "$DEPLOY_TARGET" ]; then
            rm -rf "$DEPLOY_TARGET"
        fi
        cp -R dist/NetPulse.app "$DEPLOY_TARGET"
        echo "✓ 已部署到: $DEPLOY_TARGET"

        # 也保留 Desktop 副本(便于快速双击)
        if [ -d "$DESKTOP_TARGET" ]; then
            rm -rf "$DESKTOP_TARGET"
        fi
        cp -R dist/NetPulse.app "$DESKTOP_TARGET"
        echo "✓ Desktop 副本: $DESKTOP_TARGET"

        # 验证 MD5 一致
        d1=$(md5 -q "$DEPLOY_TARGET/Contents/Resources/app.py")
        d2=$(md5 -q "$DESKTOP_TARGET/Contents/Resources/app.py")
        if [ "$d1" = "$d2" ]; then
            echo "✓ MD5 一致: $d1"
        else
            echo "✗ MD5 不一致!"
            exit 1
        fi
        ;;

    smoke)
        echo "🧪 跑 .app bundle 的 smoke test..."
        NS_SMOKE_TEST=1 "$DEPLOY_TARGET/Contents/MacOS/NetPulse"
        ;;

    inspect)
        echo "🔍 启动 NS_MENU_INSPECT 验证菜单结构..."
        NS_MENU_INSPECT=1 "$DEPLOY_TARGET/Contents/MacOS/NetPulse"
        ;;

    *)
        echo "未知命令: $cmd"
        echo "运行 ./run.sh help 查看用法"
        exit 1
        ;;
esac

"""
Headless 验证:不进入 GUI 主循环,只创建 App、注册 NSStatusItem、检查 icon 是否成功
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

import rumps
import time
import threading

from app import NetPulseApp


def verify_status_item():
    """直接检查 NSStatusBar 系统是否接受了我们的 item"""
    app = NetPulseApp()
    print(f"✓ App 实例创建成功")
    print(f"✓ app.title = {app.title!r}")

    # 通过 NSStatusBar 检查
    from AppKit import NSStatusBar, NSStatusItem
    sb = NSStatusBar.systemStatusBar()
    print(f"✓ NSStatusBar.systemStatusBar() 拿到系统菜单栏")

    # App.run() 内部会自己创建 status item, 但我们现在没 run
    # 所以手动验证菜单项注册逻辑:看 menu items 是否都正常
    print(f"\n=== 菜单结构 ===")
    print(f"status_items: {len(app.status_items)} 个")
    for i, item in enumerate(app.status_items):
        print(f"  L{i+1}: {item.title}")

    print(f"\nstatic items (非状态/VPN):")
    static = 0
    for k in app.menu:
        title = str(k.title)
        if title.startswith("L1") or title.startswith("L2") or "..." in title:
            continue
        static += 1
        if static <= 5:
            print(f"  - {title}")
    print(f"  ... 总共 {static} 个静态项")

    print(f"\nvpn_items: {len(app.vpn_items)} 个 (启动时为空)")

    # 跑一次诊断看 VPN 项能否正常插入
    print("\n--- 跑诊断 + 刷新 ---")
    from diagnostics import run_all_checks
    report = run_all_checks()
    app.last_report = report
    app._refresh_ui(report)
    print(f"刷新后 vpn_items: {len(app.vpn_items)} 个")
    for item in app.vpn_items:
        print(f"  - {item.title}")
    print(f"刷新后整体标题: {app.title!r}")

    # NSStatusBar.systemStatusBar() 在没 NSApplication.run() 时也可以创建 item
    # 这是个标准 NSStatusItem 创建测试
    test_item = sb.statusItemWithLength_(-1.0)  # -1 = variable
    test_item.setTitle_("🧪")
    print(f"\n✓ 测试 status item 创建成功: button={test_item.button()}")
    sb.removeStatusItem_(test_item)
    print(f"✓ 测试 status item 已清理")

    print("\n✅ Headless 验证全部通过")
    print("\n注: 真正的菜单栏图标需要 NSApplication.run() 进入事件循环才能显示")
    print("在 Hermes GUI 环境中无法启动 GUI app, 用户需手动 `python3 src/app.py` 启动")


if __name__ == "__main__":
    verify_status_item()

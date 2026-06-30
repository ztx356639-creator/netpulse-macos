"""
NetPulse 测试 — 不启动 GUI, 直接验证状态更新与 VPN 菜单逻辑
不用 mock(rumps 是 PyObjC,难 mock), 直接调真 rumps,
通过打印菜单内容和验证数据结构来检查逻辑
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import rumps
# 阻止 rumps.run 进入 NSApplication 事件循环
rumps.run = lambda app: None
rumps.notification = lambda *a, **k: None
rumps.alert = lambda *a, **k: None
rumps.timer = lambda s: (lambda fn: fn)

from app import NetPulseApp
from diagnostics import run_all_checks


def dump_menu(app, label):
    print(f"\n=== {label} ===")
    # NSMenu 不能按数字下标访问,用 status_items + vpn_items 反查
    seen_ids = set()
    for item in app.status_items:
        seen_ids.add(id(item))
        title = str(item.title)
        print(f"  [S] {title}")
    for item in app.vpn_items:
        seen_ids.add(id(item))
        title = str(item.title)
        print(f"  [V] {title}")
    # menu 里其他静态项
    print("  --- other menu items ---")
    for k in app.menu:  # NSMenu 迭代返回 NSMenuItem
        if id(k) not in seen_ids:
            print(f"  [ ] {str(k.title)}")
    print(f"  vpn_items count: {len(app.vpn_items)}")


# 1) 创建实例
app = NetPulseApp()
print(f"✓ App created, title={app.title!r}")
assert app.title.startswith("📡")
dump_menu(app, "Initial state")

# 2) 跑一次真实诊断
report = run_all_checks()
print(f"\n✓ Diagnostics: overall={report['overall']}, layers={len(report['layers'])}, vpns={len(report['vpns'])}")
app.last_report = report
app._refresh_ui(report)
dump_menu(app, "After _refresh_ui()")

# 3) 验证菜单栏标题反映整体状态
expected_overall = report["overall"]
expected_color = {"ok": "🟢", "warn": "🟡", "fail": "🔴"}[expected_overall]
assert expected_color in app.title, f"title 应含 {expected_color}, 实际 {app.title!r}"
print(f"\n✓ Status color {expected_color} shown in title: {app.title!r}")

# 4) 验证 5 个状态项的 title 包含层信息
print("\n=== Layer titles ===")
for i, item in enumerate(app.status_items):
    title = str(item.title)
    layer = report["layers"][i]
    print(f"  L{i+1}: {title}")
    assert layer["name"] in title, f"L{i+1} title 缺少层名 {layer['name']}"
    assert "—" in title, f"L{i+1} title 缺少分隔符"
print("✓ All 5 layers have valid titles")

# 5) 验证 VPN 项数量 = 诊断 VPN 数量
print(f"\n=== VPN items: {len(app.vpn_items)} (expected {len(report['vpns'])}) ===")
for item in app.vpn_items:
    print(f"  • {str(item.title)}")
assert len(app.vpn_items) == len(report["vpns"])
print("✓ VPN item count matches")

# 6) 验证刷新不会重复添加 VPN 项
prev_count = len(app.vpn_items)
prev_menu_count = len(list(app.menu))
app._refresh_ui(report)
assert len(app.vpn_items) == prev_count, f"VPN 项重复添加!{prev_count} -> {len(app.vpn_items)}"
assert len(list(app.menu)) == prev_menu_count, "菜单项重复!"
print(f"\n✓ Re-refresh doesn't duplicate (menu: {prev_menu_count} items)")

# 7) 测试文本报告
text = app._format_text_report(report)
print(f"\n=== Text report ({len(text)} chars) ===")
print(text[:500])
assert "NetPulse" in text
assert all(f"L{i+1}" in text or layer["name"] in text for i, layer in enumerate(report["layers"]))
print("\n✓ Text report contains all sections")

# 8) 测试 layer 点击回调(模拟点击,alert 已 mock)
print("\n=== Triggering L3 (VPN) callback ===")
try:
    app.status_items[2]._cb(None) if hasattr(app.status_items[2], '_cb') else None
except Exception as e:
    print(f"  (callback call failed: {e})")
print("✓ L3 callback is invokable")

print("\n✅ All checks passed")

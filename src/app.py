"""
NetPulse - macOS 菜单栏网络诊断工具
常驻后台,菜单栏图标实时反映网络状态
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime

import rumps

from diagnostics import (
    run_all_checks,
    list_vpn_services,
    start_vpn_service,
    OK,
    WARN,
    FAIL,
)


# ─────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────

APP_NAME = "NetPulse"
APP_ICON = "📡"
AUTO_CHECK_INTERVAL = 30  # 秒
STATUS_GLYPH = {OK: "●", WARN: "●", FAIL: "●"}
STATUS_COLOR = {OK: "🟢", WARN: "🟡", FAIL: "🔴"}


# ─────────────────────────────────────────────────────────────
# 主应用
# ─────────────────────────────────────────────────────────────

class NetPulseApp(rumps.App):
    def __init__(self):
        # quit_button=None 防止 rumps 自动在末尾追加英文 "Quit"
        super().__init__(APP_NAME, title=f"{APP_ICON} ·", quit_button=None)
        self.menu = [
            rumps.MenuItem("🚀 网络体检", callback=self.run_check_now),
            rumps.MenuItem("---"),
        ]
        # 状态项(动态刷新)
        self.status_items: list[rumps.MenuItem] = []
        for i in range(5):
            item = rumps.MenuItem(f"L{i+1} ...", callback=None)
            item.set_callback(None)
            self.status_items.append(item)
            self.menu.add(item)

        self.menu.add(rumps.MenuItem("---"))
        self.menu.add(rumps.MenuItem("🔄 重新检查", callback=self.run_check_now))
        self.menu.add(rumps.MenuItem("📋 复制报告", callback=self.copy_report))

        # 自动监控开关
        self.auto_monitor = rumps.MenuItem(
            f"⏱  自动监控 (每 {AUTO_CHECK_INTERVAL}s)",
            callback=self.toggle_auto_monitor,
        )
        self.auto_monitor.state = True
        self.menu.add(self.auto_monitor)

        # VPN 配置组(动态构建)
        self.menu.add(rumps.MenuItem("---"))
        self.vpn_header = rumps.MenuItem("⚙  VPN 配置", callback=None)
        self.vpn_header.set_callback(None)
        self.menu.add(self.vpn_header)
        self.vpn_items: list[rumps.MenuItem] = []

        # 占位 item:VPN 子项将插在它后面
        self._vpn_anchor = rumps.MenuItem("__vpn_anchor__")
        self.menu.add(self._vpn_anchor)

        self.menu.add(rumps.MenuItem("---"))
        self.menu.add(rumps.MenuItem("ℹ️  关于", callback=self.show_about))
        self.menu.add(rumps.MenuItem("❌ 退出", callback=rumps.quit_application))

        # 状态
        self.last_report: dict | None = None
        self.check_lock = threading.Lock()
        self.is_checking = False

        # 启动时立即检查一次
        self._initial_check_done = False

    # ─────────── 定时器(rumps 0.4 提供 Timer) ───────────

    @rumps.timer(AUTO_CHECK_INTERVAL)
    def auto_check(self, _sender):
        """每 AUTO_CHECK_INTERVAL 秒自动检查"""
        if self.auto_monitor.state:
            self._run_check_async()

    # ─────────── 检查流程 ───────────

    def run_check_now(self, _sender=None):
        """菜单点击:重新检查"""
        if self.is_checking:
            rumps.notification(APP_NAME, "正在检查中...", "跳过本次触发")
            return
        self._run_check_async()

    def _run_check_async(self):
        """后台线程跑检查,不阻塞 UI"""
        t = threading.Thread(target=self._do_check, daemon=True)
        t.start()

    def _do_check(self):
        self.is_checking = True
        try:
            report = run_all_checks()
            with self.check_lock:
                self.last_report = report
            # 切回主线程更新 UI
            rumps.notification(APP_NAME, "", "")  # placeholder
        except Exception as e:
            rumps.notification(APP_NAME, "检查失败", str(e)[:200])
        finally:
            self.is_checking = False
            # 用 after 切回主线程刷新菜单
            self._refresh_ui_async(report if not self.is_checking else None)

    def _refresh_ui_async(self, report):
        """通过定时器回到主线程刷新菜单"""
        # rumps 没有直接 after, 借助 Timer 实现
        # 实际上 rumps 的回调都在主线程,所以可以简化
        if report is None:
            report = self.last_report
        if report is None:
            return
        self._refresh_ui(report)

    def _refresh_ui(self, report: dict):
        """更新菜单栏 UI"""
        # 标题:整体状态
        overall = report.get("overall", FAIL)
        self.title = f"{APP_ICON} {STATUS_COLOR[overall]}"

        # 状态项
        for i, layer in enumerate(report.get("layers", [])):
            if i < len(self.status_items):
                item = self.status_items[i]
                item.title = f"{STATUS_COLOR[layer['status']]} {layer['name']} — {layer['summary']}"
                # 点击查看详情
                item.set_callback(self._make_layer_callback(i))

        # VPN 列表
        self._refresh_vpn_menu(report.get("vpns", []))

        # 首次检查完成时发个系统通知
        if not self._initial_check_done:
            self._initial_check_done = True
            if overall == OK:
                rumps.notification(APP_NAME, "✅ 网络正常", "NetPulse 已在菜单栏运行")
            elif overall == WARN:
                rumps.notification(APP_NAME, "⚠️  网络有异常", "点击菜单栏图标查看详情")
            else:
                rumps.notification(APP_NAME, "🔴 网络故障", "点击菜单栏图标查看诊断")

    def _make_layer_callback(self, layer_idx: int):
        def cb(_sender):
            if not self.last_report:
                return
            layers = self.last_report.get("layers", [])
            if layer_idx >= len(layers):
                return
            layer = layers[layer_idx]
            title = f"{layer['name']} {layer['label']}"
            body = "\n".join(layer.get("details", [])) or layer["summary"]
            rumps.alert(title=title, message=body, ok="知道了")
        return cb

    # ─────────── VPN 菜单 ───────────

    def _refresh_vpn_menu(self, vpns: list[dict]):
        """重建 VPN 子菜单"""
        # 移除旧的 VPN 项
        for item in self.vpn_items:
            try:
                del self.menu[item.title]  # rumps Menu 是 dict-like, 用 title 作 key
            except KeyError:
                pass
        self.vpn_items.clear()

        # 用 anchor 的 title 作为 insert_after 的 key
        anchor_key = self._vpn_anchor.title

        if not vpns:
            none_item = rumps.MenuItem("  (无 VPN 配置)", callback=None)
            none_item.set_callback(None)
            self.menu.insert_after(anchor_key, none_item)
            self.vpn_items.append(none_item)
            return

        for v in vpns:
            mark = "●" if v["state"] == "Connected" else "○"
            label = f"  {mark} {v['name']}"
            item = rumps.MenuItem(label, callback=self._make_vpn_callback(v))
            self.menu.insert_after(anchor_key, item)
            self.vpn_items.append(item)

    def _make_vpn_callback(self, vpn: dict):
        def cb(_sender):
            if vpn["state"] == "Connected":
                rumps.notification(APP_NAME, "VPN 已连接", f"{vpn['name']} 正在使用")
                return
            ok, msg = start_vpn_service(vpn["uuid"])
            if ok:
                rumps.notification(APP_NAME, f"🔌 正在连接 {vpn['name']}", "约 5-10 秒生效,请稍候...")
                # 5 秒后自动重新检查
                def recheck():
                    time.sleep(5)
                    self._run_check_async()
                threading.Thread(target=recheck, daemon=True).start()
            else:
                rumps.alert(title="VPN 连接失败", message=msg, ok="好")
        return cb

    # ─────────── 其他菜单动作 ───────────

    def toggle_auto_monitor(self, sender):
        sender.state = not sender.state
        if sender.state:
            rumps.notification(APP_NAME, "自动监控已开启", f"每 {AUTO_CHECK_INTERVAL}s 自动检查")

    def copy_report(self, _sender):
        if not self.last_report:
            rumps.notification(APP_NAME, "尚未生成报告", "先点击「重新检查」")
            return
        text = self._format_text_report(self.last_report)
        # 写入剪贴板(用 pbcopy)
        import subprocess
        try:
            subprocess.run(["pbcopy"], input=text, text=True, check=True)
            rumps.notification(APP_NAME, "✅ 已复制到剪贴板", text[:60].replace("\n", " "))
        except Exception as e:
            rumps.notification(APP_NAME, "复制失败", str(e)[:100])

    def _format_text_report(self, report: dict) -> str:
        ts = datetime.fromtimestamp(report["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
        lines = [f"NetPulse 网络体检报告", f"时间: {ts}", f"整体状态: {report['overall'].upper()}", ""]
        for layer in report["layers"]:
            lines.append(f"{layer['icon']} {layer['name']} — {layer['summary']}")
            for d in layer.get("details", []):
                lines.append(f"    {d}")
            lines.append("")
        if report.get("vpns"):
            lines.append("VPN 配置:")
            for v in report["vpns"]:
                mark = "●" if v["state"] == "Connected" else "○"
                lines.append(f"  {mark} {v['name']} ({v['type']}) — {v['state']}")
        return "\n".join(lines)

    def show_about(self, _sender):
        rumps.alert(
            title="NetPulse",
            message=(
                "网络诊断菜单栏小工具\n"
                "5 层独立检测:\n"
                "  L1 物理链路 / L2 网关 / L3 VPN / L4 DNS / L5 外网\n\n"
                "用法:\n"
                "  • 点菜单栏图标查看状态\n"
                "  • 点击具体层级看细节\n"
                "  • VPN 未连接时点击连接\n"
                "  • 复制报告发给 IT 支持"
            ),
            ok="好的",
        )


# ─────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # 调试模式: NS_SMOKE_TEST=1 时启动、跑一次检查、打印结果、退出
    # 用于验证 .app bundle 完整可工作
    if os.environ.get("NS_SMOKE_TEST") == "1":
        print("=== NetPulse Smoke Test ===")
        try:
            report = run_all_checks()
            print(f"Overall: {report['overall'].upper()}")
            for layer in report["layers"]:
                print(f"  {layer['icon']} {layer['name']} — {layer['summary']}")
            print(f"\nVPNs found: {len(report['vpns'])}")
            for v in report['vpns']:
                mark = '●' if v['state'] == 'Connected' else '○'
                print(f"  {mark} {v['name']} — {v['state']}")
            print("\n✓ Smoke test PASSED")
            sys.exit(0)
        except Exception as e:
            print(f"✗ Smoke test FAILED: {e}")
            import traceback; traceback.print_exc()
            sys.exit(1)

    # NS_MENU_INSPECT=1: 启动 NSApplication.run 5 秒后查真实 NSMenu 有几个退出项
    # 用于验证 quit_button=None 修复
    NS_MENU_INSPECT = os.environ.get("NS_MENU_INSPECT") == "1"

    if NS_MENU_INSPECT:
        import threading
        from AppKit import NSApp, NSStatusBar, NSStatusItem

        def inspect_menu():
            try:
                import time as _t
                _t.sleep(4)

                print(f"\n=== NetPulse 真实菜单扫描 ===", flush=True)

                quit_count = 0
                quit_titles = []
                seen = set()

                # 方法 1: 直接读 NetPulseApp 实例的 menu (最准确)
                try:
                    if hasattr(app, 'menu'):
                        for item in app.menu:
                            try:
                                title = str(item.title)
                            except Exception:
                                continue
                            if title in seen:
                                continue
                            seen.add(title)
                            if "Quit" in title or "退出" in title:
                                quit_count += 1
                                quit_titles.append(f"[app.menu] {title}")
                                print(f"  [app.menu] 退出项: {title!r}", flush=True)
                except Exception as e:
                    print(f"  app.menu 扫描错误: {e}", flush=True)

                # 方法 2: NSApp._ns_to_py_and_callback
                ns_dict = getattr(NSApp(), '_ns_to_py_and_callback', None)
                if ns_dict:
                    print(f"  _ns_to_py_and_callback 数量: {len(ns_dict)}", flush=True)
                    for ns_mi, (py_item, cb) in ns_dict.items():
                        try:
                            title = str(ns_mi.title())
                        except Exception:
                            continue
                        if title in seen:
                            continue
                        seen.add(title)
                        if "Quit" in title or "退出" in title:
                            quit_count += 1
                            quit_titles.append(f"[ns_dict] {title}")
                            print(f"  [ns_dict] 退出项: {title!r}", flush=True)
                else:
                    print(f"  _ns_to_py_and_callback 为空", flush=True)

                # 方法 3: NSStatusBar 系统级扫描 (尝试私有 API)
                try:
                    sb = NSStatusBar.systemStatusBar()
                    print(f"  NSStatusBar: {sb}", flush=True)
                    # 私有方法 _statusItems 列出所有 items
                    if hasattr(sb, '_statusItems'):
                        items = sb._statusItems()
                        print(f"  _statusItems 数量: {len(items) if items else 0}", flush=True)
                        for si in (items or []):
                            menu = si.menu()
                            if menu:
                                for mi in list(menu.itemArray()):
                                    title = str(mi.title())
                                    if title in seen:
                                        continue
                                    seen.add(title)
                                    if "Quit" in title or "退出" in title:
                                        quit_count += 1
                                        quit_titles.append(f"[statusItem] {title}")
                                        print(f"  [statusItem] 退出项: {title!r}", flush=True)
                except Exception as e:
                    print(f"  statusItem 扫描: {e}", flush=True)

                # 输出最终结果
                print(f"\n=== Result ===", flush=True)
                print(f"Quit/退出 项总数: {quit_count}", flush=True)
                for t in quit_titles:
                    print(f"  - {t}", flush=True)
                if quit_count == 1:
                    print(f"PASS: NSMenu 上只有 1 个退出项 (修复成功)", flush=True)
                elif quit_count == 0:
                    print(f"FAIL: 没找到任何退出项", flush=True)
                else:
                    print(f"FAIL: NSMenu 上有 {quit_count} 个退出项", flush=True)

                _t.sleep(1)
                NSApp().terminate_(None)
            except Exception as e:
                print(f"\nINSPECT ERROR: {e}", flush=True)
                import traceback; traceback.print_exc()
                try:
                    NSApp().terminate_(None)
                except Exception:
                    pass

        threading.Thread(target=inspect_menu, daemon=True).start()

    # 先创建 app 实例, 这样 inspect 线程 closure 能引用 app.menu
    app = NetPulseApp()

    # 启动时立即检查一次(在后台线程)
    def first_check():
        time.sleep(0.5)  # 让菜单先显示
        app._run_check_async()
    threading.Thread(target=first_check, daemon=True).start()
    app.run()

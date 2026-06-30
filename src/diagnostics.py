"""
NetPulse - 网络诊断引擎
5层独立检测:L1 物理链路 / L2 网关 / L3 VPN隧道 / L4 DNS / L5 外网
每一层返回统一格式的检测结果 dict
"""

from __future__ import annotations

import re
import socket
import subprocess
import time
from dataclasses import dataclass, field, asdict
from typing import Optional


# ─────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────

OK = "ok"          # 绿 ✓
WARN = "warn"      # 黄 ⚠
FAIL = "fail"      # 红 ✗

_STATUS_ICON = {OK: "✓", WARN: "⚠", FAIL: "✗"}
_STATUS_LABEL = {OK: "正常", WARN: "异常", FAIL: "失败"}


@dataclass
class LayerResult:
    """单层检测结果"""
    name: str               # 显示名,例如 "L1 物理链路"
    status: str             # OK / WARN / FAIL
    summary: str            # 一句话总结(给菜单栏看)
    details: list[str] = field(default_factory=list)   # 多行细节(给报告看)
    latency_ms: Optional[float] = None  # 该层延迟(如适用)

    def icon(self) -> str:
        return _STATUS_ICON.get(self.status, "?")

    def label(self) -> str:
        return _STATUS_LABEL.get(self.status, "未知")

    def to_line(self) -> str:
        """菜单栏单行格式: 状态 + 名字 + 摘要"""
        return f"{self.icon()} {self.name} — {self.summary}"

    def to_dict(self):
        return {**asdict(self), "icon": self.icon(), "label": self.label()}


# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────

def _run(cmd: str, timeout: float = 5.0) -> tuple[int, str, str]:
    """subprocess 封装,带超时,返回 (returncode, stdout, stderr)"""
    try:
        p = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s"
    except Exception as e:
        return -1, "", str(e)


def _tcp_ping(host: str, port: int = 443, timeout: float = 3.0) -> Optional[float]:
    """TCP 握手测延迟,绕过 ICMP 限制。返回毫秒或 None(失败)"""
    try:
        start = time.perf_counter()
        with socket.create_connection((host, port), timeout=timeout):
            return (time.perf_counter() - start) * 1000
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# L1 物理链路
# ─────────────────────────────────────────────────────────────

def check_l1_link() -> LayerResult:
    """检测 Wi-Fi/网线是否UP, 是否拿到IPv4"""
    # en0 = Wi-Fi (Mac 默认), en1 = 网线 / 其他
    rc, out, err = _run("ifconfig -a 2>&1 | grep -E '^(en|lo)' | head -10")
    active = []
    for line in out.splitlines():
        m = re.match(r"^(\w+):\s+flags=.*<.*UP.*>", line)
        if m:
            active.append(m.group(1))

    # 检查每个UP接口是否有IPv4
    interfaces_with_ip = []
    for iface in active:
        if iface == "lo0":
            continue  # 回环不算物理链路
        rc2, out2, _ = _run(f"ifconfig {iface} | grep 'inet '")
        if rc2 == 0 and out2:
            ip_match = re.search(r"inet (\S+)", out2)
            if ip_match:
                interfaces_with_ip.append((iface, ip_match.group(1)))

    # 按优先级: en0 (Wi-Fi) > en1+ > 其他有线
    PRIORITY = {"en0": 0, "en1": 1, "en2": 2, "en3": 3, "en4": 4}
    interfaces_with_ip.sort(key=lambda x: PRIORITY.get(x[0], 50))

    details = []
    for iface, ip in interfaces_with_ip:
        # 区分 Wi-Fi / 有线 — 用 networksetup 查询硬件端口
        rc2, kind, _ = _run(f"networksetup -listallhardwareports 2>/dev/null | grep -B1 -i 'wi-fi\\|ethernet\\|thunderbolt' | grep 'Device:' | grep -i {iface} | head -1")
        if not kind:
            # 兜底:en0 在 Mac 上默认是 Wi-Fi
            kind = "Wi-Fi" if iface == "en0" else ("有线" if iface.startswith("en") else "其他")
        else:
            # 看上一行 Hardware Port 是啥
            rc3, port_line, _ = _run(f"networksetup -listallhardwareports 2>/dev/null | grep -B2 'Device: {iface}' | head -3")
            if "Wi-Fi" in port_line:
                kind = "Wi-Fi"
            elif "Ethernet" in port_line or "Thunderbolt" in port_line:
                kind = "有线"
        details.append(f"{iface} ({kind}): {ip}")

    if not interfaces_with_ip:
        return LayerResult(
            name="L1 物理链路",
            status=FAIL,
            summary="无可用网络接口(连Wi-Fi或插网线)",
            details=details + [f"活动接口: {active}" if active else "未发现活动接口"],
        )

    # Wi-Fi 连接但信号差 → WARN; 否则 OK
    primary = interfaces_with_ip[0]
    status = OK
    summary = f"{primary[0]} UP, IP={primary[1]}"
    if "tunnel" in primary[0] or "utun" in primary[0]:
        # 隧道接口不算物理链路,跳过
        return LayerResult(
            name="L1 物理链路",
            status=WARN,
            summary=f"仅有隧道接口 {primary[0]},无物理链路",
            details=details,
        )
    return LayerResult(name="L1 物理链路", status=status, summary=summary, details=details)


# ─────────────────────────────────────────────────────────────
# L2 默认网关
# ─────────────────────────────────────────────────────────────

def check_l2_gateway() -> LayerResult:
    """检测默认网关是否可达"""
    rc, out, _ = _run("netstat -rn | grep default | head -3")
    if rc != 0 or not out:
        return LayerResult(
            name="L2 网关",
            status=FAIL,
            summary="无默认路由(可能是VPN接管了路由表?)",
            details=[],
        )

    # 解析所有默认网关
    gateways = []
    for line in out.splitlines():
        m = re.match(r"default\s+(\S+)\s+\S+\s+(\w+)", line)
        if m:
            gateways.append((m.group(2), m.group(1)))

    # 按优先级: en0 (Wi-Fi) > en1+ > utun > 其他
    gw_to_test = None
    for iface, gw in gateways:
        if iface.startswith("en") and "utun" not in iface:
            gw_to_test = gw
            break
    if not gw_to_test and gateways:
        gw_to_test = gateways[0][1]  # 兜底


    if not gw_to_test:
        return LayerResult(
            name="L2 网关",
            status=FAIL,
            summary="默认路由格式异常",
            details=out.splitlines(),
        )

    # TCP ping 替代 ICMP(macOS 防火墙常挡 ICMP)
    latency = _tcp_ping(gw_to_test, port=80, timeout=2.0) or _tcp_ping(gw_to_test, port=443, timeout=2.0)

    if latency is not None:
        return LayerResult(
            name="L2 网关",
            status=OK,
            summary=f"{gw_to_test} 可达 ({latency:.0f}ms)",
            details=[f"网关: {gw_to_test}", f"延迟: {latency:.0f} ms"],
            latency_ms=latency,
        )
    return LayerResult(
        name="L2 网关",
        status=FAIL,
        summary=f"{gw_to_test} 不可达(路由器无响应?)",
        details=[f"网关: {gw_to_test}", "TCP 80/443 均超时"],
    )


# ─────────────────────────────────────────────────────────────
# L3 VPN 隧道
# ─────────────────────────────────────────────────────────────

def _is_vpn_connected(uuid: str) -> bool:
    """scutil --nc show <uuid> 看某个 VPN 是否真的连接上"""
    rc, out, _ = _run(f"scutil --nc show '{uuid}'", timeout=3.0)
    if rc != 0 or not out:
        return False
    # Connected 显示为:   * (Connected)  在 scutil list
    # show 输出会包含 "Connected" + "interface" 信息
    return "Connected" in out and "ifconfig" not in out.lower().split("connected")[0]


def check_l3_vpn() -> LayerResult:
    """检测 VPN 配置与连接状态"""
    # scutil --nc list 列出所有网络服务(含 VPN)
    rc, out, _ = _run("scutil --nc list")
    vpns = []
    if rc == 0 and out:
        # 格式例: * (Disconnected)   UUID VPN (type) "Name"  [VPN:type]
        for line in out.splitlines():
            if "[VPN:" not in line:
                continue
            state_m = re.search(r"\((\w+)\)", line)
            name_m = re.search(r'"([^"]+)"', line)
            uuid_m = re.search(r"\)\s+([\w-]{8,})", line)
            type_m = re.search(r"\[VPN:([^\]]+)\]", line)
            if state_m and name_m:
                uuid = uuid_m.group(1) if uuid_m else ""
                # 二次确认连接状态
                real_state = state_m.group(1)
                if uuid and real_state == "Disconnected":
                    if _is_vpn_connected(uuid):
                        real_state = "Connected"
                vpns.append({
                    "name": name_m.group(1),
                    "state": real_state,
                    "type": type_m.group(1) if type_m else "",
                    "uuid": uuid,
                })

    details = []
    connected_vpns = [v for v in vpns if v["state"] == "Connected"]
    disconnected_vpns = [v for v in vpns if v["state"] != "Connected"]

    for v in vpns:
        marker = "●" if v["state"] == "Connected" else "○"
        details.append(f"  {marker} {v['name']} ({v['type']}) — {v['state']}")

    if not vpns:
        return LayerResult(
            name="L3 VPN 隧道",
            status=WARN,
            summary="系统未配置任何 VPN",
            details=["scutil 未列出 VPN 服务", "提示: 在系统设置 → 网络 添加 VPN"],
        )

    if connected_vpns:
        names = ", ".join(v["name"] for v in connected_vpns)
        return LayerResult(
            name="L3 VPN 隧道",
            status=OK,
            summary=f"VPN 已连接: {names}",
            details=details,
        )

    return LayerResult(
        name="L3 VPN 隧道",
        status=WARN,
        summary=f"{len(vpns)} 个 VPN 配置,均未连接",
        details=details,
    )


def list_vpn_services() -> list[dict]:
    """给菜单栏列 VPN 列表用, 返回 [{name, state, type, uuid}]"""
    rc, out, _ = _run("scutil --nc list")
    result = []
    if rc == 0 and out:
        for line in out.splitlines():
            if "[VPN:" not in line:
                continue
            state_m = re.search(r"\((\w+)\)", line)
            name_m = re.search(r'"([^"]+)"', line)
            uuid_m = re.search(r"\)\s+([\w-]{8,})", line)
            type_m = re.search(r"\[VPN:([^\]]+)\]", line)
            if state_m and name_m:
                result.append({
                    "name": name_m.group(1),
                    "state": state_m.group(1),
                    "type": type_m.group(1) if type_m else "",
                    "uuid": uuid_m.group(1) if uuid_m else "",
                })
    return result


def start_vpn_service(uuid: str) -> tuple[bool, str]:
    """触发 VPN 连接"""
    rc, out, err = _run(f'scutil --nc start "{uuid}"', timeout=10.0)
    if rc == 0:
        return True, "VPN 正在连接..."
    return False, err or out or "启动失败"


# ─────────────────────────────────────────────────────────────
# L4 DNS
# ─────────────────────────────────────────────────────────────

def check_l4_dns(target: str = "baidu.com") -> LayerResult:
    """DNS 解析测试"""
    rc, out, _ = _run(f"dig +time=3 +short {target}")
    if rc != 0 or not out:
        # 退到 nslookup
        rc2, out2, _ = _run(f"nslookup -timeout=3 {target} 2>&1 | grep -A1 'Name:'")
        if rc2 == 0 and out2:
            m = re.search(r"Address:\s*(\S+)", out2)
            if m:
                return LayerResult(
                    name="L4 DNS",
                    status=OK,
                    summary=f"{target} = {m.group(1)}",
                    details=[f"nslookup: {target} → {m.group(1)}"],
                )
        return LayerResult(
            name="L4 DNS",
            status=FAIL,
            summary=f"DNS 解析 {target} 失败",
            details=[f"dig: {out or 'no output'}", "检查 系统设置 → 网络 → DNS"],
        )

    ips = [l for l in out.splitlines() if re.match(r"^\d+\.\d+\.\d+\.\d+$", l)]
    if not ips:
        return LayerResult(
            name="L4 DNS",
            status=FAIL,
            summary=f"DNS 无响应 for {target}",
            details=out.splitlines(),
        )

    return LayerResult(
        name="L4 DNS",
        status=OK,
        summary=f"{target} → {ips[0]}",
        details=[f"解析结果: {', '.join(ips)}", f"目标: {target}"],
    )


# ─────────────────────────────────────────────────────────────
# L5 外网
# ─────────────────────────────────────────────────────────────

def check_l5_external(targets: list[str] | None = None) -> LayerResult:
    """检测外网连通性(支持多个目标,任一可达即OK)"""
    if targets is None:
        targets = [
            ("github.com", 443),       # 公司常用
            ("www.baidu.com", 443),    # 国内通用
            ("1.1.1.1", 443),          # Cloudflare IP,绕开DNS
            ("8.8.8.8", 53),           # Google DNS,纯IP测试
        ]

    results = []
    for host, port in targets:
        latency = _tcp_ping(host, port, timeout=4.0)
        results.append((host, port, latency))

    # 分类
    reachable = [(h, p, l) for h, p, l in results if l is not None]
    unreachable = [(h, p, l) for h, p, l in results if l is None]

    details = []
    for h, p, l in results:
        if l is not None:
            details.append(f"  ✓ {h}:{p} — {l:.0f}ms")
        else:
            details.append(f"  ✗ {h}:{p} — 超时")

    if reachable:
        # 如果只有 IP 通的,域名都不通 → 强烈怀疑 DNS/代理问题
        domain_ok = any(not h.replace(".", "").isdigit() for h, _, _ in reachable)
        ip_ok = any(h.replace(".", "").isdigit() for h, _, _ in reachable)

        if not domain_ok and ip_ok:
            return LayerResult(
                name="L5 外网",
                status=WARN,
                summary="IP 可达但域名不通(DNS 或代理异常)",
                details=details,
            )
        avg = sum(l for _, _, l in reachable) / len(reachable)
        return LayerResult(
            name="L5 外网",
            status=OK,
            summary=f"外网连通, 平均 {avg:.0f}ms",
            details=details,
            latency_ms=avg,
        )

    return LayerResult(
        name="L5 外网",
        status=FAIL,
        summary="完全无外网连接",
        details=details + ["可能原因: 路由器断网 / 公司网络隔离 / VPN 阻断"],
    )


# ─────────────────────────────────────────────────────────────
# 综合入口
# ─────────────────────────────────────────────────────────────

def run_all_checks() -> dict:
    """顺序执行所有检测, 返回结构化结果"""
    start = time.time()

    layers = [
        check_l1_link(),
        check_l2_gateway(),
        check_l3_vpn(),
        check_l4_dns(),
        check_l5_external(),
        check_l6_system_health(),
        check_l7_codex(),
        check_l8_wifi_traffic(),
    ] 

    # 整体状态:取最差
    priority = {FAIL: 3, WARN: 2, OK: 1}
    worst = max(layers, key=lambda x: priority[x.status])
    overall = worst.status

    return {
        "timestamp": time.time(),
        "elapsed_ms": (time.time() - start) * 1000,
        "overall": overall,
        "layers": [layer.to_dict() for layer in layers],
        "vpns": list_vpn_services(),
    }


# ─────────────────────────────────────────────────────────────
# L6 系统健康度 (Mac uptime / utun 堆积 / VPN 进程老旧)
# 用于提前发现 "卡死前兆" — 建议重启
# ─────────────────────────────────────────────────────────────

def _parse_etime_to_seconds(etime: str) -> int | None:
    """把 ps 的 ETIME 字段 (例如 '01-23:07:22' 或 '5:30') 解析成秒"""
    try:
        if "-" in etime:
            # days-HH:MM:SS
            d, hms = etime.split("-", 1)
            days = int(d)
            h, m, s = map(int, hms.split(":"))
            return days * 86400 + h * 3600 + m * 60 + s
        else:
            parts = etime.split(":")
            if len(parts) == 3:
                h, m, s = map(int, parts)
                return h * 3600 + m * 60 + s
            elif len(parts) == 2:
                m, s = map(int, parts)
                return m * 60 + s
        return None
    except Exception:
        return None


def check_l6_system_health() -> LayerResult:
    """检查 macOS 系统健康度
    监控项:
      1. utun 接口数量 (过多预示网络栈需清理)
      2. Mac uptime (超过 14 天建议重启)
      3. VPN 主进程 runtime (Apps Connect 等超过 7 天预兆)
      4. VPN 扩展 runtime (SkyNE.appex 等超过 30 天预兆)
    """
    details = []
    issues = []

    # 1. utun 接口数量
    rc, out, _ = _run("ifconfig -a 2>&1 | grep -E '^utun' | wc -l")
    try:
        utun_count = int(out)
    except Exception:
        utun_count = -1

    if utun_count <= 2:
        details.append(f"  ✓ utun 接口 {utun_count} 个 (健康)")
    elif utun_count <= 5:
        details.append(f"  ⚠ utun 接口 {utun_count} 个 (偏多, 建议清理)")
        issues.append(("warn", f"utun 接口 {utun_count} 个"))
    else:
        details.append(f"  ✗ utun 接口 {utun_count} 个 (过多, 网络栈臃肿)")
        issues.append(("fail", f"utun 接口 {utun_count} 个"))

    # 2. Mac uptime
    rc, out, _ = _run("uptime")
    # '21:55  up 26 days, 5:18, 1 user, ...'
    import re
    m = re.search(r"up\s+(.+?),\s+\d+ user", out)
    uptime_str = m.group(1).strip() if m else "?"
    details.append(f"  Mac 已运行: {uptime_str}")

    # 把 uptime 字符串转成秒 (粗略, 够判断)
    days = 0
    if "day" in uptime_str:
        days_m = re.search(r"(\d+)\s+day", uptime_str)
        if days_m:
            days = int(days_m.group(1))
    if days <= 7:
        details.append(f"  ✓ Mac uptime {days} 天 (< 7 天, 干净)")
    elif days <= 14:
        details.append(f"  ⚠ Mac uptime {days} 天 (建议这周重启)")
        issues.append(("warn", f"Mac uptime {days} 天"))
    else:
        details.append(f"  ✗ Mac uptime {days} 天 (强烈建议今晚重启)")
        issues.append(("fail", f"Mac uptime {days} 天"))

    # 3. VPN 主进程 (Apps Connect 等) — 取 PID + ETIME + 可读名字
    # 用 lsof 找 (Hermes 沙箱下 pgrep 找不到 GUI app)
    rc, out, _ = _run(
        "lsof -nP 2>/dev/null | grep -E '/(Apps Connect|clashx|surge|wireguard|tunnelblick)\\.app/' | "
        "awk '{print $2}' | sort -u | head -3"
    )
    vpn_process_etime = None
    vpn_process_name = None
    for pid_str in [l.strip() for l in out.splitlines() if l.strip().isdigit()]:
        # 用 lsof -F0 拿 c 字段 (含空格的真实 command)
        rc2, cmd_out, _ = _run(f"lsof -p {pid_str} -F0 2>/dev/null")
        m_cmd = re.search(r"\bc([^\x00]+)", cmd_out) if cmd_out else None
        vpn_process_name = m_cmd.group(1).strip() if m_cmd else f"VPN(pid={pid_str})"
        rc3, etime_out, _ = _run(f"ps -o etime= -p {pid_str}")
        secs = _parse_etime_to_seconds(etime_out.strip())
        if secs:
            vpn_process_etime = secs
            break

    if vpn_process_etime is None:
        details.append("  → 未发现常见 VPN 客户端主进程")
    else:
        days = vpn_process_etime // 86400
        hours = (vpn_process_etime % 86400) // 3600
        if days < 1:
            details.append(f"  ✓ {vpn_process_name} 已运行 {days}天{hours}时 (< 1 天)")
        elif days < 7:
            details.append(f"  ⚠ {vpn_process_name} 已运行 {days}天{hours}时 (建议重启 VPN)")
            issues.append(("warn", f"VPN {vpn_process_name} {days} 天"))
        else:
            details.append(f"  ✗ {vpn_process_name} 已运行 {days}天{hours}时 (重启 VPN)")
            issues.append(("fail", f"VPN {vpn_process_name} {days} 天"))

    # 4. VPN 扩展 (SkyNE.appex 等) — 用 lsof 找 .appex
    rc, out, _ = _run(
        "lsof -nP 2>/dev/null | grep -E 'SkyNE\\.appex|\\.appex/SkyNE|wireguard-go|ipsec' | "
        "awk '{print $2}' | sort -u | head -3"
    )
    ext_etime = None
    ext_name = None
    for pid_str in [l.strip() for l in out.splitlines() if l.strip().isdigit()]:
        rc2, cmd_out, _ = _run(f"lsof -p {pid_str} -F0 2>/dev/null")
        m_cmd = re.search(r"\bc([^\x00]+)", cmd_out) if cmd_out else None
        ext_name = m_cmd.group(1).strip() if m_cmd else f"VPNExt(pid={pid_str})"
        rc3, etime_out, _ = _run(f"ps -o etime= -p {pid_str}")
        secs = _parse_etime_to_seconds(etime_out.strip())
        if secs:
            ext_etime = secs
            break

    if ext_etime is None:
        details.append("  → 未发现 VPN 扩展 (NetworkExtension)")
    else:
        days = ext_etime // 86400
        if days < 30:
            details.append(f"  ✓ {ext_name} 扩展已运行 {days} 天 (< 30 天)")
        elif days < 60:
            details.append(f"  ⚠ {ext_name} 扩展已运行 {days} 天 (考虑重启)")
            issues.append(("warn", f"VPN 扩展 {ext_name} {days} 天"))
        else:
            details.append(f"  ✗ {ext_name} 扩展已运行 {days} 天 (强烈重启)")
            issues.append(("fail", f"VPN 扩展 {ext_name} {days} 天"))

    # 汇总
    if not issues:
        return LayerResult(
            name="L6 系统健康度",
            status=OK,
            summary=f"utun {utun_count}个 / Mac {days}天 / 系统干净",
            details=details,
        )

    has_fail = any(s == "fail" for s, _ in issues)
    has_warn = any(s == "warn" for s, _ in issues)
    status = FAIL if has_fail else WARN
    summary = ", ".join(desc for _, desc in issues[:2])
    if len(issues) > 2:
        summary += f" (+{len(issues)-2} 项)"

    return LayerResult(name="L6 系统健康度", status=status, summary=summary, details=details)


# ─────────────────────────────────────────────────────────────
# L7 Codex 连接 (OpenAI API 专项)
# ─────────────────────────────────────────────────────────────

def check_l7_codex() -> LayerResult:
    """专项检测 OpenAI / Codex CLI 的网络连接性
    检测 3 件事:
      1. DNS 解析 api.openai.com
      2. TCP 握手 api.openai.com:443
      3. HTTPS 401 (代表连接通 + 但 API key 无效, 与"网络不通"是不同信号)
    """
    details = []
    has_network_ok = True
    auth_error_seen = False
    failure_reason = None

    # 1. DNS 解析
    rc, out, _ = _run("dig +time=3 +short api.openai.com 2>&1")
    api_ip = None
    if rc == 0 and out:
        ip_match = re.search(r"(\d+\.\d+\.\d+\.\d+)", out)
        if ip_match:
            api_ip = ip_match.group(1)
    if api_ip:
        details.append(f"  ✓ DNS 解析 api.openai.com → {api_ip}")
    else:
        details.append(f"  ✗ DNS 解析 api.openai.com 失败")
        has_network_ok = False
        failure_reason = "DNS 解析失败"

    # 2. TCP 握手
    latency = _tcp_ping("api.openai.com", 443, timeout=4.0)
    if latency is not None:
        details.append(f"  ✓ TCP 握手 api.openai.com:443 ({latency:.0f}ms)")
    else:
        details.append(f"  ✗ TCP 握手 api.openai.com:443 超时")
        has_network_ok = False
        if not failure_reason:
            failure_reason = "TCP 握手失败"

    # 3. HTTPS 试探 (用无效 key, 看服务器响应)
    rc, out, _ = _run(
        "curl -sS -o /dev/null -w '%{http_code}' --max-time 8 "
        "-H 'Authorization: Bearer probe' "
        "-H 'Content-Type: application/json' "
        "-X POST https://api.openai.com/v1/chat/completions "
        "-d '{\"model\":\"gpt-4o-mini\",\"messages\":[],\"max_tokens\":1}'"
    )
    http_code = out.strip() if out else ""
    if http_code in ("401", "400"):
        # 401 = 网络通但 key 无效 (正常状态, 不是连接问题)
        # 400 = 我们的 probe body 太简陋, 但说明**连接到了 OpenAI**
        details.append(f"  ✓ HTTPS 到达 OpenAI (HTTP {http_code} — 网络层通, key 问题不在此处)")
        auth_error_seen = True
    elif http_code == "200":
        # 罕见 — probe key 居然被认了
        details.append(f"  ? HTTPS 200 (异常, probe key 不应有效)")
    elif http_code.startswith("2"):
        details.append(f"  ✓ HTTPS {http_code} (OpenAI 可达)")
    elif http_code in ("", "000"):
        details.append(f"  ✗ HTTPS 无响应 (网络层阻塞)")
        has_network_ok = False
        if not failure_reason:
            failure_reason = "HTTPS 无响应"
    elif http_code.startswith(("5", "4")):
        # 5xx 是 OpenAI 服务问题
        details.append(f"  ⚠ HTTPS {http_code} (OpenAI 服务端问题, 不是你网络)")
    else:
        details.append(f"  ? HTTPS {http_code} (未知状态)")

    # 判定整体状态
    if has_network_ok and auth_error_seen:
        # 网络 OK, 但 key 无效 — 这是配置问题不是网络问题
        return LayerResult(
            name="L7 Codex 连接",
            status=OK,
            summary="网络层 100% 通, key 问题在 OpenAI 账户侧",
            details=details,
        )
    elif has_network_ok:
        return LayerResult(
            name="L7 Codex 连接",
            status=OK,
            summary="OpenAI 可达",
            details=details,
        )
    else:
        return LayerResult(
            name="L7 Codex 连接",
            status=FAIL,
            summary=f"网络层 {failure_reason}",
            details=details,
        )


# ─────────────────────────────────────────────────────────────
# L8 WiFi 信号强度 + 流量监控
# ─────────────────────────────────────────────────────────────

def check_l8_wifi_traffic() -> LayerResult:
    """L8a: 当前 Wi-Fi 信号强度 (RSSI / Noise / SNR)
    L8b: 流量监控 (en0 收/发字节速率 — 5 秒间隔采样)

    设计要点:
      - L8a 用 system_profiler SPAirPortDataType 解析当前 SSID 的信号
      - L8b 用 netstat -ib 取 Ibytes/Obytes 两次采样算速率
      - 两个检测合并成一个 LayerResult (避免 run_all_checks 又慢 1 倍)
    """
    details = []
    issues = []  # (level, desc)

    # ─── L8a: Wi-Fi 信号强度 ───
    rssi_dbm = None
    noise_dbm = None
    ssid = None
    channel = None

    # system_profiler 输出很长, 限制一下区域 (只取当前网络信息)
    rc, out, _ = _run(
        "system_profiler SPAirPortDataType 2>/dev/null | "
        "awk '/Current Network Information:/{flag=1} flag; /Other Local Wi-Fi/{flag=0}'",
        timeout=8.0
    )
    if rc == 0 and out:
        # system_profiler 输出格式:
        #   Current Network Information:
        #       <SSID 名字>:
        #           PHY Mode: 802.11 a/b/g/n/ac/ax
        #           Channel: 44 (5GHz, 160MHz)
        #           Signal / Noise: -57 dBm / -92 dBm
        # SSID 行的判定: 缩进 >= 4 个空格, 但不是 "PHY Mode:" 等已知字段名
        lines = out.splitlines()
        skip_labels = {"PHY Mode", "Channel", "Network Type", "Security",
                       "Signal / Noise", "Other Local Wi-Fi", "Current Network Information"}
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped in skip_labels:
                continue
            # SSID 行: "    <name>:" (空行后第一行)
            if line.endswith(":") and not stripped.startswith(("<", "/")):
                ssid = stripped.rstrip(":")
                continue
            # Signal / Noise: -57 dBm / -92 dBm
            m_sn = re.search(r"Signal\s*/\s*Noise:\s*(-?\d+)\s*dBm\s*/\s*(-?\d+)\s*dBm", line)
            if m_sn:
                rssi_dbm = int(m_sn.group(1))
                noise_dbm = int(m_sn.group(2))
                continue
            # Channel: 44 (5GHz, 160MHz)
            m_chan = re.search(r"Channel:\s*(\d+)", line)
            if m_chan and channel is None:
                channel = m_chan.group(1)

    # 判定信号强度
    if rssi_dbm is not None:
        snr = rssi_dbm - (noise_dbm or -100) if noise_dbm else None
        if rssi_dbm >= -50:
            sig_status = "强"
            sig_icon = "🟢"
        elif rssi_dbm >= -65:
            sig_status = "良好"
            sig_icon = "🟢"
        elif rssi_dbm >= -75:
            sig_status = "一般"
            sig_icon = "🟡"
        else:
            sig_status = "弱"
            sig_icon = "🔴"

        snr_str = f" SNR {snr}dB" if snr is not None else ""
        ch_str = f" ch{channel}" if channel else ""
        details.append(f"  {sig_icon} Wi-Fi 信号: {rssi_dbm}dBm (噪声 {noise_dbm}dBm){snr_str}{ch_str}")
        # macOS 12+ 默认隐藏 SSID → 显示 <redacted>, 给用户提示而非空字符串
        if ssid and ssid != "Current Network Information":
            if "<" in ssid:
                details.append(f"  SSID: <私有> (macOS 隐藏)")
            else:
                details.append(f"  SSID: {ssid}")

        if rssi_dbm < -75:
            issues.append(("warn", f"Wi-Fi 信号 {rssi_dbm}dBm ({sig_status})"))
    else:
        # 没拿到 RSSI — 多半是因为没接 Wi-Fi (网线/有线)
        rc2, media_out, _ = _run("ifconfig en0 2>&1 | grep -E 'media:|status:'")
        media = (media_out or "").strip()
        if "autoselect" in media or "status: active" in media:
            details.append(f"  → en0 {media} (无 Wi-Fi, 用以太网)")
        else:
            details.append(f"  → 无法读取 Wi-Fi 信号 ({media or 'en0 状态未知'})")

    # ─── L8b: 流量监控 (en0 速率) ───
    rc, out1, _ = _run("netstat -ib 2>/dev/null | grep -E '^en0\\s' | head -1")
    if rc == 0 and out1:
        # 列格式: en0 1500 ... Ipkts ... Ibytes Opkts ... Obytes
        parts = out1.split()
        try:
            ibytes_1 = int(parts[6])  # Ibytes 列
            obytes_1 = int(parts[9])  # Obytes 列
        except (IndexError, ValueError):
            ibytes_1 = obytes_1 = None
    else:
        ibytes_1 = obytes_1 = None

    # 2 秒间隔采样 (比原设计 5 秒更短, 不让 run_all_checks 整体超过 10 秒)
    time.sleep(2)
    rc, out2, _ = _run("netstat -ib 2>/dev/null | grep -E '^en0\\s' | head -1")
    if rc == 0 and out2:
        parts = out2.split()
        try:
            ibytes_2 = int(parts[6])
            obytes_2 = int(parts[9])
        except (IndexError, ValueError):
            ibytes_2 = obytes_2 = None
    else:
        ibytes_2 = obytes_2 = None

    def fmt_rate(bps: float | None) -> str:
        if bps is None:
            return "?/s"
        if bps < 1024:
            return f"{bps:.0f}B/s"
        if bps < 1024 * 1024:
            return f"{bps / 1024:.1f}KB/s"
        return f"{bps / 1024 / 1024:.2f}MB/s"

    if ibytes_1 is not None and ibytes_2 is not None and obytes_1 is not None and obytes_2 is not None:
        delta_in = ibytes_2 - ibytes_1
        delta_out = obytes_2 - obytes_1
        rate_in = delta_in / 2.0  # bytes/sec
        rate_out = delta_out / 2.0
        details.append(f"  📊 流量: ↓ {fmt_rate(rate_in)}  ↑ {fmt_rate(rate_out)} (en0, 2s 采样)")
        details.append(f"  累计: 下行 {_fmt_bytes(ibytes_2)}, 上行 {_fmt_bytes(obytes_2)}")
    else:
        details.append("  → 无法读取 en0 流量统计")

    # 汇总
    if not issues:
        # OK 状态
        parts = []
        if rssi_dbm is not None and rssi_dbm < 0:
            parts.append(f"Wi-Fi {rssi_dbm}dBm")
        parts.append("流量 ok")
        summary = ", ".join(parts)
        return LayerResult(
            name="L8 Wi-Fi/流量",
            status=OK,
            summary=summary,
            details=details,
        )

    # WARN/FAIL 路径
    has_fail = any(s == "fail" for s, _ in issues)
    status = FAIL if has_fail else WARN
    summary = ", ".join(desc for _, desc in issues)
    return LayerResult(name="L8 Wi-Fi/流量", status=status, summary=summary, details=details)


def _fmt_bytes(n: int) -> str:
    """字节数格式化 (1024 进制)"""
    if n < 1024:
        return f"{n}B"
    if n < 1024 ** 2:
        return f"{n/1024:.1f}KB"
    if n < 1024 ** 3:
        return f"{n/1024/1024:.1f}MB"
    return f"{n/1024/1024/1024:.2f}GB"


# ─────────────────────────────────────────────────────────────
# CLI 测试入口
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    print(json.dumps(run_all_checks(), indent=2, ensure_ascii=False))

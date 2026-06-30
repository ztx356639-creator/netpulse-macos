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
# CLI 测试入口
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    print(json.dumps(run_all_checks(), indent=2, ensure_ascii=False))

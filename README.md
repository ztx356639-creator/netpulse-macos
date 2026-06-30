# NetPulse 📡

> macOS 菜单栏网络诊断小工具 — **7 层独立检测**,一眼看清"VPN 登不上"和"Mac 该不该重启"的根因

## 它解决什么问题

每次 VPN 登不上时,你不知道是:
- 物理链路断了?
- 路由器/网关失联?
- VPN 隧道本身?
- DNS 解析挂了?
- 外网本身不通?
- **Mac 系统健康度是否需要重启?**
- **Codex/OpenAI API 是否网络可达?**

NetPulse 常驻菜单栏,把网络栈 + 系统健康拆成 **7 层独立检测**,每层用 🟢🟡🔴 显示状态。

## 7 层诊断

| 层 | 检测 | 工具 |
|---|---|---|
| L1 物理链路 | Wi-Fi/网线接口 UP + IPv4 | `ifconfig` + `networksetup` |
| L2 默认网关 | 网关是否可达 | TCP ping |
| L3 VPN 隧道 | VPN 配置 + 连接状态 | `scutil --nc` |
| L4 DNS | 域名能否解析 | `dig` |
| L5 外网 | 实际能否连通目标 | 多目标 TCP 探测 |
| **L6 系统健康度** | **utun 堆积 / Mac uptime / VPN 进程老化** | `ifconfig` / `uptime` / `lsof` |
| **L7 Codex 连接** | **OpenAI API 网络可达 + key 区分** | `dig` / socket / `curl` |

## 关于 L6 系统健康度

L6 是**提前发现"卡死前兆"的关键**。它监测 4 个指标:

| 指标 | OK | WARN | FAIL |
|---|---|---|---|
| utun 接口数量 | ≤2 | 3-5 | ≥6 |
| Mac uptime | ≤7 天 | 7-14 天 | >14 天 |
| VPN 主进程 ETIME | <1 天 | 1-7 天 | >7 天 |
| VPN 扩展 ETIME (SkyNE 等) | <30 天 | 30-60 天 | >60 天 |

**任何一项 FAIL 都建议今晚重启**。

**完整诊断** (不只是 L6 — 还有其它网络栈检测) 见菜单栏点击 "NetPulse 详情"。

## 关于 L7 Codex 连接

L7 专项检测 OpenAI API,做三件事:
1. DNS 解析 api.openai.com
2. TCP 握手 443
3. HTTPS 试探(用 probe key,期望 401)

**401 = 网络通但 key 无效** → 这条会明确告诉你"**网络层 100% 通,key 问题在 OpenAI 账户侧**",省得你跟网络死磕。

## 截图示意

```
菜单栏:  📡 🟢
   ↓
┌──────────────────────────────────┐
│ 🚀 网络体检                       │
│ ─────────────────────────────────│
│ 🟢 L1 物理链路 — en0 UP, 192.168...│
│ 🟢 L2 网关 — 192.168.0.1 可达 (8ms)│
│ 🟢 L3 VPN 隧道 — VPN 已连接: App... │
│ 🟢 L4 DNS — baidu.com → 198.18...  │
│ 🟢 L5 外网 — 外网连通, 平均 2ms   │
│ ─────────────────────────────────│
│ 🔄 重新检查                       │
│ 📋 复制报告                       │
│ ⏱  自动监控 (每 30s) ✓            │
│ ─────────────────────────────────│
│ ⚙  VPN 配置                       │
│   ● Apps Connect                 │
│ ─────────────────────────────────│
│ ℹ️  关于                          │
│ ❌ 退出                           │
└──────────────────────────────────┘
```

## 安装

```bash
# 1. 克隆
git clone https://github.com/ztx356639-creator/netpulse-macos.git
cd netpulse-macos

# 2. 创建虚拟环境并安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install rumps py2app

# 3. 命令行诊断(不开 GUI)
./run.sh check

# 4. 启动菜单栏 app
./run.sh run

# 5. 打包成 .app
./run.sh build
```

## 命令行用法

```bash
./run.sh           # 显示帮助
./run.sh run       # 启动菜单栏 app (源码模式)
./run.sh test      # 跑诊断引擎单元测试
./run.sh check     # 跑一次诊断输出 JSON (7 层)
./run.sh build     # 打包 + 部署到 ~/Documents/skills/
./run.sh smoke     # 跑 .app bundle 的 smoke test
./run.sh inspect   # 启动 NS_MENU_INSPECT 验证菜单结构
```

### 看每层的细节

```bash
./run.sh check | python3 -m json.tool | head -80
```

会输出 JSON 包含 `layers` 数组(L1-L7),每项含 `name/status/summary/details`,直接看哪层出问题。

## 技术栈

- **Python 3.9+**
- **rumps 0.4.0** — 菜单栏 app 框架 (PyObjC 封装)
- **py2app 0.28+** — 打包成 macOS .app
- **macOS 26 (Tahoe)** 测试通过

## 维护指南

### 改了代码后,完整流程

```bash
# 1. 在本地测试
./run.sh check         # 跑一次诊断看结果
./run.sh test          # 跑单元测试

# 2. 打包并部署
./run.sh build         # 自动验证 MD5 一致

# 3. 验证 menu 结构 (可选,但推荐改 UI 时跑)
./run.sh inspect

# 4. 提交 + 推送
git add -A
git commit -m "feat: 描述你的改动"
git push origin main
```

### 常见改动场景

| 改动 | 操作 |
|---|---|
| 修复诊断逻辑 | 改 `src/diagnostics.py` → `./run.sh check` 验证 → `./run.sh build` |
| 改菜单文字/顺序 | 改 `src/app.py` → `./run.sh inspect` 验证 → `./run.sh build` |
| 加新检测层 | `src/diagnostics.py` 加新函数 + `_LAYER_REGISTRY` 注册 → 同步 `src/app.py` UI |
| 加新的子命令 | `run.sh` 加 case 分支 |

## 关于双退出按钮的历史 bug

**症状**: 菜单下拉同时有中文 `❌ 退出` 和英文 `Quit` 两个。

**根因**: `rumps.App.__init__` 默认 `quit_button='Quit'`,会在菜单末尾自动加一个英文 Quit。

**修复**: `super().__init__(name, title=..., quit_button=None)`。

**教训**: 自定义退出项时,必须显式 `quit_button=None`,否则与自己的中文退出项共存。

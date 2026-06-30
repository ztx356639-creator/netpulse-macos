# NetPulse 📡

> macOS 菜单栏网络诊断小工具 — 一眼看清"VPN 登不上"的根因

## 它解决什么问题

每次 VPN 登不上时,你不知道是:
- 物理链路断了?
- 路由器/网关失联?
- VPN 隧道本身?
- DNS 解析挂了?
- 外网本身不通?

NetPulse 常驻菜单栏,把网络栈拆成 **5 层独立检测**,每层用 🟢🟡🔴 显示状态。
VPN 断了? 立刻知道是哪一层的事,不用瞎猜。

## 5 层诊断

| 层 | 检测 | 工具 |
|---|---|---|
| L1 物理链路 | Wi-Fi/网线接口 UP + IPv4 | `ifconfig` + `networksetup` |
| L2 默认网关 | 网关是否可达 | TCP ping |
| L3 VPN 隧道 | VPN 配置 + 连接状态 | `scutil --nc` |
| L4 DNS | 域名能否解析 | `dig` |
| L5 外网 | 实际能否连通目标 | 多目标 TCP 探测 |

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
./run.sh check     # 跑一次诊断, 输出 JSON
./run.sh build     # 打包 + 部署到 ~/Documents/skills/
./run.sh smoke     # 跑 .app bundle 的 smoke test
./run.sh inspect   # 启动 NS_MENU_INSPECT 验证菜单结构
```

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

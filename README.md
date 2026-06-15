# 🕵️‍♂️ TCP Idle Timeout Probe

云网络 TCP 长连接静默丢包专业诊断工具

---

## 📌 背景

在 AWS、阿里云、腾讯云等公有云环境中，NAT 网关、负载均衡（SLB/ELB）或云安全组通常会清理长时间空闲（Idle）的 TCP 会话状态，且往往不会发送 RST 包（即静默丢弃）。

这会导致业务侧长连接在休眠后出现：

- ETIMEDOUT（黑洞）
- ECONNRESET（连接重置）
- 假死连接

---

## 🚀 工具简介

TCP Idle Timeout Probe 是一款轻量级异步网络探测工具：

- 🐍 Python asyncio 实现
- 📦 单文件 / 零依赖（标准库）
- ⚡ 并发梯度 idle-time 探测
- 🔄 全双工 ACK 验证

用于测量云网络 TCP Session / Conntrack idle timeout 临界值。

---

## ✨ 核心特性

### 🛡️ 纯净协议栈（Zero-Pollution）
不使用 SO_LINGER=0 等 hack 行为，保证抓包可分析性。

### 🧠 控制面 / 数据面分离
- Data Plane：socket I/O
- Control Plane：调度 / 熔断 / 监控

### ⚡ 熔断机制
连续失败自动取消剩余探测任务。

### 🌊 背压与错峰
- 有界队列防止资源过载
- stagger 控制连接风暴

### 🔄 全双工验证
```
INIT → PUSH → ACK
```

---

## ⚙️ 工作流程

1. Client 发送 INIT-{idle}
2. Server sleep idle 时间
3. Server 返回 PUSH
4. Client 返回 ACK
5. 判定链路状态

---

## 📥 安装

无需依赖：

```bash
python3 tcp_idle_probe.py
```

---

## 🚀 使用方式

### 🖥️ Server
```bash
python3 tcp_idle_probe.py server --port 9999
```

### 💻 Client

基础模式：
```bash
python3 tcp_idle_probe.py client --host <server-ip>
```

精细探测：
```bash
python3 tcp_idle_probe.py client --host <server-ip> --start 250 --step 10 --count 15
```

高抖动网络：
```bash
python3 tcp_idle_probe.py client --host <server-ip> --tolerance 60 --max-fails 3
```

---

## 📊 结果说明

| 状态 | 含义 |
|------|------|
| SUCCESS | 正常通信 |
| TIMEOUT | 云网络黑洞 |
| RESET | 被中间设备重置 |
| EOF | 连接关闭 |
| ERROR | 异常 |

---

## 📈 输出示例

```
========================
📊 探测结果统计
========================
SUCCESS : 5
TIMEOUT : 2
RESET   : 0
EOF     : 0
ERROR   : 0
ABORTED : 13
========================
```

---

## 🧠 状态解释

- TIMEOUT：NAT / SLB silently dropped connection
- RESET：network middlebox actively reset
- EOF：server closed connection

---

## 🛠️ 参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| --host | 必填 | 目标地址 |
| --port | 9999 | 端口 |
| --start | 60 | 起始 idle |
| --step | 60 | 步进 |
| --count | 20 | 探测数量 |
| --tolerance | 30 | 容忍时间 |
| --max-fails | 2 | 熔断阈值 |
| --stagger | 0.5 | 错峰间隔 |

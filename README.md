🕵️‍♂️ TCP Idle Timeout Probe
云网络 TCP 长连接静默丢包专业诊断工具

在 AWS、阿里云、腾讯云等公有云环境中，NAT 网关、负载均衡（SLB/ELB）或云安全组通常会清理长时间空闲（Idle）的 TCP 会话状态，且往往不会发送 RST 包（即静默丢弃）。这会导致业务侧的长连接在休眠唤醒后发生 ETIMEDOUT（黑洞现象）或持续处于假死状态。

TCP Idle Timeout Probe 是一款纯 Python 编写的轻量级、无依赖的异步诊断工具。它能通过并发多组不同休眠时长的 TCP 连接，快速且精准地测算出云厂商底层网络设备的 TCP Session / Conntrack Timeout 临界值。
✨ 核心架构与特性
本工具经历了严格的工程化重构，专为极致的网络排障场景设计：

🛡️ 纯净协议栈 (Zero-Pollution)：坚决抵制 SO_LINGER=0 等会污染网络环境的 Hack 手段。完全保留原生 TCP 协议栈行为，完美配合 tcpdump / Wireshark 抓包取证，不会产生脚本伪造的脏数据。

🧠 数据/控制面彻底分离：采用工业级生产者-消费者模型。Data Plane 仅负责纯粹的 Socket I/O 操作；Control Plane 运行独立的 Watcher 守护协程集中处理熔断裁决，彻底杜绝并发状态污染。

⚡ 智能熔断 (Circuit Breaker)：连续 N 次探测失败后自动拉闸。调度器主动下发 Cancel 指令瞬间切断底层 OS 等待，避免无意义的挂机长等。

🌊 背压与防风暴 (Backpressure & Stagger)：引入有界事件队列防内存溢出，并支持 TCP 建连错峰排队（Stagger），防止瞬时海量 SYN 包对云原生网关造成 Conntrack 冲击导致“伪失败”。

🔄 全双工 ACK 验证：不仅测试下行推送，还要求上行确认，严格验证云网络会话的半开/全开状态。

📦 零第三方依赖：单文件脚本，无需 pip install，拉到任意 Linux/Mac/Windows 机器上直接运行。

⚙️ 工作原理
Client 发起连接，通知 Server 预期的休眠时间（例如 300s）。

双端同步休眠：不消耗任何 CPU 资源。

休眠结束后，Server 尝试主动下发 PUSH 数据包。

Client 收到后立即回复 ACK 数据包验证上行链路。

精准分类捕获底层的 ETIMEDOUT（网络黑洞）、ECONNRESET（网关拒绝）或 EOF（对端断开）。

📥 安装
无需安装，只需下载单个文件即可运行：
(环境要求：Python 3.7 及以上)

🚀 快速上手
你需要两台机器：一台作为目标云主机（Server），一台作为发起探测的机器（Client）。

1. 启动 Server 端
在目标云主机上启动监听（确保安全组已放行对应端口，默认 9999）：
python3 tcp_idle_probe.py server --port 9999
2. 启动 Client 端 (发起探测)
场景 A：一键默认排查（推荐）
从 60 秒开始，每次递增 60 秒，并发测试 20 个连接（覆盖 1分钟 ~ 20分钟 的范围）：
python3 tcp_idle_probe.py client --host <云主机公网IP>
场景 B：已知范围的精细化刺探
如果你怀疑超时时间在 300 秒左右，想以 10 秒为步进精确寻找临界点（测试 250s ~ 400s）：
python3 tcp_idle_probe.py client --host <云主机公网IP> --start 250 --step 10 --count 15
场景 C：网络较差时的宽容探测
如果你所在的网络环境 RTT 抖动较大，可以通过 --tolerance 放宽双端通信的等待余量，并通过 --max-fails 提高熔断阈值：
python3 tcp_idle_probe.py client --host <云主机公网IP> --tolerance 60.0 --max-fails 3
📊 结果解读
测试结束后（或触发熔断后），控制台会输出一张优雅的汇总统计表：
==================================================
 📊 探 测 结 果 统 计
==================================================
  ✅ 成功 (SUCCESS) : 5
  🕳️  超时 (TIMEOUT) : 2  <-- [静默丢包]
  ⛔ 重置 (RESET)   : 0  <-- [主动阻断]
  🔌 断开 (EOF)     : 0
  ❌ 异常 (ERROR)   : 0
  ⏭️  跳过 (ABORTED) : 13
==================================================
TIMEOUT (ETIMEDOUT)：这是最典型的云安全组/NAT黑洞行为。会话已在中间网络设备被悄悄删除，数据包发进黑洞。

RESET (ECONNRESET)：说明网络中间件或防火墙比较“讲究”，在发现旧会话时主动伪造了 RST 包拒绝了连接。

EOF：对端操作系统正常的连接关闭（通常是 Server 端进程崩溃或被主动 Kill 导致）。
🛠️ 高级参数配置
可以通过 python3 tcp_idle_probe.py client -h 查看完整参数：
参数	默认值	描述
--host	必填	目标 Server 的 IP 地址
--port	9999	目标 Server 的监听端口
--start	60	起始空闲时间（秒）
--step	60	每次递增的步进时间（秒）
--count	20	并发发起的探测连接总数
--tolerance	30.0	双端统一步调的网络容忍度（秒）
--max-fails	2	连续失败 N 次后触发全局熔断
--stagger	0.5	错峰建连的延迟间隔（秒），防止瞬时网络风暴

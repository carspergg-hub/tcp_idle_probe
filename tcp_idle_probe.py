import asyncio
import socket
import argparse
import sys
from datetime import datetime

# ==========================================
# 工具类：支持标准输出与文件双向流
# ==========================================
class Tee:
    def __init__(self, *files):
        self.files = files
    def write(self, obj):
        for f in self.files:
            f.write(obj)
            f.flush()
    def flush(self):
        for f in self.files:
            f.flush()

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def optimize_socket(writer):
    try:
        sock = writer.get_extra_info('socket')
        if sock is not None:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 0)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception as e:
        log(f"[!] 警告: 无法设置底层 Socket 选项: {e}")

# ==========================================
# 服务端逻辑
# ==========================================
async def handle_client(reader, writer, tolerance):
    addr = writer.get_extra_info('peername')
    optimize_socket(writer)
    try:
        data = await asyncio.wait_for(reader.read(1024), timeout=10.0)
        if not data:
            return
        msg = data.decode(errors='ignore')
        if msg.startswith("INIT-"):
            idle_time = int(msg.split("-")[1])
            log(f"[Server] {addr} 请求休眠 {idle_time}s")
            await asyncio.sleep(idle_time)
            writer.write(f"PUSH-{idle_time}".encode())
            await writer.drain()
            ack_data = await asyncio.wait_for(reader.read(1024), timeout=tolerance)
            if ack_data and ack_data.decode(errors='ignore') == f"ACK-{idle_time}":
                log(f"[Server] {addr} (Idle {idle_time}s) -> 全双工成功")
            else:
                log(f"[Server] {addr} (Idle {idle_time}s) -> 失败: 未收到有效 ACK")
    except Exception as e:
        log(f"[Server] {addr} -> 异常: {type(e).__name__}: {e}")
    finally:
        if not writer.is_closing():
            try:
                writer.close()
                await writer.wait_closed()
            except OSError:
                pass

async def run_server(host, port, tolerance):
    server = await asyncio.start_server(lambda r, w: handle_client(r, w, tolerance), host, port)
    log(f"[*] 服务端启动: {host}:{port} | 容忍度: {tolerance}s")
    async with server:
        await server.serve_forever()

# ==========================================
# 客户端逻辑 (Data Plane)
# ==========================================
async def probe_task(host, port, idle_time, tolerance, delay, result_queue):
    writer = None
    status = "ERROR"

    if delay > 0:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            await result_queue.put((idle_time, "ABORTED"))
            return

    try:
        reader, writer = await asyncio.open_connection(host, port)
        optimize_socket(writer)

        writer.write(f"INIT-{idle_time}".encode())
        await writer.drain()
        log(f"[Client] [{idle_time:4}s] 已建连，等待 PUSH ...")

        try:
            data = await asyncio.wait_for(reader.read(1024), timeout=idle_time + tolerance)
            if data and data.decode(errors='ignore') == f"PUSH-{idle_time}":
                writer.write(f"ACK-{idle_time}".encode())
                await writer.drain()
                status = "SUCCESS"
            else:
                status = "EOF"
        except asyncio.CancelledError:
            status = "ABORTED"
            raise

    except asyncio.TimeoutError:
        status = "TIMEOUT"
    except ConnectionResetError:
        status = "RESET"
    except Exception as e:
        if not isinstance(e, asyncio.CancelledError):
            status = "ERROR"

    finally:
        if status != "ABORTED":
            await result_queue.put((idle_time, status))
        if writer and not writer.is_closing():
            try:
                writer.close()
                await writer.wait_closed()
            except OSError:
                pass

# ==========================================
# 控制中心 (Control Plane)
# ==========================================
async def monitor_task(result_queue, tasks, max_fails):
    stats = {"SUCCESS": 0, "TIMEOUT": 0, "RESET": 0, "EOF": 0, "ERROR": 0, "ABORTED": 0}
    fails = 0

    while True:
        item = await result_queue.get()
        if item is None:
            result_queue.task_done()
            break

        idle_time, status = item
        stats[status] += 1

        fails = 0 if status == "SUCCESS" else fails + 1

        if fails >= max_fails:
            for t in tasks:
                t.cancel()

        result_queue.task_done()

    print("\n" + "=" * 50 + "\n 📊 探 测 结 果 统 计 \n" + "=" * 50)
    for k, v in stats.items():
        print(f"  {k:8}: {v}")
    print("=" * 50)

# ==========================================
# 启动入口
# ==========================================
async def run_client(host, port, start, step, count, tolerance, max_fails, stagger, output):
    if output:
        sys.stdout = Tee(sys.stdout, open(output, 'a', encoding='utf-8'))

    log(f"[*] 策略: {start}s起步, 步进{step}s, 共{count}次 | 阻断: {max_fails}次")

    result_queue = asyncio.Queue(maxsize=count)

    tasks = [
        asyncio.create_task(
            probe_task(host, port, start + i * step, tolerance, i * stagger, result_queue)
        ) for i in range(count)
    ]

    watcher = asyncio.create_task(monitor_task(result_queue, tasks, max_fails))

    await asyncio.gather(*tasks, return_exceptions=True)

    await result_queue.put(None)
    await watcher

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="role")

    s = sub.add_parser("server")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=9999)
    s.add_argument("--tolerance", type=float, default=30.0)

    c = sub.add_parser("client")
    c.add_argument("--host", required=True)
    c.add_argument("--port", type=int, default=9999)
    c.add_argument("--start", type=int, default=60)
    c.add_argument("--step", type=int, default=60)
    c.add_argument("--count", type=int, default=20)
    c.add_argument("--tolerance", type=float, default=30.0)
    c.add_argument("--max-fails", type=int, default=2)
    c.add_argument("--stagger", type=float, default=0.5)
    c.add_argument("--output")

    args = p.parse_args()

    if args.role == "server":
        asyncio.run(run_server(args.host, args.port, args.tolerance))
    elif args.role == "client":
        asyncio.run(run_client(args.host, args.port, args.start, args.step, args.count, args.tolerance, args.max_fails, args.stagger, args.output))

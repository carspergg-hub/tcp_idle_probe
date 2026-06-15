import asyncio
import socket
import argparse
import sys
from datetime import datetime

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
                
    except asyncio.TimeoutError:
        log(f"[Server] {addr} -> ETIMEDOUT (等待 ACK 超时)")
    except ConnectionResetError:
        log(f"[Server] {addr} -> ECONNRESET (收到 RST)")
    except BrokenPipeError:
        log(f"[Server] {addr} -> EPIPE (管道破裂)")
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
    server_cb = lambda r, w: handle_client(r, w, tolerance)
    server = await asyncio.start_server(server_cb, host, port)
    
    log(f"[*] 服务端已启动: {host}:{port} | 容忍度: {tolerance}s")
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
        log(f"[Client] [{idle_time:4}s] 已建连，等待服务端 PUSH ...")
        
        wait_timeout = idle_time + tolerance
        try:
            data = await asyncio.wait_for(reader.read(1024), timeout=wait_timeout)
            
            if data and data.decode(errors='ignore') == f"PUSH-{idle_time}":
                writer.write(f"ACK-{idle_time}".encode())
                await writer.drain()
                log(f"[Client] [{idle_time:4}s] [SUCCESS] 全双工连通")
                status = "SUCCESS"
            else:
                log(f"[Client] [{idle_time:4}s] [EOF] 服务端提前断开")
                status = "EOF"
                
        except asyncio.CancelledError:
            log(f"[Client] [{idle_time:4}s] [SKIP] 探测跳过 (收到打断指令)")
            status = "ABORTED"
            raise
            
    except asyncio.TimeoutError:
        log(f"[Client] [{idle_time:4}s] [TIMEOUT] ETIMEDOUT (疑似黑洞丢包)")
        status = "TIMEOUT"
    except ConnectionResetError:
        log(f"[Client] [{idle_time:4}s] [RESET] ECONNRESET (收到主动 RST)")
        status = "RESET"
    except OSError as e:
        log(f"[Client] [{idle_time:4}s] [ERROR] OS异常: {e}")
        status = "ERROR"
    except Exception as e:
        if isinstance(e, asyncio.CancelledError):
            return
        log(f"[Client] [{idle_time:4}s] [ERROR] 异常: {type(e).__name__}: {e}")
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
    consecutive_fails = 0
    is_aborted = False

    while True:
        item = await result_queue.get()
        
        if item is None:
            result_queue.task_done()
            break
            
        idle_time, status = item
        stats[status] += 1
        
        if status == "SUCCESS":
            consecutive_fails = 0
        elif status in ("TIMEOUT", "RESET", "EOF", "ERROR"):
            consecutive_fails += 1
            
        if consecutive_fails >= max_fails and not is_aborted:
            is_aborted = True
            log(f"\n[!] 连续 {max_fails} 次失败，触发熔断。Monitor 接管终止剩余任务...")
            for task in tasks:
                if not task.done():
                    task.cancel()
                    
        result_queue.task_done()
        
    print("\n" + "="*50)
    print(" 📊 探 测 结 果 统 计")
    print("="*50)
    print(f"  ✅ 成功 (SUCCESS) : {stats['SUCCESS']}")
    print(f"  🕳️  超时 (TIMEOUT) : {stats['TIMEOUT']}  <-- [静默丢包]")
    print(f"  ⛔ 重置 (RESET)   : {stats['RESET']}  <-- [主动阻断]")
    print(f"  🔌 断开 (EOF)     : {stats['EOF']}")
    print(f"  ❌ 异常 (ERROR)   : {stats['ERROR']}")
    print(f"  ⏭️  跳过 (ABORTED) : {stats['ABORTED']}")
    print("="*50)

async def run_client(host, port, start, step, count, tolerance, max_fails, stagger):
    log(f"[*] 目标: {host}:{port}")
    log(f"[*] 策略: {start}s起步, 步进{step}s, 共{count}次 | 连败阻断: {max_fails}次")
    
    times = [start + i * step for i in range(count)]
    
    result_queue = asyncio.Queue(maxsize=count)
    tasks = []
    
    watcher = asyncio.create_task(monitor_task(result_queue, tasks, max_fails))
    
    for i, t in enumerate(times):
        delay = i * stagger
        task = asyncio.create_task(probe_task(host, port, t, tolerance, delay, result_queue))
        tasks.append(task)
        
    await asyncio.gather(*tasks, return_exceptions=True)
    
    await result_queue.put(None)
    await watcher

# ==========================================
# 命令行入口
# ==========================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="云网络 TCP Idle Timeout 工业级排障工具")
    subparsers = parser.add_subparsers(dest="role", help="选择模式")
    
    # Server
    server_parser = subparsers.add_parser("server")
    server_parser.add_argument("--host", type=str, default="0.0.0.0")
    server_parser.add_argument("--port", type=int, default=9999)
    server_parser.add_argument("--tolerance", type=float, default=30.0, help="双端统一步调的网络容忍度(秒)")
    
    # Client
    client_parser = subparsers.add_parser("client")
    client_parser.add_argument("--host", type=str, required=True)
    client_parser.add_argument("--port", type=int, default=9999)
    client_parser.add_argument("--start", type=int, default=60, help="起始时间(秒)")
    client_parser.add_argument("--step", type=int, default=60, help="步进时间(秒)")
    client_parser.add_argument("--count", type=int, default=20, help="探测数量")
    client_parser.add_argument("--tolerance", type=float, default=30.0, help="网络容忍度(秒)")
    client_parser.add_argument("--max-fails", type=int, default=2, help="连续失败阻断阈值")
    client_parser.add_argument("--stagger", type=float, default=0.5, help="连接发起的错峰间隔(秒)")
    
    args = parser.parse_args()
    
    try:
        if args.role == "server":
            asyncio.run(run_server(args.host, args.port, args.tolerance))
        elif args.role == "client":
            asyncio.run(run_client(args.host, args.port, args.start, args.step, args.count, args.tolerance, args.max_fails, args.stagger))
        else:
            parser.print_help()
    except KeyboardInterrupt:
        print("\n[*] 用户终止程序")

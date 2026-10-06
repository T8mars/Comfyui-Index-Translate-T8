import os
import sys
import json
import time
import threading
from pathlib import Path
from process_lock import exclusive_lock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "_vendor"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("DO_NOT_TRACK", "1")
if __name__ == "__main__":
    from app.private_runtime import ensure_private_runtime
    ensure_private_runtime(__file__)
    import argparse
    import uvicorn
    from app.server import create_app
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8098)
    parser.add_argument("--instance", default=None)
    parser.add_argument("--offline", action="store_true", help="只允许本机连接，完整模型可离线推理")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        def local_connections_only(event, values):
            if event == "socket.connect":
                address = values[1]
                if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
                    raise OSError("离线模式不允许外部网络连接")
            if event == "socket.getaddrinfo" and values[0] not in ("127.0.0.1", "::1", "localhost", None):
                raise OSError("离线模式不允许外部域名解析")
        sys.addaudithook(local_connections_only)
    service_lock = exclusive_lock(ROOT / "data/service.lock", "本整合包已有服务运行，请使用它的启动或停止脚本。")
    service_lock.__enter__()
    app = create_app(port=args.port)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, access_log=False))
    record_path = ROOT / "data/service-process.json"
    if args.instance:
        import psutil
        from app.state import atomic_json
        atomic_json(record_path, {"pid": os.getpid(), "created": psutil.Process().create_time(), "instance": args.instance,
                                  "port": args.port, "identity": app.state.service.config["identity"]})
        stop_path = ROOT / "data" / ("stop-" + args.instance + ".json")
        def monitor():
            while not server.should_exit:
                if stop_path.exists():
                    app.state.service.quit.set()
                    with app.state.service.wake:
                        for event in app.state.service.cancels.values():
                            event.set()
                        app.state.service.wake.notify_all()
                    server.should_exit = True
                    break
                time.sleep(.3)
        threading.Thread(target=monitor, daemon=True).start()
    try:
        server.run()
    finally:
        if args.instance:
            stop_path.unlink(missing_ok=True)
            try:
                record = json.loads(record_path.read_text("utf-8"))
                if record.get("instance") == args.instance:
                    record_path.unlink()
            except FileNotFoundError:
                pass
        service_lock.__exit__(None, None, None)

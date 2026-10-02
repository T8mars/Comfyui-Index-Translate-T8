"""Standard-library launcher; manages only the process started by this package."""
import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from process_lock import exclusive_lock

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RECORD = DATA / "service-process.json"


def read_record():
    try:
        return json.loads(RECORD.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def owned(record):
    import psutil
    try:
        process = psutil.Process(record["pid"])
        expected = str(ROOT / "run.py")
        return (abs(process.create_time() - record["created"]) < .1 and
                Path(process.exe()).resolve() == Path(sys.executable).resolve() and
                expected in process.cmdline() and record["instance"] in process.cmdline())
    except (psutil.Error, KeyError, OSError):
        return False


def ready(record):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{record['port']}/health", timeout=2) as response:
            value = json.load(response)
        return value.get("status") == "service_ready" and value.get("identity") == record.get("identity")
    except (OSError, ValueError):
        return False


def start(port, browser):
    with exclusive_lock(DATA / "launcher.lock", "启动脚本正在运行，请稍后重试。"):
        _start(port, browser)


def _start(port, browser):
    DATA.mkdir(parents=True, exist_ok=True)
    record = read_record()
    if record and owned(record):
        if ready(record):
            print("服务已启动：", f"http://127.0.0.1:{record['port']}")
            if browser:
                webbrowser.open(f"http://127.0.0.1:{record['port']}")
            return
        raise RuntimeError("本整合包的服务仍在启动，请稍后重试；可用停止脚本退出它。")
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            raise RuntimeError(f"端口 {port} 已被占用。请使用 --port 指定其他端口。")
    instance = secrets.token_urlsafe(24)
    environment = dict(os.environ, PYTHONUTF8="1", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
    log = (DATA / "service.log").open("ab")
    subprocess.Popen([sys.executable, "-u", str(ROOT / "run.py"), "--port", str(port), "--instance", instance],
                     cwd=ROOT, env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    log.close()
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        record = read_record()
        if record and record.get("instance") == instance and ready(record):
            print("服务已启动：", f"http://127.0.0.1:{port}")
            if browser:
                webbrowser.open(f"http://127.0.0.1:{port}")
            return
        time.sleep(.4)
    raise RuntimeError("服务未能就绪，请查看 data/service.log；未停止任何其他程序。")


def stop():
    record = read_record()
    if not record or not owned(record):
        print("本整合包没有正在运行的服务。")
        return
    target = DATA / ("stop-" + record["instance"] + ".json")
    target.write_text(json.dumps({"instance": record["instance"]}), "utf-8")
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline and owned(record):
        time.sleep(.3)
    if owned(record):
        raise RuntimeError("服务正在结束模型加载或翻译。取消请求已提交，请稍后再次检查。")
    print("本整合包服务已停止。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["start", "stop", "status", "diagnose"])
    parser.add_argument("--port", type=int, default=8098)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "start":
            start(args.port, not args.no_browser)
        elif args.command == "stop":
            stop()
        elif args.command == "status":
            record = read_record()
            print("服务就绪" if record and owned(record) and ready(record) else "服务未就绪")
        else:
            sys.path.insert(0, str(ROOT / "_vendor"))
            from index_translate_core.inference import hardware
            print(json.dumps({"python": sys.executable, "root": str(ROOT), "hardware": hardware()}, ensure_ascii=False, indent=2))
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)

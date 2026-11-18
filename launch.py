"""Windows launcher for builds downloaded from the GitHub Pages share page."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parent
APP_FILES = ("app.py", "core.py", "reader.py", "requirements.txt")
VERSION = re.compile(r"[0-9a-f]{10}")
SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_PACKAGE = 8 * 1024 * 1024


def fetch(url: str, limit: int) -> bytes:
    request = Request(url, headers={"Cache-Control": "no-cache"})
    with urlopen(request, timeout=5) as response:
        if response.geturl() != url:
            raise ValueError("업데이트 주소가 변경됐습니다.")
        content = response.read(limit + 1)
    if len(content) > limit:
        raise ValueError("업데이트 파일이 너무 큽니다.")
    return content


def verified_release(base: str, cache: Path, current: str) -> tuple[Path | None, str]:
    url = urlsplit(base)
    if (
        url.scheme != "https"
        or not url.hostname
        or not url.hostname.endswith(".github.io")
        or url.netloc != url.hostname
        or url.query or url.fragment
        or not re.fullmatch(r"/[A-Za-z0-9._-]+", url.path)
        or url.path in ("/.", "/..")
    ):
        raise ValueError("업데이트 주소가 올바르지 않습니다.")
    manifest = json.loads(fetch(base + "/release.json", 16 * 1024))
    version = manifest["version"]
    checksum = manifest["sha256"]
    if not VERSION.fullmatch(version) or not SHA256.fullmatch(checksum):
        raise ValueError("버전 또는 무결성 정보가 올바르지 않습니다.")
    if version == current:
        return None, version
    payload = fetch(base + "/youtube-detail-windows.zip?v=" + version, MAX_PACKAGE)
    if hashlib.sha256(payload).hexdigest() != checksum:
        raise ValueError("업데이트 파일 확인에 실패했습니다.")
    cache.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=cache) as temporary:
        stage = Path(temporary) / "app"
        stage.mkdir()
        with ZipFile(io.BytesIO(payload)) as archive:
            for name in APP_FILES:
                member = archive.getinfo("youtube-detail/" + name)
                if member.file_size > MAX_PACKAGE:
                    raise ValueError("프로그램 파일이 너무 큽니다.")
                (stage / name).write_bytes(archive.read(member))
        destination = cache / version
        if not destination.exists():
            stage.rename(destination)
    if not all((destination / name).is_file() for name in APP_FILES):
        raise ValueError("업데이트 폴더가 완전하지 않습니다.")
    return destination, version


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path.parent) as temporary:
        replacement = Path(temporary) / path.name
        replacement.write_text(content, encoding="utf-8")
        os.replace(replacement, path)


def prepare_python(app: Path) -> Path:
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)
    requirements = app / "requirements.txt"
    marker = ROOT / ".venv" / "requirements.sha256"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    if not marker.exists() or marker.read_text(encoding="utf-8") != digest:
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(requirements)], check=True)
        atomic_write(marker, digest)
    return python


def run() -> int:
    config = json.loads((ROOT / "release-config.json").read_text(encoding="utf-8"))
    cache = ROOT / ".updates"
    pointer = cache / "current"
    option = cache / "enabled"
    if "--enable-updates" in sys.argv:
        atomic_write(option, "yes")
    if not option.exists() and config["base_url"]:
        answer = input("실행할 때 최신 공개 버전을 자동으로 받으시겠어요? [Y/n] ").strip().lower()
        atomic_write(option, "yes" if answer in ("", "y", "yes") else "no")
    current = pointer.read_text(encoding="utf-8").strip() if pointer.exists() else ""
    if VERSION.fullmatch(current) and all((cache / current / name).is_file() for name in APP_FILES):
        app = cache / current
    else:
        current, app = config["version"], ROOT
    python = None
    if config["base_url"] and option.exists() and option.read_text(encoding="utf-8") == "yes":
        try:
            updated, version = verified_release(config["base_url"], cache, current)
            if updated:
                print(f"새 프로그램 버전 {version}을 준비합니다.")
                python = prepare_python(updated)
                atomic_write(pointer, version)
                app = updated
        except (BadZipFile, KeyError, ValueError, OSError, TypeError, subprocess.CalledProcessError) as error:
            print(f"업데이트를 적용하지 못해 저장된 버전을 엽니다. ({type(error).__name__})")
    if python is None:
        python = prepare_python(app)
    env = dict(os.environ, YOUTUBE_DETAIL_DATA_DIR=str(ROOT / "data"))
    # Multiple installed copies can run independently with their own data folders.
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", 8517))
        except OSError:
            probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    address = f"http://127.0.0.1:{port}"
    print(f"브라우저에서 프로그램을 엽니다. 직접 열 주소는 {address} 입니다.", flush=True)
    args = [
        str(python), "-m", "streamlit", "run", str(app / "app.py"),
        "--server.address", "127.0.0.1", "--server.port", str(port), "--server.headless", "true",
        "--server.maxUploadSize", "1024", "--browser.gatherUsageStats", "false",
    ]
    with subprocess.Popen(args, cwd=ROOT, env=env) as server:
        try:
            while server.poll() is None:
                try:
                    with urlopen(address + "/_stcore/health", timeout=1) as health:
                        if health.read(16).strip() == b"ok":
                            atomic_write(cache / "port", str(port))
                            webbrowser.open(address)
                            break
                except OSError:
                    pass
                time.sleep(0.3)
            return server.wait()
        finally:
            (cache / "port").unlink(missing_ok=True)
            if server.poll() is None:
                server.terminate()


if __name__ == "__main__":
    import msvcrt

    lock_path = ROOT / ".updates" / "launch.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            try:
                port = int((ROOT / ".updates" / "port").read_text(encoding="utf-8"))
                if 0 < port < 65536:
                    webbrowser.open(f"http://127.0.0.1:{port}")
            except (OSError, ValueError):
                pass
            print("프로그램이 시작 중이거나 이미 실행 중입니다. 실행한 창에서 주소를 확인하세요.")
            raise SystemExit(0)
        raise SystemExit(run())

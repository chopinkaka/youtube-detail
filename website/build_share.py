"""Rebuild the share folder from a small allowlist of application files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

KIT = Path(__file__).resolve().parent
APP = KIT.parent
DIST = KIT / "dist"
APP_FILES = ("START.bat", "launch.py", "app.py", "core.py", "reader.py", "requirements.txt")
KOREA = timezone(timedelta(hours=9))
SAMPLE = """<section id="sample" class="sample">
    <div>
      <h2>예시 카드</h2>
      <p class="note">영상 정리 결과가 어떻게 보이는지 확인할 수 있습니다.</p>
    </div>
    <a href="cards.html?v=__CARDS_VERSION__">예시 카드 열기 →</a>
  </section>"""
GUIDE = """영상 깊이 읽기 · Windows

1. Python 3.11 이상을 설치합니다. https://www.python.org/downloads/windows/
2. 이 ZIP을 풀고 START.bat를 더블클릭합니다.
3. 영상 링크 또는 영상·음성 파일을 입력합니다.
4. 핵심 노트를 자동 생성하려면 본인의 Gemini API 키를 사용합니다.
   음성 인식은 PC에서 처리되며, 분석 버튼을 누르면 Gemini에 대본이 전달됩니다.

업데이트
처음 실행할 때 자동 업데이트를 켜면 이후 실행 시 최신 버전을 확인합니다.
이전 배포본을 쓰고 있다면 이 ZIP을 기존 youtube-detail 폴더에 한 번 덮어씁니다.
같은 폴더에서 실행하면 저장한 작업과 대본을 계속 열 수 있습니다.
"""


def make_release(cards: Path | None, base_url: str = "") -> dict:
    sources = {name: (APP / name).read_bytes() for name in APP_FILES}
    sources["사용법.txt"] = GUIDE.encode("utf-8-sig")
    digest = hashlib.sha256()
    for name, content in sources.items():
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
    version = digest.hexdigest()[:10]
    today = datetime.now(KOREA).date().isoformat()

    DIST.mkdir(exist_ok=True)
    manifest = DIST / "release.json"
    old = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    released = old["date"] if old.get("version") == version else today

    cards_path = DIST / "cards.html"
    cards_bytes = cards.read_bytes() if cards else cards_path.read_bytes() if cards_path.exists() else None
    if cards_bytes is not None:
        cards_text = cards_bytes.decode("utf-8-sig")
        if 'class="slide"' not in cards_text or "<!doctype html>" not in cards_text.lower():
            raise ValueError("앱에서 저장한 ‘카드 저장 (.html)’ 파일을 선택하세요.")

    page = (KIT / "page-template.html").read_text(encoding="utf-8-sig")
    web_script = (KIT / "web-app.mjs").read_bytes()
    page = page.replace("__WEB_VERSION__", hashlib.sha256(web_script).hexdigest()[:10])
    if cards_bytes is None:
        page = page.replace("__SAMPLE__", "")
    else:
        cards_version = hashlib.sha256(cards_bytes).hexdigest()[:10]
        page = page.replace("__SAMPLE__", SAMPLE.replace("__CARDS_VERSION__", cards_version))

    page = page.replace("__APP_VERSION__", version).replace("__APP_DATE__", released)
    info = {"version": version, "date": released, "cards": cards_bytes is not None}
    sources["release-config.json"] = json.dumps({"version": version, "base_url": base_url.rstrip("/")}).encode("utf-8")
    with TemporaryDirectory(dir=KIT) as directory:
        staging = Path(directory)
        with ZipFile(staging / "youtube-detail-windows.zip", "w", ZIP_DEFLATED) as archive:
            for name, content in sources.items():
                archive.writestr("youtube-detail/" + name, content)
        info['sha256'] = hashlib.sha256((staging / 'youtube-detail-windows.zip').read_bytes()).hexdigest()
        if cards_bytes is not None:
            (staging / "cards.html").write_bytes(cards_bytes)
        (staging / "web-app.mjs").write_bytes(web_script)
        (staging / "index.html").write_text(page, encoding="utf-8")
        (staging / "release.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
        for name in ("youtube-detail-windows.zip", "cards.html", "web-app.mjs", "release.json", "index.html"):
            file = staging / name
            if file.exists():
                os.replace(file, DIST / name)
    return info


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="친구에게 보여줄 최신 프로그램과 카드 파일을 묶습니다.")
    parser.add_argument("--cards", type=Path, help="공유할 카드 HTML. 생략하면 기존 공개용 카드를 유지합니다.")
    parser.add_argument("--base-url", default="")
    args = parser.parse_args()
    info = make_release(args.cards, args.base_url)
    print(f"공유 파일 준비 완료: {DIST}")
    print(f"프로그램 버전: {info['version']} · {info['date']}")

# 영상 깊이 읽기 공유 페이지

공유 페이지에서 Windows용 최신 ZIP을 받을 수 있고, 공개용 카드는 브라우저에서 바로 읽을 수 있습니다.

## 처음 한 번

1. 배포용 파일을 GitHub 저장소의 `main`에 올립니다. 무료 GitHub Pages를 사용할 경우 공개 저장소를 선택합니다. 공개 범위에는 앱 소스, 페이지 코드, 예시 카드가 포함됩니다.
2. 저장소의 **Settings → Pages → Source**에서 **GitHub Actions**를 선택합니다.
3. **Actions → Publish the share page → Run workflow**를 `main`에서 한 번 실행합니다. 배포가 성공하면 Pages에 표시되는 사이트 주소를 공유합니다.

[GitHub Pages 공식 설정 안내](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site#publishing-with-a-custom-github-actions-workflow)

## 이후 업데이트

- 프로그램 수정은 앱 파일 변경을 `main`에 반영합니다.
- 공개 카드를 바꿀 때는 검토한 카드 HTML로 `website/cards.html`을 교체하고 `main`에 반영합니다.
- GitHub가 페이지와 Windows ZIP을 빌드해 기존 주소에 자동 배포합니다. 배포 결과는 Actions에서 확인합니다.
- 이전 설치본에는 최신 ZIP을 한 번 덮어쓰면 됩니다. 첫 실행에서 자동 업데이트를 켜면 이후 실행 시 최신 버전을 적용하며 저장 작업은 같은 폴더에 보존됩니다.

카드에 포함되는 내용은 공개 전에 확인합니다. 작업 데이터, 원음, 전체 대본, API 키와 가상환경은 저장소 추가 대상에서 제외합니다.

# Render 배포

1. 프로젝트 소스 파일을 비공개 GitHub 저장소에 올립니다. 기존 Run을 함께 옮길 경우에만 `steelchallenge.sqlite3`도 포함하세요. 이 파일에는 조업 기록과 OCR 텍스트가 들어 있습니다.
2. [Render Dashboard](https://dashboard.render.com/)에서 **New → Blueprint**를 선택해 저장소를 연결합니다. 승인 전에 화면에 표시되는 유료 서비스 및 디스크 비용을 확인하세요. `STEEL_ALLOWED_EMAILS`에는 접속을 허용할 Google 이메일을 쉼표로 구분해 입력합니다.
3. 서비스의 HTTPS 주소가 정해지면 Google Cloud에서 웹 애플리케이션용 OAuth 클라이언트를 만들고 승인된 리디렉션 URI로 `https://서비스.onrender.com/oauth2callback`을 등록합니다.
4. Render 서비스의 **Environment → Secret Files**에 이름이 `secrets.toml`인 파일을 추가합니다. 아래 자리에는 해당 서비스의 값과 무작위 비밀 키를 넣으세요.

```toml
[auth]
redirect_uri = "https://서비스.onrender.com/oauth2callback"
cookie_secret = "무작위로 생성한 비밀 키"
client_id = "Google OAuth Client ID"
client_secret = "Google OAuth Client Secret"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```

비밀 키 생성 명령은 `python -c "import secrets; print(secrets.token_urlsafe(48))"`입니다. 비밀 파일은 Render에만 입력합니다.

5. 재배포 후 시크릿 창에서 HTTPS 주소를 열어 로그인 전 접근 제한을 확인합니다. 허용 계정으로 들어가 Run 건수를 확인하고, 테스트 기록을 추가한 후 서비스 재시작 뒤에도 남는지 확인합니다.

첫 시작에만 저장소의 DB를 영구 디스크로 복사합니다. 이후 웹에서 한 변경은 영구 디스크에 쌓이며 로컬 DB와는 별도로 관리됩니다.

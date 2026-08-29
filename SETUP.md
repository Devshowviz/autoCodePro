# autoCodePro 설치 가이드

업비트 자동매매 Django 웹 애플리케이션의 전체 설치 가이드입니다.
Python 설치부터 서버 실행까지 순서대로 따라가면 됩니다.

---

## ⚠️ 시작하기 전에 — API 키 보안

이 저장소의 과거 커밋 히스토리에 `.env` 파일이 실제 업비트 API 키와 함께
포함되어 있었고, 저장소는 공개(public) 상태입니다.
파일은 이후 삭제되었지만 **git 히스토리에는 그대로 남아 있습니다.**

**해당 키는 유출된 것으로 간주하고, 업비트에서 삭제 후 새로 발급하세요.**
새 키를 발급받기 전에는 이 프로그램을 실행하지 마세요.

---

## 0. 사전 요구사항 한눈에 보기

| 항목 | 요구 버전 / 비고 |
|---|---|
| Python | **3.10 ~ 3.14** (Django 5.2 지원 범위) |
| pip | Python에 기본 포함 |
| Git | 소스 내려받기용 |
| 업비트 계정 | Open API 키 발급 + 접속 IP 등록 필요 |
| 데이터베이스 | SQLite (별도 설치 불필요) |

> 프로젝트의 `.idea` 설정에는 Python 3.9로 되어 있으나, Django 5.2는 3.10 미만을
> 지원하지 않습니다. **3.10 이상**을 사용하세요.
> 이미 3.13 / 3.14 가 설치되어 있다면 그대로 쓰면 됩니다 — 다운그레이드할 필요
> 없습니다. `requirements.txt` 의 5개 패키지 모두 3.14까지 공식 지원합니다.
> (이 가이드는 Python 3.11 + Django 5.2.17 환경에서 실행 검증했습니다.)

---

## 1단계 — Python 설치

### Windows

1. https://www.python.org/downloads/windows/ 에서 설치 파일 다운로드 (3.10~3.14 중 아무 버전)
2. 설치 실행 시 **`Add python.exe to PATH` 체크박스를 반드시 켜세요** (가장 흔한 실수)
3. `Install Now` 클릭
4. 설치 확인 — PowerShell을 새로 열고:

```powershell
python --version
pip --version
```

`Python 3.14.x` 처럼 나오면 성공입니다.

> 버전 확인은 대문자 `-V` 또는 `--version` 입니다.
> 소문자 `python -v` 는 import 추적(verbose) 모드로 대화형 셸에 진입합니다.
> 그 상태(`>>>` 프롬프트)에서 빠져나오려면 `exit()` 를 입력하세요.

### macOS

Homebrew 사용 (권장):

```bash
brew install python@3.12
python3 --version
```

Homebrew가 없다면 먼저 설치:
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### Linux (Ubuntu / Debian)

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv git
python3 --version
```

> **참고:** 이 문서의 명령어에서 Windows는 `python`, macOS/Linux는 `python3` 를
> 사용합니다. 가상환경을 활성화한 뒤에는 모든 OS에서 `python` 으로 통일됩니다.

---

## 2단계 — 소스 내려받기

```bash
git clone https://github.com/Devshowviz/autoCodePro.git
cd autoCodePro
```

내려받은 디렉터리 구조:

```
autoCodePro/
├── requirements.txt          ← 파이썬 패키지 목록
├── SETUP.md                  ← 이 문서
└── autoCodeProWeb/           ← Django 프로젝트 루트
    ├── manage.py
    ├── .env.example          ← 환경변수 템플릿
    ├── .gitignore
    ├── autoCodeProWeb/       ← 설정 패키지
    │   ├── settings.py
    │   └── urls.py
    └── trading/              ← 자동매매 앱
        ├── views.py
        ├── utils.py          ← 업비트 API 호출
        ├── auto_trade.py     ← 자동매매 로직
        └── templates/
            └── main.html     ← 대시보드 UI
```

---

## 3단계 — 가상환경 만들기

프로젝트 전용으로 패키지를 격리합니다. `autoCodePro` 디렉터리 안에서:

```bash
# 생성 (macOS/Linux)
python3 -m venv venv

# 생성 (Windows)
python -m venv venv
```

활성화:

```bash
# macOS / Linux
source venv/bin/activate

# Windows (PowerShell)
.\venv\Scripts\Activate.ps1

# Windows (cmd)
venv\Scripts\activate.bat
```

프롬프트 앞에 `(venv)` 가 붙으면 성공입니다.

> **PowerShell 실행 정책 오류가 나면:**
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
> ```
> 를 한 번 실행한 뒤 다시 활성화하세요.

> 작업이 끝나면 `deactivate` 로 빠져나옵니다.
> 새 터미널을 열 때마다 활성화를 다시 해야 합니다.

---

## 4단계 — 패키지 설치

가상환경이 활성화된 상태에서:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

설치되는 패키지:

| 패키지 | 역할 |
|---|---|
| `Django` | 웹 프레임워크 |
| `djangorestframework` | API 시리얼라이저 |
| `django-environ` | `.env` 파일 로딩 |
| `PyJWT` | 업비트 API 인증 토큰 생성 |
| `requests` | 업비트 REST API 호출 |

확인:
```bash
pip list
```

---

## 5단계 — 업비트 API 키 발급

1. https://upbit.com/mypage/open_api_management 접속
2. 필요한 권한 체크:
   - **자산 조회** — 계좌 잔고 조회용 (`/v1/accounts`)
   - **주문하기** — 자동매매 매수/매도용 (`/v1/orders`)
3. **접속 허용 IP 주소를 등록** — 업비트는 IP 화이트리스트가 **필수**입니다.
   등록하지 않으면 계좌 조회부터 실패합니다.
   - 로컬 PC에서 실행한다면 본인의 공인 IP (검색창에 "내 아이피" 입력)
   - 서버에서 실행한다면 그 서버의 공인 IP
   - 공유기/모바일 환경은 IP가 바뀔 수 있으니 바뀔 때마다 재등록해야 합니다
4. 발급된 **Access key** 와 **Secret key** 를 복사
   — **Secret key는 발급 시점에 딱 한 번만 표시됩니다.**

---

## 6단계 — 환경변수(.env) 설정

`.env` 파일은 `manage.py` 와 **같은 폴더**에 있어야 합니다
(`settings.py` 가 `BASE_DIR/.env` 를 읽습니다).

```bash
cd autoCodeProWeb
cp .env.example .env      # Windows: copy .env.example .env
```

`SECRET_KEY` 생성:

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

`.env` 를 편집기로 열어 세 값을 모두 채웁니다:

```ini
SECRET_KEY=위에서_생성한_문자열
UPBIT_API_KEY=업비트_Access_key
UPBIT_SECRET_KEY=업비트_Secret_key
```

> **주의:** `SECRET_KEY` 를 빠뜨리면 서버가 아예 뜨지 않습니다
> (`ImproperlyConfigured: Set the SECRET_KEY environment variable`).
> 세 개 모두 필수입니다.

> 값에 따옴표를 붙이지 마세요. `SECRET_KEY=abc123` 형태가 맞습니다.

---

## 7단계 — 데이터베이스 초기화

이 프로젝트는 자체 모델이 없지만, Django 기본 앱(admin, auth, sessions)의
테이블 생성을 위해 마이그레이션이 필요합니다. SQLite를 쓰므로 DB 서버 설치는
필요 없습니다.

```bash
# autoCodeProWeb/ 디렉터리 (manage.py 가 있는 곳)에서
python manage.py migrate
```

`db.sqlite3` 파일이 생성됩니다.

관리자 페이지(`/admin/`)를 쓰려면 계정도 만들어 둡니다 (선택):

```bash
python manage.py createsuperuser
```

---

## 8단계 — 설정 점검

실행 전에 설정 오류를 미리 확인합니다:

```bash
python manage.py check
```

`System check identified no issues` 가 나오면 정상입니다.

문제가 생기면 [문제 해결](#문제-해결) 을 참고하세요.

---

## 9단계 — 서버 실행

```bash
python manage.py runserver
```

브라우저에서 **http://127.0.0.1:8000** 접속.

### 화면 구성

| 영역 | 내용 |
|---|---|
| 전체 계좌 조회 | 화폐 / 보유 수량 / 평균 매수 단가 / 평가 금액 |
| KRW 마켓 상위 5개 코인 | 현재가 / 전일 대비 / 24시간 거래대금 |
| 자동매매 컨트롤 | 매수 금액 입력 + 시작 / 정지 버튼 |
| 자동매매 로그 | 실시간 로그 (최근 50건) |

### 제공 엔드포인트

| URL | 설명 |
|---|---|
| `/` | 대시보드 메인 |
| `/api/account_data/` | 계좌 정보 JSON |
| `/api/coin_data/` | 상위 5개 코인 JSON |
| `/api/trade_logs/` | 자동매매 로그 JSON |
| `/auto_trade/start/?budget=10000` | 자동매매 시작 |
| `/auto_trade/stop/` | 자동매매 정지 |
| `/admin/` | Django 관리자 |

서버 종료는 `Ctrl + C` 입니다.

---

## 자동매매 동작 확인 및 주의사항

### ⚠️ 실계좌 주문 전 반드시 소액으로 검증하세요

주문 API 경로는 **실제 업비트 키로는 검증되지 않았습니다.** 개발 환경에서
`api.upbit.com` 접근이 차단되어 있어, 요청이 규격대로 만들어지는지까지만
단위 테스트로 확인했습니다. 처음 실행할 때는 **최소 주문 금액(5,000원)**
으로 매수·매도가 실제로 체결되는지 확인한 뒤 금액을 올리세요.

### 테스트 실행

```bash
# autoCodeProWeb/ 디렉터리에서
python manage.py test trading
```

RSI 계산, 주문 파라미터 구성, 인증 헤더, 매도 판단, 일일 손실 한도, 뷰 응답에
대한 42개 테스트가 있습니다.

### 매매 전략

| 단계 | 조건 |
|---|---|
| **진입** | 거래대금 상위 5개 코인 중 RSI(14, 1분봉) ≤ 30 인 첫 종목을 시장가 매수 |
| **익절** | 매수가 대비 **+2%** |
| **손절** | 매수가 대비 **-2%** |
| **일일 정지** | 당일 누적 실현손익이 매매원금 대비 **-10%** 도달 시 보유분 청산 후 정지 |

일일 손실 한도는 **KST(한국시간) 자정 기준**으로 초기화됩니다. 정지된 뒤
같은 날 다시 매매하려면 서버를 재시작하거나 날짜가 바뀌어야 합니다.

> **수수료를 감안하면 승률 52.5% 이상이어야 본전입니다.**
> 익절 +2% − 왕복 수수료 0.1% = +1.9%, 손절 -2% − 0.1% = -2.1% 이므로
> 손익분기 승률은 `1.9p = 2.1(1-p)` → `p ≈ 0.525` 입니다.
> 익절 폭을 손절보다 크게 두면(예: +3% / -2%) 구조적으로 유리해집니다.

### 매매 전략 값 조정

`trading/auto_trade.py` 상단 상수를 고치면 전략이 바뀝니다.

| 상수 | 기본값 | 의미 |
|---|---|---|
| `TRADE_INTERVAL_SECONDS` | `3` | 매매 루프 주기(초) |
| `RSI_PERIOD` | `14` | RSI 계산 기간 |
| `RSI_CANDLE_UNIT` | `1` | 분봉 단위 (1분봉) |
| `RSI_BUY_THRESHOLD` | `30` | 이 값 이하면 매수 |
| `TAKE_PROFIT_RATE` | `0.02` | 매수가 대비 +2% 익절 |
| `STOP_LOSS_RATE` | `-0.02` | 매수가 대비 -2% 손절 |
| `DAILY_LOSS_CUT_RATE` | `-0.10` | 당일 누적 실현손익 한도 (매매원금 대비) |
| `FEE_RATE` | `0.0005` | 업비트 원화마켓 수수료(편도) |

> `TRADE_INTERVAL_SECONDS` 를 1초로 줄이면 업비트 시세 API 제한(초당 10회)에
> 걸릴 수 있습니다. 한 번 순회할 때 시세 조회 2회 + 후보 코인당 캔들 조회
> 1회(최대 5회)가 나갑니다.

### 남아 있는 제약

- 매매 상태(`trade_logs`, 전역 `trader`)를 프로세스 메모리에 두므로
  서버를 재시작하면 진행 중이던 매매 정보가 사라집니다. 보유 코인은
  업비트 계좌에 그대로 남으니 직접 확인해야 합니다.
- 워커를 여러 개 띄우면 각 워커가 독립적으로 매매하게 되어 위험합니다.
  단일 워커로만 운영하세요.
- 매수 체결가가 아니라 **주문 시점의 시세**를 매수가로 기록합니다.
  시장가 주문이라 실제 체결가와 차이가 날 수 있고, 실현손익도 그만큼
  근사치입니다.
- **일일 누적 손익도 메모리에만 있습니다.** 로스컷으로 정지된 뒤 서버를
  재시작하면 누적 손익이 0으로 초기화되어 그날 안에 다시 매매가 시작됩니다.
  의도한 동작이 아니라면 재시작하지 마세요.

---

## 문제 해결

**`ImproperlyConfigured: Set the SECRET_KEY environment variable`**
`.env` 에 `SECRET_KEY` 가 없거나, `.env` 가 `manage.py` 와 다른 폴더에 있습니다.
`autoCodeProWeb/.env` 위치와 세 개 키가 모두 채워졌는지 확인하세요.

**`ModuleNotFoundError: No module named 'django'`**
가상환경이 활성화되지 않았습니다. 프롬프트에 `(venv)` 가 있는지 확인하고,
없으면 3단계의 활성화 명령을 다시 실행하세요.

**`ModuleNotFoundError: No module named 'autoCodeProWeb'`**
`manage.py` 가 있는 `autoCodeProWeb/` 디렉터리에서 명령을 실행해야 합니다.

**계좌 조회에 `{"error": ...}` 가 표시됨**
업비트 API 키 문제입니다. 순서대로 확인하세요:
1. 접속 허용 IP가 현재 IP와 일치하는지 (가장 흔한 원인)
2. 키에 "자산 조회" 권한이 있는지
3. `.env` 의 키 값에 공백이나 따옴표가 섞이지 않았는지
4. 키가 만료되지 않았는지 (업비트 API 키는 유효기간이 있습니다)

**`DisallowedHost` 오류**
`settings.py` 의 `ALLOWED_HOSTS = []` 는 `DEBUG=True` 일 때 `127.0.0.1`,
`localhost` 만 허용합니다. 다른 호스트명이나 외부 IP로 접속하려면
`ALLOWED_HOSTS` 에 해당 호스트를 추가해야 합니다.

**Python 버전이 3.10 미만**
`pip install -r requirements.txt` 단계에서 Django 설치가 실패합니다
(`Django requires Python >=3.10`). 3.10 이상을 설치한 뒤 가상환경을 새로
만드세요 (`venv` 폴더 삭제 후 3단계부터).

---

## 운영 배포 시 추가 작업

현재 설정은 **로컬 개발 전용**입니다. 외부에 공개하려면 최소한 다음이 필요합니다.

- `settings.py:39` 의 `DEBUG = True` → `False` (환경변수로 분리 권장)
- `ALLOWED_HOSTS` 에 실제 도메인 등록
- `runserver` 대신 Gunicorn / uWSGI + Nginx 사용
- `python manage.py collectstatic` 및 정적 파일 서빙 설정
- SQLite → PostgreSQL 등으로 교체 검토
- 자동매매 로직이 프로세스 메모리(`trade_logs`, 전역 `trader`)에 상태를 두므로,
  워커를 여러 개 띄우면 정상 동작하지 않습니다. 단일 워커로 운영하거나
  상태 저장 방식을 먼저 변경해야 합니다.

---

## 빠른 설치 요약

```bash
git clone https://github.com/Devshowviz/autoCodePro.git
cd autoCodePro

python3 -m venv venv
source venv/bin/activate            # Windows: .\venv\Scripts\Activate.ps1

pip install --upgrade pip
pip install -r requirements.txt

cd autoCodeProWeb
cp .env.example .env                # Windows: copy .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(50))"
# → 출력값과 업비트 키 2개를 .env 에 채워 넣기

python manage.py migrate
python manage.py check
python manage.py runserver
# → http://127.0.0.1:8000
```

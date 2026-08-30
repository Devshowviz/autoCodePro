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
    └── trading/                  ← 자동매매 앱
        ├── models.py             ← 거래 기록 등 DB 모델
        ├── views.py              ← 웹 엔드포인트
        ├── utils.py              ← 업비트 API 호출
        ├── indicators.py         ← 기술적 지표 (RSI/MACD/볼린저 등)
        ├── market_analysis.py    ← 매수 종목 선정 · 시장 강도 분석
        ├── auto_trade.py         ← 자동매매 엔진
        ├── tests.py
        └── templates/
            └── main.html         ← 대시보드 UI
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
| `pandas` | 기술적 지표 계산 |

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

거래 기록·주문 실패 종목·시장 거래량·매도 기록 테이블을 만듭니다. SQLite를
쓰므로 DB 서버 설치는 필요 없습니다.

| 테이블 | 용도 |
|---|---|
| `TradeRecord` | 매수한 거래. 재시작 시 보유 종목 복원에 사용 |
| `DailyPnlRecord` | 당일 누적 실현손익. 재시작해도 일일 손실 한도 유지 |
| `FailedMarket` | 주문 실패 종목. 이후 매수 대상에서 제외 |
| `MarketVolumeRecord` | 24시간 주기 시장 거래량 스냅샷 |
| `AskRecord` | 매도 기록. 10분간 같은 종목 재매수 차단 |

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
| `/auto_trade/start/?budget=N` | 자동매매 시작 (N원 예산) |
| `/auto_trade/stop/` | 자동매매 정지 |
| `/api/fetch_account_data/` | 계좌 정보 |
| `/api/fetch_coin_data/` | 상위 코인 시세 |
| `/api/trade_logs/` | 자동매매 로그 (최근 50건) |
| `/api/check_auto_trading/` | 실행 여부 · 보유 종목 · 당일 손익 |
| `/api/get_market_volume/` | 시장 상태 (상승/하락/보합) |
| `/api/getRecntTradeLog/` | 최근 매도 체결 내역 |
| `/api/recentProfitLog/` | 최근 수익 로그 |
| `/admin/` | Django 관리자 (거래 기록 조회) |

> 이전 경로 `/api/account_data/`, `/api/coin_data/` 도 그대로 동작합니다.

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

기술적 지표 6종, 매수 종목 선정 3단계, 시장 강도 분석, 주문 파라미터·인증
헤더, 포지션 관리, 매도 전략, 일일 손실 한도, 웹 엔드포인트, 장애 상황
안전장치에 대한 **105개** 테스트가 있습니다.

### 매매 전략

**매수 — 3단계 선정**

| 단계 | 조건 |
|---|---|
| 1차 | 전일 대비 상승 종목 중 상승률 **상위 10개** |
| 2차 | 호가 분석 — 매수 총잔량 > 매도 총잔량 × **1.5**, 스프레드 **0.1% 미만** |
| 3차 | 통과 종목 중 거래대금 상위 5개 → `현재가 × 거래대금` 최대 종목 |

보유 중 / 주문 실패 이력 / 최근 10분 내 매도한 종목은 제외됩니다.
동시 보유는 **최대 3종목**, KRW 잔고가 10,000원 미만이면 매수하지 않습니다.

**매도 — 우선순위 순으로 평가**

| 순위 | 조건 | 동작 |
|---|---|---|
| 1 | 매수가 대비 **-2%** (고변동성 종목은 **-4%**) | 손절 |
| 2 | **+2% 도달 이후**, 최고점 대비 **-1%** 하락 | 트레일링 스탑 매도 |
| 3 | **+1%** 이면서 보합장·하락장 | 즉시 익절 |
| 4 | 상승장 5분 / 그 외 10분 경과 + **+1%** | 시간 기반 매도 |
| — | 당일 누적 실현손익이 매매원금 대비 **-10%** | 전량 청산 후 정지 |

고변동성 판정은 전일 대비 변동률 절댓값이 **5% 이상**인 종목입니다.
상승장에서는 +1% 에 팔지 않고 트레일링 스탑으로 더 끌고 갑니다.

**시장 강도 판정** — 아래 3개 지표 중 2개 이상이 같은 방향이면 그 방향, 아니면 보합장

| 지표 | 상승장 | 하락장 |
|---|---|---|
| BTC/ETH 평균 변동률 | > +2% | < -2% |
| 전체 시장 거래량 변화 (24시간 전 대비) | > +20% | < -20% |
| 상승/하락 코인 비율 | 상승 > 60% | 하락 > 60% |

일일 손실 한도는 **KST(한국시간) 자정 기준**으로 초기화됩니다.

> **수수료를 감안하면 승률이 중요합니다.**
> 업비트 원화마켓 수수료는 편도 0.05%, 왕복 0.1% 입니다. 실현손익은
> 실질 매수가 `매수가 × 1.0005`, 실질 매도가 `매도가 × 0.9995` 로 계산합니다.

### 매매 전략 값 조정

`trading/auto_trade.py` 와 `trading/market_analysis.py` 상단 상수를 고치면 전략이 바뀝니다.

**auto_trade.py**

| 상수 | 기본값 | 의미 |
|---|---|---|
| `TRADE_INTERVAL_SECONDS` | `1` | 매매 루프 주기(초) |
| `MAX_POSITIONS` | `3` | 동시 보유 종목 수 상한 |
| `MIN_KRW_BALANCE` | `10000` | 이 금액 미만이면 매수 중단 |
| `REBUY_BLOCK_SECONDS` | `600` | 매도 후 재매수 차단 시간 |
| `QUICK_PROFIT_RATE` | `0.01` | 보합·하락장 즉시 익절 |
| `TRAILING_ACTIVATE_RATE` | `0.02` | 트레일링 스탑 활성화 |
| `TRAILING_DROP_RATE` | `0.01` | 최고점 대비 하락 매도 |
| `STOP_LOSS_RATE` | `-0.02` | 일반 손절 |
| `VOLATILE_STOP_LOSS_RATE` | `-0.04` | 고변동성 손절 |
| `HOLD_SECONDS_BULLISH` | `360` | 상승장 보유 시간 |
| `HOLD_SECONDS_OTHERWISE` | `600` | 그 외 보유 시간 |
| `DAILY_LOSS_CUT_RATE` | `-0.10` | 일일 손실 한도 |
| `FEE_RATE` | `0.0005` | 업비트 수수료(편도) |

**market_analysis.py**

| 상수 | 기본값 | 의미 |
|---|---|---|
| `RISING_CANDIDATE_COUNT` | `10` | 1차 상승률 상위 N개 |
| `BID_ASK_RATIO` | `1.5` | 매수세 우위 배수 |
| `MAX_SPREAD_RATE` | `0.001` | 스프레드 상한 |
| `BENCHMARK_RATE` | `0.02` | BTC/ETH 변동률 임계값 |
| `VOLUME_CHANGE_RATE` | `0.20` | 거래량 변동률 임계값 |
| `UP_DOWN_RATIO` | `0.60` | 상승/하락 코인 비율 임계값 |

### 남아 있는 제약

- 매수 체결가가 아니라 **주문 시점의 시세**를 매수가로 기록합니다. 시장가
  주문이라 실제 체결가와 차이가 날 수 있고, 실현손익도 그만큼 근사치입니다.
- 당일 누적 손익은 `DailyPnlRecord` 에 기록되어 **서버를 재시작하거나 시작
  버튼을 다시 눌러도 일일 손실 한도가 유지**됩니다. 한도 초과 상태에서
  시작을 요청하면 `loss_cut` 응답과 함께 거부되며, KST 자정에 풀립니다.
- 워커를 여러 개 띄우면 각 워커가 독립적으로 매매하게 되어 위험합니다.
  단일 워커로만 운영하세요.
- `FailedMarket` 에 쌓인 종목은 **1시간 동안** 매수 대상에서 제외된 뒤 자동으로
  풀립니다. 네트워크 오류 같은 일시적 실패는 아예 기록하지 않습니다.

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

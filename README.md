# 바이낸스 USDT-M 무기한 선물 페이퍼 트레이딩 봇 (롱/숏)

실시간 시세는 **바이낸스 공개 API로 진짜로** 받되, 주문은 **가상 지갑(Paper Wallet)에서만 체결**하는 모의투자 자동매매 봇. 실제 돈을 넣지 않고 롱/숏 양방향, 레버리지, 강제청산까지 시뮬레이션하며 전략을 검증·운영한다.

> ⚠️ **이 봇은 수익을 보장하지 않으며 투자 권유가 아니다.** 레버리지 선물은 손실이 증폭되고 청산 위험이 크다. 백테스트가 좋아도 실시간/실거래에서 무너질 수 있다(과최적화·슬리피지·시장변화). 실거래(`live`) 모드는 잠겨 있다.

---

## 핵심 특징

- **거래소**: 바이낸스 USDT-M 무기한 선물 (시세 공개 API, 인증 불필요)
- **롱/숏 양방향** + 레버리지(기본 3x / 최대 10x) + **격리마진 강제청산 모델링**
- **전략 플러그인** 3종: 변동성 돌파 · MA 교차 · RSI 역추세 (롱/숏 대칭)
- **백테스트 = 실시간 동일 체결 엔진**(`ExecutionEngine`) → 결과 일관성 보장
- **그리드서치 최적화**: (전략×심볼×파라미터×레버리지) 인샘플/아웃샘플 분리 + 과최적화 경고
- **Slack 알림**(진입/청산/손절/청산/일일요약) + **FastAPI 실시간 대시보드**
- **안전장치**: `mode: paper` 기본, `live` 호출 시 즉시 차단. 상태 영속화(SQLite)로 재시작 복원

> **기본 전략 성과** (MA 교차 5/15 · 일봉 · 3x · 진입비중 20% · 4심볼 동일가중):
> `python backtest.py` 기준 **최근 365일 +17.5%**, **730일(2년) +13.2%** (둘 다 3/4·2/4 심볼 흑자).
> ⚠️ 과거 데이터 결과이며 미래 수익을 보장하지 않는다. 종목별 편차가 크다(예: SOL은 두 기간 모두 손실).

---

## 설치

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # 슬랙 쓸 경우 토큰/채널 입력 (안 써도 동작)
```

요구사항: Python 3.11+ (3.14 검증 완료).

---

## 설정 (`config.yaml`)

코드 수정 없이 모든 파라미터를 조정한다. 주요 항목:

| 섹션 | 키 | 설명 |
|---|---|---|
| `mode` | | `paper` (기본). `live`는 차단됨 |
| `symbols` | | 거래 심볼 (`BTCUSDT` 등) |
| `account` | `initial_balance_usdt` | 시작 가상자본 (기본 1000 USDT) |
| | `taker_fee_rate` | 수수료 (기본 0.05%) |
| | `maintenance_margin_rate` | 유지증거금률 (청산가 계산) |
| `leverage` | `default` / `max` | 기본/최대 레버리지 |
| `strategy` | | 실시간에 쓸 전략명 |
| `risk` | `position_size_pct` 등 | 진입 비중, 손절/익절, 일일한도, 롱/숏 허용 |
| `slack` | `enabled` / `events` | 알림 on/off, 이벤트별 토글 |
| `dashboard` | `host` / `port` | 대시보드 주소 |

시크릿(슬랙 토큰·채널)은 `config.yaml`이 아니라 **`.env`**에만 둔다.

---

## 사용법

### 1. 백테스트 (전략 검증 — 먼저 권장)
```bash
python backtest.py                          # config 전체 심볼·전략
python backtest.py --symbol BTCUSDT --days 90 --interval 4h --strategy ma_crossover
```

### 2. 파라미터 최적화 (그리드서치)
```bash
python optimize.py --interval 1d --days 365
```
(전략×심볼×파라미터×레버리지)를 70/30 인/아웃샘플로 평가해 랭킹과 추천 config를 출력하고 **과최적화 경고**를 함께 노출한다.

### 3. 실시간 페이퍼 트레이딩
```bash
python run.py
```
`Ctrl+C`로 안전 종료(상태 저장). 재시작 시 직전 잔고/포지션을 복원한다.

### 4. 웹 대시보드 (별도 터미널)
```bash
uvicorn dashboard.app:app --host 127.0.0.1 --port 8000
# 브라우저: http://localhost:8000
```
평가자산·자산곡선·보유 포지션(롱/숏·레버리지·청산가·미실현손익)·거래내역을 5초마다 갱신.

### 5. CLI/HTML 리포트
```bash
python report.py --format text
python report.py --format html --output reports/report.html
```

### Slack 설정 (선택)
1. Slack 앱 생성 → Bot Token(`xoxb-`) 발급, `chat:write` 권한, 채널에 봇 초대
2. `.env`에 `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID` 입력
3. `config.yaml`의 `slack.enabled: true` (토큰 없으면 자동으로 무력화되어 안전)

---

## 아키텍처

```
run.py / backtest.py / optimize.py / report.py    ← 진입점
core/
  config.py        설정 로더 + live 차단 안전장치
  market_data.py   BinanceFuturesClient (fapi 공개 API, 백오프/레이트리밋)
  paper_wallet.py  ExecutionEngine(순수) + PaperWallet  ⭐ 롱/숏·레버리지·청산
  risk.py          포지션 사이징·손절/익절·일일한도·레버리지 캡
  orchestrator.py  실시간 루프(시세→청산→손절/익절→전략→리스크→체결→기록)
  storage.py       SQLite(WAL): trades / equity_curve / signals / account_state
  notifier.py      SlackNotifier / NullNotifier
strategies/        base(레지스트리) + volatility_breakout / ma_crossover / rsi_mean_reversion
dashboard/         FastAPI app + StateReader + routers + static/index.html
tests/             pytest 133케이스 (체결/청산/손익/전략/백테스트)
```

**설계 핵심**
- 체결·수수료·청산 로직은 순수 `ExecutionEngine`에 모아 백테스트와 실시간이 공유 → 일관성.
- 청산가(격리): 롱 `entry×(1 − 1/lev + mmr)`, 숏 `entry×(1 + 1/lev − mmr)`.
- 봇(쓰기)과 대시보드(읽기)는 SQLite WAL로 분리. `StateReader` 추상화로 추후 원격 DB(배포) 전환 가능.

---

## 테스트
```bash
pytest -q          # 133 케이스
```

---

## 안전 / 한계

- **실거래 미구현**: `mode: live`는 `RuntimeError`로 차단(실거래 사고 원천봉쇄).
- **펀딩비**는 기본 비활성(`account.funding_enabled: false`) — 백테스트 일관성 우선.
- 청산/슬리피지는 **간이 모델**이다(실거래소의 부분청산·청산수수료·펀딩·호가 깊이와 다름).
- 백테스트의 인트라바 청산은 봉의 저가(롱)/고가(숏)로 보수적 판정한다.

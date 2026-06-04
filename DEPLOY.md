# 클라우드 배포 가이드 (무료 티어)

> 전부 무료 범위 내에서 동작합니다. 신용카드 없이 시작 가능.

---

## (1) Supabase — DB 설정

1. [supabase.com](https://supabase.com) 에서 새 프로젝트 생성 (Free 플랜).
2. **SQL Editor** 탭 → `supabase/schema.sql` 전체 내용을 붙여넣고 **Run** 실행.
   - 4개 테이블(trades, equity_curve, signals, account_state)과 RLS 정책이 생성됩니다.
3. **Project Settings → Database** 탭:
   - **Connection string (URI)** 항목의 `postgresql://postgres:[YOUR-PASSWORD]@db.[ref].supabase.co:5432/postgres` 를 복사. → `SUPABASE_DB_URL` 에 사용.
4. **Project Settings → API** 탭:
   - **Project URL** 복사 → `NEXT_PUBLIC_SUPABASE_URL`
   - **anon public** 키 복사 → `NEXT_PUBLIC_SUPABASE_ANON_KEY`

---

## (2) GitHub — Actions 봇 틱 설정

1. 이 저장소를 GitHub 에 푸시:
   ```bash
   git init
   git add .
   git commit -m "initial"
   git remote add origin https://github.com/<user>/<repo>.git
   git push -u origin main
   ```
2. **Settings → Secrets and variables → Actions → New repository secret** 에서 등록:
   | 이름 | 값 |
   |------|------|
   | `SUPABASE_DB_URL` | postgresql://... (위 (1)-3 에서 복사) |
   | `SLACK_BOT_TOKEN` | xoxb-... (선택) |
   | `SLACK_CHANNEL_ID` | C... (선택) |
3. **Actions 탭** → 워크플로우가 보이면 활성화 상태 확인.
4. 매 15분마다 `bot-tick.yml` 이 자동 실행됩니다.
   - 수동 실행: Actions → "Bot Tick (15min)" → **Run workflow**.
   - 로그는 Actions 탭 → 해당 실행 클릭 → **tick** job 에서 확인.

> **⚠️ 주의**: GitHub 은 60일간 저장소에 활동(push 등)이 없으면 스케줄을 자동 비활성화합니다. 비활성화된 경우 Settings → Actions 에서 재활성화하거나, 빈 커밋을 push 하세요.
>
> **⚠️ 주의**: cron 스케줄은 best-effort 입니다. GitHub Actions 서버 부하에 따라 수 분~수십 분 지연될 수 있습니다. 초정밀 타이밍이 필요하다면 아래 "(4) 대안" 을 참고하세요.

---

## (3) Vercel — 대시보드 배포

1. [vercel.com](https://vercel.com) 에서 GitHub 계정 연동 후 **New Project** 클릭.
2. 이 저장소를 Import.
3. **Configure Project** 화면:
   - **Root Directory**: `web` 으로 변경 (Next.js 프로젝트 위치).
   - **Framework Preset**: Next.js 자동 감지.
4. **Environment Variables** 추가:
   | 이름 | 값 |
   |------|------|
   | `NEXT_PUBLIC_SUPABASE_URL` | (1)-4 에서 복사한 Project URL |
   | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | (1)-4 에서 복사한 anon key |
5. **Deploy** 클릭. 빌드 완료 후 `https://<project>.vercel.app` 에서 대시보드 확인.

---

## (4) 확인 체크리스트

- [ ] Actions 탭 → "Bot Tick (15min)" → 실행 성공 (녹색)
- [ ] Supabase → Table Editor → `equity_curve` 테이블에 행 추가 확인
- [ ] Vercel 대시보드 화면에서 데이터 표시 확인

---

## (5) 대안: 진짜 24/7 상주 실행 (Dockerfile)

GitHub Actions cron 의 지연·비활성화가 문제라면, `Dockerfile` 로 상주 프로세스를 배포할 수 있습니다.

### Fly.io (무료 티어: 3 shared-cpu-1x VM)

```bash
# fly CLI 설치 후
fly auth login
fly launch --name my-stock-bot --image python:3.12-slim --no-deploy
# fly.toml 생성 확인 후
fly secrets set STORAGE_BACKEND=supabase SUPABASE_DB_URL=postgresql://...
fly deploy
```

### Render (무료 티어: 750 h/월 Web Service)

1. New Web Service → GitHub 연동 → 이 저장소 선택.
2. **Dockerfile** 자동 감지.
3. **Environment Variables** 에 `STORAGE_BACKEND`, `SUPABASE_DB_URL` 등 입력.
4. **Start Command**: `python run.py`

### Oracle Cloud Free VM (Always Free)

```bash
# VM 생성 후 SSH 접속
git clone https://github.com/<user>/<repo>.git && cd <repo>
docker build -t stock-bot .
docker run -d --restart=always \
  -e STORAGE_BACKEND=supabase \
  -e SUPABASE_DB_URL=postgresql://... \
  stock-bot
```

---

## 비용 요약

| 서비스 | 무료 한도 | 비고 |
|--------|-----------|------|
| GitHub Actions | 2,000 분/월 | 15분 틱 × 24h × 30일 ≈ 720 실행 × ~30초 = **360분** (여유 있음) |
| Supabase | DB 500MB, 50,000 행/월 API | 직접 Postgres 연결이므로 행 제한 없음 |
| Vercel | Hobby 플랜 무료 | 대역폭 100GB/월 |
| Fly.io / Render | 무료 VM 1개 | 상주 실행 시 사용 |

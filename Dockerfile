# Dockerfile — 24/7 상주 실행 대안 (Fly.io / Render / Oracle Free VM 등)
# GitHub Actions cron 대신 진짜 상주 루프(run.py)가 필요할 때 사용.
#
# 빌드:  docker build -t stock-bot .
# 실행:  docker run -e STORAGE_BACKEND=supabase \
#                   -e SUPABASE_DB_URL=postgresql://... \
#                   -e SLACK_BOT_TOKEN=xoxb-... \
#                   -e SLACK_CHANNEL_ID=C... \
#                   stock-bot

FROM python:3.12-slim

# 작업 디렉터리
WORKDIR /app

# 의존성 먼저 설치 (캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 복사
COPY . .

# 기본 실행: 상주 루프 (run.py)
# GitHub Actions 환경에서는 tick.py 를 직접 python tick.py 로 호출.
CMD ["python", "run.py"]

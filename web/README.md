# Paper Trading Dashboard (Next.js)

Binance USDT-M 무기한 선물 페이퍼 트레이딩 대시보드.  
Supabase를 데이터 소스로 읽고, Vercel에 배포.

## 로컬 실행

```bash
cp .env.example .env.local
# .env.local 에 실제 Supabase URL / Anon Key 입력

npm install
npm run dev
# http://localhost:3000
```

## 필요한 환경변수

| 변수 | 설명 |
|------|------|
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase 프로젝트 URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon(공개) 키 |

## Vercel 배포

1. Vercel 대시보드 → New Project → 이 저장소 import
2. **Root Directory** 를 `web` 으로 설정
3. Environment Variables 에 위 2개 키 등록
4. Deploy

> Supabase RLS: anon 역할에 `SELECT` 권한만 허용 필요 (INSERT는 봇이 서비스 키로 직접 연결).

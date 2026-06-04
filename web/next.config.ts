import type { NextConfig } from "next";
import path from "path";

const nextConfig: NextConfig = {
  // Supabase 환경변수 없을 때도 빌드 통과
  env: {
    NEXT_PUBLIC_SUPABASE_URL: process.env.NEXT_PUBLIC_SUPABASE_URL ?? "",
    NEXT_PUBLIC_SUPABASE_ANON_KEY:
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "",
  },
  // turbopack root 경고 제거
  turbopack: {
    root: path.resolve(__dirname),
  },
};

export default nextConfig;

import { createClient, SupabaseClient } from "@supabase/supabase-js";

// 환경변수 없으면 빌드는 통과, 런타임에 빈 데이터 반환
const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

export const isConfigured = Boolean(supabaseUrl && supabaseAnonKey);

// 환경변수 없을 때 더미 클라이언트 대신 null guard 패턴 사용
let _client: SupabaseClient | null = null;

export function getSupabase(): SupabaseClient | null {
  if (!isConfigured) return null;
  if (!_client) {
    _client = createClient(supabaseUrl, supabaseAnonKey);
  }
  return _client;
}

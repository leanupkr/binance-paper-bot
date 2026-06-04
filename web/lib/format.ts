// 숫자/날짜 포맷 유틸

export const fmt = {
  /** 소수점 d자리 로컬 포맷 */
  num: (v: number | null | undefined, d = 2): string => {
    if (v == null) return "—";
    return Number(v).toLocaleString("ko-KR", {
      minimumFractionDigits: d,
      maximumFractionDigits: d,
    });
  },

  /** 퍼센트 (±prefix) */
  pct: (v: number | null | undefined, d = 2): string => {
    if (v == null) return "—";
    const n = Number(v);
    return (n >= 0 ? "+" : "") + n.toFixed(d) + "%";
  },

  /** 가격 (소수점 가변) */
  price: (v: number | null | undefined): string => {
    if (v == null) return "—";
    return Number(v).toLocaleString("ko-KR", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 6,
    });
  },

  /** Unix timestamp → KST 문자열 */
  ts: (ts: number | null | undefined): string => {
    if (!ts) return "—";
    const d = new Date(ts * 1000);
    return d.toLocaleString("ko-KR", {
      timeZone: "Asia/Seoul",
      hour12: false,
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  },

  /** 차트 x축 레이블 */
  chartTs: (ts: number): string => {
    const d = new Date(ts * 1000);
    return d.toLocaleString("ko-KR", {
      timeZone: "Asia/Seoul",
      hour12: false,
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  },
};

/** 양수=green, 음수=red CSS className */
export function colorCls(v: number | null | undefined): string {
  if (v == null) return "";
  if (v > 0) return "text-long";
  if (v < 0) return "text-short-DEFAULT";
  return "";
}

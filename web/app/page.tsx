"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ResponsiveContainer,
} from "recharts";
import { getSupabase, isConfigured } from "@/lib/supabase";
import { fmt } from "@/lib/format";
import type { AccountState, EquityCurveRow, TradeRow, PositionEntry } from "@/lib/types";

// ── 상수 ──────────────────────────────────────────────────────────────────────
const POLL_MS = 5000;
const INITIAL_EQUITY_HOURS = 24;
const EQUITY_LIMIT = 500;
const TRADES_LIMIT = 20;

// ── 타입 ──────────────────────────────────────────────────────────────────────
interface DashboardData {
  state: AccountState | null;
  equity: EquityCurveRow[];
  trades: TradeRow[];
}

interface KPI {
  equity: number;
  balance: number;
  totalReturnPct: number | null;
  dailyReturnPct: number | null;
  mdd: number | null;
  sharpe: number | null;
  winRate: number | null;
  tradeCount: number;
  profitFactor: number | null;
  liqCount: number;
  realizedPnl: number;
  unrealizedPnl: number;
  avgWin: number | null;
  avgLoss: number | null;
}

// ── KPI 계산 ─────────────────────────────────────────────────────────────────
function calcKPI(
  state: AccountState | null,
  equity: EquityCurveRow[],
  trades: TradeRow[]
): KPI {
  const stateBalance = state?.balance_usdt ?? 0;
  const latestEquity = equity.length > 0 ? equity[equity.length - 1].total_equity : 0;
  const realizedPnl = state?.realized_pnl ?? 0;

  // 미실현: equity_curve 최신 행
  const latestUnrealized =
    equity.length > 0 ? equity[equity.length - 1].unrealized_pnl : 0;

  // 누적 수익률: 첫 equity 대비
  const initialEquity = equity.length > 0 ? equity[0].total_equity : null;
  const totalReturnPct =
    initialEquity && initialEquity > 0
      ? ((latestEquity - initialEquity) / initialEquity) * 100
      : null;

  // 일간 수익률: 24시간 전 equity 대비
  const now = Date.now() / 1000;
  const dayAgoTs = now - 86400;
  const dayAgoRow = equity.find((r) => r.timestamp >= dayAgoTs) ?? null;
  const dailyReturnPct =
    dayAgoRow && dayAgoRow.total_equity > 0
      ? ((latestEquity - dayAgoRow.total_equity) / dayAgoRow.total_equity) * 100
      : null;

  // MDD: equity 곡선에서 계산
  let mdd: number | null = null;
  if (equity.length > 1) {
    let peak = equity[0].total_equity;
    let maxDd = 0;
    for (const row of equity) {
      if (row.total_equity > peak) peak = row.total_equity;
      const dd = peak > 0 ? ((peak - row.total_equity) / peak) * 100 : 0;
      if (dd > maxDd) maxDd = dd;
    }
    mdd = -maxDd;
  }

  // 거래 기반 지표
  const closedTrades = trades.filter((t) => t.realized_pnl !== 0);
  const wins = closedTrades.filter((t) => t.realized_pnl > 0);
  const losses = closedTrades.filter((t) => t.realized_pnl < 0);
  const liqTrades = trades.filter((t) => t.is_liquidation);

  const winRate =
    closedTrades.length > 0
      ? (wins.length / closedTrades.length) * 100
      : null;

  const totalWin = wins.reduce((s, t) => s + t.realized_pnl, 0);
  const totalLoss = Math.abs(losses.reduce((s, t) => s + t.realized_pnl, 0));
  const profitFactor =
    totalLoss > 0 ? totalWin / totalLoss : wins.length > 0 ? null : null;

  const avgWin =
    wins.length > 0 ? totalWin / wins.length : null;
  const avgLoss =
    losses.length > 0
      ? Math.abs(losses.reduce((s, t) => s + t.realized_pnl, 0)) / losses.length
      : null;

  // Sharpe: 단순 추정 (equity 일간 변화율 기반)
  let sharpe: number | null = null;
  if (equity.length > 10) {
    const returns: number[] = [];
    for (let i = 1; i < equity.length; i++) {
      const prev = equity[i - 1].total_equity;
      if (prev > 0) returns.push((equity[i].total_equity - prev) / prev);
    }
    if (returns.length > 0) {
      const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
      const variance =
        returns.reduce((a, b) => a + (b - mean) ** 2, 0) / returns.length;
      const std = Math.sqrt(variance);
      sharpe = std > 0 ? (mean / std) * Math.sqrt(365 * 24) : null;
    }
  }

  return {
    equity: latestEquity || stateBalance,
    balance: stateBalance,
    totalReturnPct,
    dailyReturnPct,
    mdd,
    sharpe,
    winRate,
    tradeCount: trades.length,
    profitFactor,
    liqCount: liqTrades.length,
    realizedPnl,
    unrealizedPnl: latestUnrealized,
    avgWin,
    avgLoss,
  };
}

// ── 포지션 파싱 ───────────────────────────────────────────────────────────────
function parsePositions(state: AccountState | null): Record<string, PositionEntry> {
  if (!state?.positions_json) return {};
  if (typeof state.positions_json === "object") {
    return state.positions_json as Record<string, PositionEntry>;
  }
  try {
    return JSON.parse(state.positions_json as string);
  } catch {
    return {};
  }
}

// ── 컴포넌트: MetricCard ──────────────────────────────────────────────────────
function MetricCard({
  label,
  value,
  sub,
  colorSign,
}: {
  label: string;
  value: string;
  sub?: string;
  colorSign?: number | null;
}) {
  const cls =
    colorSign == null
      ? ""
      : colorSign > 0
      ? "text-pos"
      : colorSign < 0
      ? "text-neg"
      : "";

  return (
    <div className="metric-card">
      <div
        style={{
          fontSize: 11,
          color: "var(--ink-3)",
          fontWeight: 500,
          textTransform: "uppercase",
          letterSpacing: "0.04em",
          marginBottom: 6,
        }}
      >
        {label}
      </div>
      <div
        className={`font-num ${cls}`}
        style={{ fontSize: 22, fontWeight: 600, letterSpacing: "-0.03em" }}
      >
        {value}
      </div>
      {sub && (
        <div className="font-num" style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 4 }}>
          {sub}
        </div>
      )}
    </div>
  );
}

// ── 컴포넌트: EquityChart ─────────────────────────────────────────────────────
function EquityChart({
  data,
  hours,
  onHoursChange,
}: {
  data: EquityCurveRow[];
  hours: number;
  onHoursChange: (h: number) => void;
}) {
  const ranges = [6, 24, 72, 168, 720];
  const rangeLabels: Record<number, string> = {
    6: "6H",
    24: "24H",
    72: "3D",
    168: "7D",
    720: "30D",
  };

  const chartData = data.map((r) => ({
    ts: fmt.chartTs(r.timestamp),
    equity: r.total_equity,
  }));

  return (
    <div className="panel">
      <div className="panel-header">
        <h2 style={{ fontSize: 13, fontWeight: 600, letterSpacing: "-0.01em" }}>
          자산 곡선
        </h2>
        <div className="range-btn">
          {ranges.map((h) => (
            <button
              key={h}
              className={hours === h ? "active" : ""}
              onClick={() => onHoursChange(h)}
            >
              {rangeLabels[h]}
            </button>
          ))}
        </div>
      </div>
      <div style={{ padding: "16px 18px", height: 240 }}>
        {chartData.length === 0 ? (
          <div
            style={{
              height: "100%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--ink-3)",
              fontSize: 13,
            }}
          >
            데이터 없음
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(22,22,26,0.06)" />
              <XAxis
                dataKey="ts"
                tick={{ fill: "#7a7a88", fontFamily: "'JetBrains Mono', monospace", fontSize: 10 }}
                tickLine={false}
                axisLine={false}
                interval="preserveStartEnd"
              />
              <YAxis
                tick={{ fill: "#7a7a88", fontFamily: "'JetBrains Mono', monospace", fontSize: 10 }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v) => fmt.num(v, 0)}
                width={64}
              />
              <Tooltip
                contentStyle={{
                  background: "#16161a",
                  border: "1px solid rgba(255,255,255,0.08)",
                  borderRadius: 6,
                  fontSize: 12,
                  fontFamily: "'JetBrains Mono', monospace",
                  color: "#faf9f6",
                }}
                formatter={(v) => [`${fmt.num(v as number)} USDT`, "평가자산"]}
                labelStyle={{ color: "#a0a0b0", marginBottom: 4 }}
              />
              <Line
                type="monotone"
                dataKey="equity"
                stroke="#1e40af"
                strokeWidth={1.5}
                dot={false}
                activeDot={{ r: 4, fill: "#1e40af" }}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

// ── 컴포넌트: PositionTable ───────────────────────────────────────────────────
function PositionTable({ positions }: { positions: Record<string, PositionEntry> }) {
  const entries = Object.entries(positions);

  return (
    <div className="panel">
      <div className="panel-header">
        <h2 style={{ fontSize: 13, fontWeight: 600, letterSpacing: "-0.01em" }}>
          보유 포지션
        </h2>
      </div>
      <div style={{ overflowX: "auto", WebkitOverflowScrolling: "touch" }}>
        <table>
          <thead>
            <tr>
              <th>심볼</th>
              <th>방향</th>
              <th>레버리지</th>
              <th>진입가</th>
              <th>청산가</th>
              <th>증거금</th>
              <th>미실현</th>
            </tr>
          </thead>
          <tbody>
            {entries.length === 0 ? (
              <tr>
                <td
                  colSpan={7}
                  style={{
                    textAlign: "center",
                    padding: 28,
                    color: "var(--ink-3)",
                    fontFamily: "var(--font-sans)",
                    fontSize: 13,
                  }}
                >
                  포지션 없음
                </td>
              </tr>
            ) : (
              entries.map(([sym, p]) => {
                const side = (p.side ?? "").toString().toUpperCase();
                const isLong = side === "LONG";
                const unrealizedPnl = p.unrealized_pnl;
                return (
                  <tr key={sym}>
                    <td style={{ fontWeight: 600 }}>{sym}</td>
                    <td>
                      <span className={`badge ${isLong ? "badge-long" : "badge-short"}`}>
                        {side}
                      </span>
                    </td>
                    <td>{p.leverage != null ? `${p.leverage}x` : "—"}</td>
                    <td>{fmt.price(p.entry_price)}</td>
                    <td>{fmt.price(p.liquidation_price ?? undefined)}</td>
                    <td>{fmt.num(p.margin_usdt)} USDT</td>
                    <td
                      className={
                        unrealizedPnl == null
                          ? ""
                          : unrealizedPnl > 0
                          ? "text-pos"
                          : unrealizedPnl < 0
                          ? "text-neg"
                          : ""
                      }
                    >
                      {unrealizedPnl != null ? `${fmt.num(unrealizedPnl)} USDT` : "—"}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── 컴포넌트: ReportPanel ─────────────────────────────────────────────────────
function ReportPanel({ kpi }: { kpi: KPI }) {
  const items: [string, string, string][] = [
    ["총 수익률", kpi.totalReturnPct != null ? fmt.pct(kpi.totalReturnPct) : "—", kpi.totalReturnPct != null ? (kpi.totalReturnPct > 0 ? "text-pos" : kpi.totalReturnPct < 0 ? "text-neg" : "") : ""],
    ["일간 수익률", kpi.dailyReturnPct != null ? fmt.pct(kpi.dailyReturnPct) : "—", kpi.dailyReturnPct != null ? (kpi.dailyReturnPct > 0 ? "text-pos" : kpi.dailyReturnPct < 0 ? "text-neg" : "") : ""],
    ["승률", kpi.winRate != null ? fmt.pct(kpi.winRate, 1) : "—", kpi.winRate != null ? (kpi.winRate > 50 ? "text-pos" : kpi.winRate < 50 ? "text-neg" : "") : ""],
    ["수익 팩터", kpi.profitFactor != null ? fmt.num(kpi.profitFactor) : "—", kpi.profitFactor != null ? (kpi.profitFactor > 1 ? "text-pos" : "text-neg") : ""],
    ["평균 익절", kpi.avgWin != null ? `${fmt.num(kpi.avgWin)} USDT` : "—", "text-pos"],
    ["평균 손절", kpi.avgLoss != null ? `${fmt.num(kpi.avgLoss)} USDT` : "—", "text-neg"],
    ["MDD", kpi.mdd != null ? fmt.pct(kpi.mdd) : "—", "text-neg"],
    ["Sharpe", kpi.sharpe != null ? fmt.num(kpi.sharpe) : "—", kpi.sharpe != null ? (kpi.sharpe > 0 ? "text-pos" : "text-neg") : ""],
    ["실현 손익", `${fmt.num(kpi.realizedPnl)} USDT`, kpi.realizedPnl > 0 ? "text-pos" : kpi.realizedPnl < 0 ? "text-neg" : ""],
    ["미실현 손익", `${fmt.num(kpi.unrealizedPnl)} USDT`, kpi.unrealizedPnl > 0 ? "text-pos" : kpi.unrealizedPnl < 0 ? "text-neg" : ""],
    ["총 거래수", `${kpi.tradeCount}건`, ""],
    ["강제청산", `${kpi.liqCount}회`, kpi.liqCount > 0 ? "text-neg" : ""],
  ];

  return (
    <div className="panel">
      <div className="panel-header">
        <h2 style={{ fontSize: 13, fontWeight: 600, letterSpacing: "-0.01em" }}>
          성과 요약
        </h2>
      </div>
      <div className="report-grid">
        {items.map(([label, val, cls]) => (
          <div className="report-item" key={label}>
            <span style={{ fontSize: 12, color: "var(--ink-3)" }}>{label}</span>
            <span
              className={`font-num ${cls}`}
              style={{ fontSize: 13, fontWeight: 500 }}
            >
              {val}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── 컴포넌트: TradeTable ──────────────────────────────────────────────────────
function TradeTable({ trades }: { trades: TradeRow[] }) {
  return (
    <div className="panel">
      <div className="panel-header">
        <h2 style={{ fontSize: 13, fontWeight: 600, letterSpacing: "-0.01em" }}>
          최근 거래
        </h2>
      </div>
      <div style={{ overflowX: "auto", WebkitOverflowScrolling: "touch" }}>
        <table>
          <thead>
            <tr>
              <th>시각(KST)</th>
              <th>심볼</th>
              <th>방향</th>
              <th>액션</th>
              <th>트리거</th>
              <th>체결가</th>
              <th>수량</th>
              <th>명목</th>
              <th>실현손익</th>
              <th>수수료</th>
            </tr>
          </thead>
          <tbody>
            {trades.length === 0 ? (
              <tr>
                <td
                  colSpan={10}
                  style={{
                    textAlign: "center",
                    padding: 28,
                    color: "var(--ink-3)",
                    fontFamily: "var(--font-sans)",
                    fontSize: 13,
                  }}
                >
                  거래 없음
                </td>
              </tr>
            ) : (
              trades.map((t) => {
                const side = (t.side ?? "").toUpperCase();
                const isLong = side === "LONG";
                const pnlCls =
                  t.realized_pnl > 0
                    ? "text-pos"
                    : t.realized_pnl < 0
                    ? "text-neg"
                    : "";
                return (
                  <tr key={t.id}>
                    <td>{fmt.ts(t.timestamp)}</td>
                    <td style={{ fontWeight: 600 }}>{t.symbol ?? "—"}</td>
                    <td>
                      <span className={`badge ${isLong ? "badge-long" : "badge-short"}`}>
                        {side}
                      </span>
                    </td>
                    <td>
                      {t.action ?? "—"}
                      {Boolean(t.is_liquidation) && (
                        <span className="badge badge-liq" style={{ marginLeft: 4 }}>
                          청산
                        </span>
                      )}
                    </td>
                    <td>{t.trigger ?? "—"}</td>
                    <td>{fmt.price(t.filled_price)}</td>
                    <td>{fmt.num(t.quantity, 4)}</td>
                    <td>{fmt.num(t.notional)} USDT</td>
                    <td className={pnlCls}>{fmt.num(t.realized_pnl)} USDT</td>
                    <td>{fmt.num(t.fee_usdt)} USDT</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── 메인 페이지 ───────────────────────────────────────────────────────────────
export default function DashboardPage() {
  const [data, setData] = useState<DashboardData>({
    state: null,
    equity: [],
    trades: [],
  });
  const [equityHours, setEquityHours] = useState(INITIAL_EQUITY_HOURS);
  const [lastUpdate, setLastUpdate] = useState<string>("");
  const [isOnline, setIsOnline] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchData = useCallback(async (hours: number) => {
    const supabase = getSupabase();
    if (!supabase) {
      setError("Supabase 환경변수가 설정되지 않았습니다. .env.local 을 확인하세요.");
      return;
    }

    try {
      const now = Date.now() / 1000;
      const since = now - hours * 3600;

      // 병렬 쿼리
      const [stateRes, equityRes, tradesRes] = await Promise.all([
        supabase
          .from("account_state")
          .select("*")
          .order("id", { ascending: false })
          .limit(1),
        supabase
          .from("equity_curve")
          .select("*")
          .gte("timestamp", since)
          .order("timestamp", { ascending: true })
          .limit(EQUITY_LIMIT),
        supabase
          .from("trades")
          .select("*")
          .order("id", { ascending: false })
          .limit(TRADES_LIMIT),
      ]);

      if (stateRes.error) throw stateRes.error;
      if (equityRes.error) throw equityRes.error;
      if (tradesRes.error) throw tradesRes.error;

      const stateRow = stateRes.data?.[0] ?? null;
      setData({
        state: stateRow as AccountState | null,
        equity: (equityRes.data ?? []) as EquityCurveRow[],
        trades: (tradesRes.data ?? []) as TradeRow[],
      });

      // 온라인 판정: 마지막 account_state 가 2분 이내
      const lastTick = stateRow?.timestamp ?? 0;
      setIsOnline(Boolean(stateRow) && now - lastTick < 120);
      setError(null);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(`데이터 로드 오류: ${msg}`);
      setIsOnline(false);
    }

    setLastUpdate(
      new Date().toLocaleTimeString("ko-KR", { hour12: false })
    );
  }, []);

  // 시간 범위 변경 시 즉시 재조회
  const handleHoursChange = (h: number) => {
    setEquityHours(h);
    fetchData(h);
  };

  useEffect(() => {
    fetchData(equityHours);
    pollRef.current = setInterval(() => fetchData(equityHours), POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchData]);

  const kpi = calcKPI(data.state, data.equity, data.trades);
  const positions = parsePositions(data.state);
  const mode = data.state?.mode ?? null;

  return (
    <>
      {/* ── 헤더 ── */}
      <header className="header">
        <div style={{ fontSize: 15, fontWeight: 600, letterSpacing: "-0.02em" }}>
          Paper Trading{" "}
          <span style={{ color: "var(--accent)" }}>Dashboard</span>
        </div>
        <div
          className="font-num"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            fontSize: 12,
            color: "var(--ink-3)",
          }}
        >
          <span
            className={`status-dot${isOnline ? "" : " offline"}`}
          />
          <span>{mode ? mode.toUpperCase() : "—"}</span>
          <span>갱신 {lastUpdate || "—"}</span>
        </div>
      </header>

      {/* ── 본문 ── */}
      <main
        style={{
          maxWidth: 1400,
          margin: "0 auto",
          padding: 24,
          display: "flex",
          flexDirection: "column",
          gap: 20,
        }}
      >
        {/* 오류 배너 */}
        {error && (
          <div
            style={{
              background: "var(--warn-bg)",
              color: "var(--warn)",
              border: "1px solid rgba(180,83,9,0.2)",
              borderRadius: 6,
              padding: "10px 16px",
              fontSize: 13,
              fontFamily: "var(--font-sans)",
            }}
          >
            {error}
          </div>
        )}

        {/* ── KPI 카드 그리드 ── */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
            gap: 12,
          }}
        >
          <MetricCard
            label="평가자산"
            value={`${fmt.num(kpi.equity)} USDT`}
            sub={`가용 ${fmt.num(kpi.balance)} USDT`}
          />
          <MetricCard
            label="누적 수익률"
            value={kpi.totalReturnPct != null ? fmt.pct(kpi.totalReturnPct) : "—"}
            sub={`실현 ${fmt.num(kpi.realizedPnl)} USDT`}
            colorSign={kpi.totalReturnPct}
          />
          <MetricCard
            label="일간 수익률"
            value={kpi.dailyReturnPct != null ? fmt.pct(kpi.dailyReturnPct) : "—"}
            sub={`미실현 ${fmt.num(kpi.unrealizedPnl)} USDT`}
            colorSign={kpi.dailyReturnPct}
          />
          <MetricCard
            label="MDD"
            value={kpi.mdd != null ? fmt.pct(kpi.mdd) : "—"}
            sub={`Sharpe ${kpi.sharpe != null ? fmt.num(kpi.sharpe) : "—"}`}
            colorSign={kpi.mdd}
          />
          <MetricCard
            label="승률"
            value={kpi.winRate != null ? fmt.pct(kpi.winRate, 1) : "—"}
            sub={`거래 ${kpi.tradeCount}건`}
            colorSign={kpi.winRate != null ? kpi.winRate - 50 : null}
          />
          <MetricCard
            label="수익 팩터"
            value={kpi.profitFactor != null ? fmt.num(kpi.profitFactor) : "—"}
            sub={`청산 ${kpi.liqCount}회`}
            colorSign={kpi.profitFactor != null ? kpi.profitFactor - 1 : null}
          />
        </div>

        {/* ── 자산 곡선 ── */}
        <EquityChart
          data={data.equity}
          hours={equityHours}
          onHoursChange={handleHoursChange}
        />

        {/* ── 포지션 + 성과 2열 ── */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 20,
          }}
          className="panel-row-responsive"
        >
          <PositionTable positions={positions} />
          <ReportPanel kpi={kpi} />
        </div>

        {/* ── 최근 거래 ── */}
        <TradeTable trades={data.trades} />
      </main>

      {/* 반응형 스타일 */}
      <style>{`
        @media (max-width: 900px) {
          .panel-row-responsive {
            grid-template-columns: 1fr !important;
          }
        }
      `}</style>
    </>
  );
}

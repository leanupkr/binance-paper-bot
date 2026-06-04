// Supabase 테이블 타입 (스키마 계약과 동일)

export interface AccountState {
  id: number;
  timestamp: number;
  timestamp_kst: string | null;
  balance_usdt: number;
  positions_json: string | Record<string, PositionEntry>;
  realized_pnl: number;
  mode: string;
}

export interface PositionEntry {
  side: "LONG" | "SHORT" | "long" | "short";
  leverage: number;
  entry_price: number;
  liquidation_price: number | null;
  margin_usdt: number;
  quantity: number;
  unrealized_pnl?: number;
}

export interface EquityCurveRow {
  id: number;
  timestamp: number;
  timestamp_kst: string | null;
  total_equity: number;
  balance_usdt: number;
  used_margin: number;
  unrealized_pnl: number;
}

export interface TradeRow {
  id: number;
  timestamp: number;
  timestamp_kst: string | null;
  symbol: string;
  side: string;
  action: string;
  trigger: string | null;
  strategy_name: string | null;
  leverage: number;
  mark_price: number | null;
  filled_price: number;
  quantity: number;
  notional: number;
  fee_usdt: number;
  realized_pnl: number;
  margin: number;
  is_liquidation: boolean;
  balance_after: number;
  equity_after: number;
}

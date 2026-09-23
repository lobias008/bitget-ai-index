"""Deterministic paper simulator: classified signals in, simulated fills,
positions and hypothetical P&L out.

Accounting rules:
  * Every internal number is a Decimal. Rounding happens only when values
    are serialized for display - never before arithmetic.
  * Sizing mirrors the strategy's ``_position_plan`` formulas exactly
    (risk% of the margin budget / ATR stop distance, per-position notional
    capped by margin x leverage), with the paper account's initial balance
    as the margin budget. This reuses the existing risk rules; it does not
    change them.
  * Cash accounting is futures-margin style: entries debit notional /
    leverage plus fee; exits credit the margin share plus gross P&L minus
    fee. Equity = cash + escrowed margin + unrealized P&L at the latest
    closed bar.
  * Within a bar the stop is checked BEFORE take-profits (conservative),
    and a gapped open fills at the worse open price.
  * The daily circuit breaker (config threshold, default -2%) flattens all
    positions at the current close and locks new entries for 24h. This is
    a second, independent layer: the replay runner also feeds the simulated
    daily P&L back to the strategy, which refuses to emit entries at the
    limit on its own.
  * Duplicate signals (same symbol + timestamp) and entries while a
    position is already open are rejected and recorded as events.

Nothing here can place a real order - there is no order code in this
package, only Decimal arithmetic over historical bars.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Sequence

from .signals import ACTIONABLE, iso_utc

D = Decimal
HOURS_24_MS = 86_400_000
PSARFn = Callable[[str, int], Optional[Decimal]]


def _dec(value) -> Decimal:
    return Decimal(str(value))


@dataclass
class Position:
    symbol: str
    entry_time_ms: int
    reference_price: Decimal     # signal close used by the strategy plan
    entry_price: Decimal         # simulated fill (reference + slippage)
    quantity: Decimal
    initial_quantity: Decimal
    stop_distance: Decimal
    stop_loss: Decimal
    breakeven: Decimal
    target_2r: Decimal
    target_4r: Decimal
    state: str                   # "initial" | "free_trade"
    entry_fee: Decimal
    entry_margin: Decimal
    realized_pnl: Decimal = D(0)
    fees_paid: Decimal = D(0)
    legs: List[dict] = field(default_factory=list)
    exit_reasons: List[str] = field(default_factory=list)
    last_exit_ms: Optional[int] = None


class PaperSimulator:
    """Long-only simulator honoring the strategy's plan levels verbatim."""

    def __init__(
        self,
        config,
        strategy_params: dict,
        psar_fn: Optional[PSARFn] = None,
        effective_params_fn: Optional[Callable[[str], dict]] = None,
    ) -> None:
        self.config = config
        self.initial_balance = config.initial_balance
        self.balance = config.initial_balance
        self.fee_rate = config.fee_pct / D(100)
        self.slip_rate = config.slippage_pct / D(100)
        self.strategy_params = dict(strategy_params)
        self.margin_budget = _dec(strategy_params.get("margin_budget", config.initial_balance))
        self.leverage = _dec(strategy_params.get("leverage", 1))
        self.risk_pct = _dec(strategy_params.get("risk_per_trade_pct", 1.0))
        self.breaker_pct = _dec(strategy_params.get("circuit_breaker_daily_loss_pct", -2.0))
        self.partial_pct = _dec(strategy_params.get("partial_take_profit_pct", 50.0)) / D(100)
        self.account_cap = self.margin_budget * self.leverage
        self.psar_fn = psar_fn
        self.effective_params_fn = effective_params_fn
        self.positions: Dict[str, Position] = {}
        self.closed_trades: List[dict] = []
        self.fills: List[dict] = []
        self.events: List[dict] = []
        self.equity_history: List[dict] = []
        self.processed_keys = set()
        self.last_mark: Dict[str, Decimal] = {}
        self.last_equity: Decimal = self.initial_balance
        self.last_step_ms: Optional[int] = None
        self.day_key: Optional[str] = None
        self.day_start_equity: Decimal = self.initial_balance
        self.locked_until_ms: Optional[int] = None
        self.breaker_trips = 0

    # -- helpers ----------------------------------------------------------
    def _effective_params(self, symbol: str) -> dict:
        if self.effective_params_fn is None:
            return self.strategy_params
        try:
            return dict(self.effective_params_fn(symbol))
        except Exception:
            # Unknown/invalid instruments are already blocked upstream by the
            # strategy; sizing falls back to the global deterministic params.
            return self.strategy_params

    def _locked(self, time_ms: int) -> bool:
        return self.locked_until_ms is not None and time_ms < self.locked_until_ms

    def _equity(self) -> Decimal:
        equity = self.balance
        for position in self.positions.values():
            mark = self.last_mark.get(position.symbol, position.entry_price)
            margin_share = position.entry_margin * position.quantity / position.initial_quantity
            equity += margin_share + (mark - position.entry_price) * position.quantity
        return equity

    def _daily_pct(self, equity: Decimal) -> Decimal:
        if self.day_start_equity == 0:
            return D(0)
        return (equity - self.day_start_equity) / self.day_start_equity * D(100)

    def reported_daily_pnl_pct(self, time_ms: int) -> float:
        """Daily P&L known at the PREVIOUS processed step (no look-ahead).

        Fed back into the strategy's reported_daily_pnl_pct input so its own
        circuit breaker pre-check sees the simulated account state.
        """
        if self.day_key is None:
            return 0.0
        day = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc).date().isoformat()
        if day != self.day_key:
            return 0.0
        return float(self._daily_pct(self.last_equity))

    def _event(self, time_ms: int, event_type: str, **extra) -> None:
        self.events.append({
            "time_ms": time_ms,
            "time": iso_utc(time_ms),
            "type": event_type,
            **extra,
        })

    # -- step processing ----------------------------------------------------
    def process_step(self, time_ms: int, closed_bars: Dict[str, object], signals: Sequence = ()) -> None:
        day = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc).date().isoformat()
        if day != self.day_key:
            self.day_key = day
            self.day_start_equity = self.last_equity

        for symbol in sorted(closed_bars):
            bar = closed_bars[symbol]
            self.last_mark[symbol] = _dec(bar.close)
            position = self.positions.get(symbol)
            if position is not None and position.entry_time_ms < time_ms:
                self._process_exits(position, bar, time_ms)

        equity = self._equity()
        daily_pct = self._daily_pct(equity)
        if not self._locked(time_ms) and daily_pct <= self.breaker_pct:
            self.breaker_trips += 1
            self._flatten_all(time_ms, closed_bars)
            self.locked_until_ms = time_ms + HOURS_24_MS
            equity = self._equity()
            daily_pct = self._daily_pct(equity)
            self._event(
                time_ms,
                "circuit_breaker",
                daily_pnl_pct=str(daily_pct),
                threshold_pct=str(self.breaker_pct),
                locked_until_ms=self.locked_until_ms,
                locked_until=iso_utc(self.locked_until_ms),
                action="flatten_all_positions_and_block_new_entries_24h",
            )

        for signal in signals:
            self._process_entry_signal(signal, closed_bars, time_ms)

        self.last_step_ms = time_ms
        self.last_equity = self._equity()
        self.equity_history.append({
            "time_ms": time_ms,
            "time": iso_utc(time_ms),
            "equity": str(self.last_equity),
            "balance": str(self.balance),
            "unrealized_pnl": str(self.last_equity - self.balance - self._escrowed_margin()),
            "daily_pnl_pct": str(self._daily_pct(self.last_equity)),
            "open_positions": len(self.positions),
        })

    def _escrowed_margin(self) -> Decimal:
        total = D(0)
        for position in self.positions.values():
            total += position.entry_margin * position.quantity / position.initial_quantity
        return total

    # -- entries ----------------------------------------------------------
    def _process_entry_signal(self, signal, closed_bars: Dict[str, object], time_ms: int) -> None:
        if getattr(signal, "classification", None) != ACTIONABLE:
            return  # defense in depth: only actionable paper signals can fill
        key = (signal.symbol, signal.timestamp_ms)
        if key in self.processed_keys:
            self._event(time_ms, "duplicate_signal_rejected", symbol=signal.symbol,
                        signal_timestamp_ms=signal.timestamp_ms)
            return
        self.processed_keys.add(key)
        if self._locked(time_ms):
            self._event(time_ms, "entry_blocked_circuit_breaker", symbol=signal.symbol,
                        locked_until_ms=self.locked_until_ms)
            return
        if signal.symbol in self.positions:
            self._event(time_ms, "entry_ignored_position_open", symbol=signal.symbol)
            return
        bar = closed_bars.get(signal.symbol)
        if bar is None:
            self._event(time_ms, "entry_missing_market_data", symbol=signal.symbol)
            return
        self._open_position(signal, bar, time_ms)

    def size_entry(self, symbol: str, plan: dict):
        """Re-derive the strategy's sizing in exact Decimal arithmetic.

        Mirrors ``_position_plan``: risk_usdt = margin_budget * risk_pct / 100;
        quantity = risk_usdt / stop_distance; quantity is scaled down if the
        notional would exceed margin_budget * leverage. Returns
        (quantity, notional_at_reference) or None when the plan is unusable.
        """
        params = self._effective_params(symbol)
        margin_budget = _dec(params.get("margin_budget", self.margin_budget))
        risk_pct = _dec(params.get("risk_per_trade_pct", self.risk_pct))
        leverage = _dec(params.get("leverage", self.leverage))
        try:
            entry_ref = _dec(plan["entry_price"])
            stop = _dec(plan["stop_loss"])
        except (KeyError, ArithmeticError, TypeError, ValueError):
            return None
        stop_distance = entry_ref - stop
        if stop_distance <= 0 or entry_ref <= 0 or margin_budget <= 0 or leverage <= 0:
            return None
        risk_usdt = margin_budget * risk_pct / D(100)
        quantity = risk_usdt / stop_distance
        cap = margin_budget * leverage
        if quantity * entry_ref > cap:
            quantity = cap / entry_ref
        if quantity <= 0:
            return None
        return quantity, quantity * entry_ref

    def _open_position(self, signal, bar, time_ms: int) -> None:
        plan = dict(getattr(signal, "risk_plan", None) or {})
        try:
            entry_ref = _dec(plan["entry_price"])
            stop = _dec(plan["stop_loss"])
            target_2r = _dec(plan["take_profit_50pct_at_2r"])
            target_4r = _dec(plan["runner_target_4r_plus"])
            breakeven = _dec(plan.get("move_stop_to_breakeven_after_2r", plan["entry_price"]))
        except (KeyError, ArithmeticError, TypeError, ValueError):
            self._event(time_ms, "invalid_plan_rejected", symbol=signal.symbol,
                        reason="risk_plan is missing required price levels")
            return
        sized = self.size_entry(signal.symbol, plan)
        if sized is None:
            self._event(time_ms, "invalid_plan_rejected", symbol=signal.symbol,
                        reason="stop_distance <= 0 or unusable sizing inputs")
            return
        quantity, _planned_notional = sized
        params = self._effective_params(signal.symbol)
        leverage = _dec(params.get("leverage", self.leverage))
        reference = _dec(bar.close)
        fill_price = reference * (D(1) + self.slip_rate)
        notional = quantity * fill_price
        if self._open_notional() + notional > self.account_cap:
            self._event(time_ms, "margin_guard_rejected", symbol=signal.symbol,
                        open_notional=str(self._open_notional()),
                        attempted_notional=str(notional),
                        account_cap=str(self.account_cap))
            return
        margin = notional / leverage if leverage > 0 else notional
        fee = notional * self.fee_rate
        if self.balance - (margin + fee) < 0:
            self._event(time_ms, "insufficient_margin_rejected", symbol=signal.symbol,
                        required_margin=str(margin + fee), balance=str(self.balance))
            return
        self.balance -= (margin + fee)
        stop_distance = entry_ref - stop
        self.positions[signal.symbol] = Position(
            symbol=signal.symbol,
            entry_time_ms=time_ms,
            reference_price=entry_ref,
            entry_price=fill_price,
            quantity=quantity,
            initial_quantity=quantity,
            stop_distance=stop_distance,
            stop_loss=stop,
            breakeven=breakeven,
            target_2r=target_2r,
            target_4r=target_4r,
            state="initial",
            entry_fee=fee,
            entry_margin=margin,
        )
        self.fills.append({
            "time_ms": time_ms,
            "time": iso_utc(time_ms),
            "symbol": signal.symbol,
            "side": "entry",
            "reason": "signal_entry",
            "quantity": str(quantity),
            "reference_price": str(reference),
            "fill_price": str(fill_price),
            "notional": str(notional),
            "margin": str(margin),
            "fee": str(fee),
            "stop_loss": str(stop),
            "target_2r": str(target_2r),
            "target_4r": str(target_4r),
            "mode": "paper",
        })
        self._event(time_ms, "entry_filled", symbol=signal.symbol,
                    quantity=str(quantity), fill_price=str(fill_price),
                    margin=str(margin), fee=str(fee))

    def _open_notional(self) -> Decimal:
        total = D(0)
        for position in self.positions.values():
            total += position.quantity * position.entry_price
        return total

    # -- exits --------------------------------------------------------------
    def _process_exits(self, position: Position, bar, time_ms: int) -> None:
        open_ = _dec(bar.open)
        high = _dec(bar.high)
        low = _dec(bar.low)
        close = _dec(bar.close)
        if position.state == "initial":
            if low <= position.stop_loss:
                self._close(position, position.quantity, self._gap_ref(open_, position.stop_loss),
                            time_ms, "stop_loss")
                return
            if high >= position.target_2r:
                partial_qty = position.quantity * self.partial_pct
                if partial_qty > 0:
                    self._close(position, partial_qty, position.target_2r, time_ms,
                                "partial_take_profit_2r")
                if position.quantity > 0:
                    position.state = "free_trade"
                    position.stop_loss = position.breakeven
                    if high >= position.target_4r:
                        self._close(position, position.quantity, position.target_4r,
                                    time_ms, "runner_target_4r")
                    elif low <= position.stop_loss:
                        # gave back the whole bar after the partial: exit at BE
                        self._close(position, position.quantity,
                                    self._gap_ref(open_, position.stop_loss),
                                    time_ms, "breakeven_stop")
                    else:
                        self._sar_exit_check(position, bar, close, time_ms)
            return
        # free_trade: runner management
        if low <= position.stop_loss:
            reason = "breakeven_stop" if position.stop_loss == position.breakeven else "stop_loss"
            self._close(position, position.quantity, self._gap_ref(open_, position.stop_loss),
                        time_ms, reason)
            return
        if high >= position.target_4r:
            self._close(position, position.quantity, position.target_4r, time_ms,
                        "runner_target_4r")
            return
        self._sar_exit_check(position, bar, close, time_ms)

    def _sar_exit_check(self, position: Position, bar, close: Decimal, time_ms: int) -> None:
        if self.psar_fn is None:
            return
        value = self.psar_fn(position.symbol, time_ms)
        if value is not None and close < value:
            self._close(position, position.quantity, close, time_ms, "sar_flip")

    @staticmethod
    def _gap_ref(open_price: Decimal, level: Decimal) -> Decimal:
        """A bar opening beyond the level fills at the worse open."""
        return open_price if open_price < level else level

    def _close(self, position: Position, quantity: Decimal, reference: Decimal,
               time_ms: int, reason: str) -> None:
        quantity = min(quantity, position.quantity)
        if quantity <= 0:
            return
        fill_price = reference * (D(1) - self.slip_rate)
        gross = (fill_price - position.entry_price) * quantity
        exit_fee = fill_price * quantity * self.fee_rate
        margin_share = position.entry_margin * quantity / position.initial_quantity
        entry_fee_share = position.entry_fee * quantity / position.initial_quantity
        realized = gross - exit_fee - entry_fee_share
        self.balance += margin_share + gross - exit_fee
        position.quantity -= quantity
        position.realized_pnl += realized
        position.fees_paid += exit_fee + entry_fee_share
        position.exit_reasons.append(reason)
        position.last_exit_ms = time_ms
        leg = {
            "time_ms": time_ms,
            "time": iso_utc(time_ms),
            "symbol": position.symbol,
            "side": "exit",
            "reason": reason,
            "quantity": str(quantity),
            "reference_price": str(reference),
            "fill_price": str(fill_price),
            "fee": str(exit_fee),
            "realized_pnl": str(realized),
            "position_state": position.state,
            "mode": "paper",
        }
        position.legs.append(leg)
        self.fills.append(leg)
        if position.quantity <= 0:
            del self.positions[position.symbol]
            risk_units = position.stop_distance * position.initial_quantity
            self.closed_trades.append({
                "symbol": position.symbol,
                "direction": "long",
                "entry_time_ms": position.entry_time_ms,
                "entry_time": iso_utc(position.entry_time_ms),
                "exit_time_ms": time_ms,
                "exit_time": iso_utc(time_ms),
                "reference_price": str(position.reference_price),
                "entry_price": str(position.entry_price),
                "initial_quantity": str(position.initial_quantity),
                "state_at_exit": position.state,
                "exit_reasons": list(position.exit_reasons),
                "legs": len(position.legs),
                "realized_pnl": str(position.realized_pnl),
                "fees": str(position.fees_paid),
                "r_multiple": str(position.realized_pnl / risk_units) if risk_units > 0 else None,
                "mode": "paper",
            })

    def _flatten_all(self, time_ms: int, closed_bars: Dict[str, object]) -> None:
        for symbol in sorted(self.positions):
            position = self.positions[symbol]
            bar = closed_bars.get(symbol)
            if bar is not None:
                reference = _dec(bar.close)
            else:
                reference = self.last_mark.get(symbol, position.entry_price)
            self._close(position, position.quantity, reference, time_ms,
                        "circuit_breaker_flatten")

    # -- reporting ----------------------------------------------------------
    def open_positions_json(self) -> List[dict]:
        out = []
        for symbol in sorted(self.positions):
            position = self.positions[symbol]
            mark = self.last_mark.get(symbol, position.entry_price)
            unrealized = (mark - position.entry_price) * position.quantity
            out.append({
                "symbol": symbol,
                "state": position.state,
                "quantity": str(position.quantity),
                "entry_price": str(position.entry_price),
                "mark_price": str(mark),
                "unrealized_pnl": str(unrealized),
                "stop_loss": str(position.stop_loss),
                "target_4r": str(position.target_4r),
                "entry_time": iso_utc(position.entry_time_ms),
            })
        return out

    def stats(self) -> dict:
        equity = self.last_equity
        unrealized = D(0)
        for position in self.positions.values():
            mark = self.last_mark.get(position.symbol, position.entry_price)
            unrealized += (mark - position.entry_price) * position.quantity
        realized = equity - self.initial_balance - unrealized
        return_pct = (equity / self.initial_balance - D(1)) * D(100) if self.initial_balance else D(0)
        wins = sum(1 for trade in self.closed_trades if Decimal(trade["realized_pnl"]) > 0)
        win_rate = (wins / len(self.closed_trades) * 100.0) if self.closed_trades else None
        return {
            "equity": equity,
            "balance": self.balance,
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "return_pct": return_pct,
            "closed_trades": len(self.closed_trades),
            "win_rate_pct": win_rate,
            "open_positions": len(self.positions),
            "breaker_trips": self.breaker_trips,
            "locked": self._locked(self.last_step_ms) if self.last_step_ms is not None else False,
            "locked_until_ms": self.locked_until_ms,
        }
from datetime import datetime, timezone
from math import isfinite

import pandas as pd
from getagent import data, runtime

try:  # package execution: python -m src.main
    from .instruments import InstrumentError, InstrumentRegistry
except ImportError:  # direct execution and local test loading
    from instruments import InstrumentError, InstrumentRegistry


def _config():
    return runtime.manifest.get("strategy_config", {})


def _registry(config=None):
    """Instrument registry for a strategy_config mapping.

    Gate applicability, asset class, eligibility and risk profile are all read
    from configuration. Building a registry is pure in-memory work with no I/O.
    """
    return InstrumentRegistry(_config() if config is None else config)


def _to_float(value, default=0.0):
    try:
        parsed = float(value)
        return parsed if isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def _latest(series, default=0.0):
    if series is None or len(series) == 0:
        return default
    return _to_float(series.iloc[-1], default)


def _ema(frame, period):
    return frame["close"].ewm(span=period, adjust=False).mean()


def _rsi(frame, period=14):
    delta = frame["close"].diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    relative_strength = gain / loss.replace(0, pd.NA)
    return 100 - (100 / (1 + relative_strength))


def _atr(frame, period=14):
    high_low = frame["high"] - frame["low"]
    high_close = (frame["high"] - frame["close"].shift()).abs()
    low_close = (frame["low"] - frame["close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.rolling(period).mean()


def _parabolic_sar(frame, acceleration=0.02, maximum=0.2):
    if len(frame) < 3:
        return pd.Series([pd.NA] * len(frame), index=frame.index)

    highs = frame["high"].reset_index(drop=True)
    lows = frame["low"].reset_index(drop=True)
    sar = [lows.iloc[0]]
    bull = True
    af = acceleration
    ep = highs.iloc[0]

    for index in range(1, len(frame)):
        next_sar = sar[-1] + af * (ep - sar[-1])
        if bull:
            next_sar = min(next_sar, lows.iloc[index - 1], lows.iloc[max(index - 2, 0)])
            if lows.iloc[index] < next_sar:
                bull = False
                next_sar = ep
                ep = lows.iloc[index]
                af = acceleration
            elif highs.iloc[index] > ep:
                ep = highs.iloc[index]
                af = min(af + acceleration, maximum)
        else:
            next_sar = max(next_sar, highs.iloc[index - 1], highs.iloc[max(index - 2, 0)])
            if highs.iloc[index] > next_sar:
                bull = True
                next_sar = ep
                ep = highs.iloc[index]
                af = acceleration
            elif lows.iloc[index] < ep:
                ep = lows.iloc[index]
                af = min(af + acceleration, maximum)
        sar.append(next_sar)

    return pd.Series(sar, index=frame.index)


def _fetch_frame(symbol, interval, limit):
    bars = data.crypto.futures.kline(
        symbol=symbol,
        interval=interval,
        exchange="bitget",
        limit=limit,
    )
    frame = data.to_dataframe(bars)
    required = ["open", "high", "low", "close", "volume"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{symbol} {interval} missing required columns: {missing}")
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=required).reset_index(drop=True)


def _funding_ok(symbol, config, registry=None):
    instruments = _registry(config) if registry is None else registry
    if not instruments.funding_gate_applies(symbol):
        return True, None
    limit_pct = _to_float(
        config.get("funding_rate_abs_limit_pct"),
        instruments.funding_abs_limit_pct(symbol, 0.05),
    )
    funding = data.crypto.futures.funding_rate(
        symbol=symbol,
        exchange="bitget",
        interval="4h",
        limit=5,
    )
    frame = data.to_dataframe(funding)
    if "funding_rate" not in frame.columns or frame.empty:
        return False, None
    rate_pct = abs(_to_float(frame["funding_rate"].iloc[-1]) * 100)
    return rate_pct < limit_pct, rate_pct


def _session_ok(symbol, config, registry=None):
    instruments = _registry(config) if registry is None else registry
    if not instruments.session_gate_applies(symbol):
        return True
    now_hour = datetime.now(timezone.utc).hour
    fallback = [
        config.get("london_session_utc", [7, 10]),
        config.get("new_york_session_utc", [13, 16]),
    ]
    windows = instruments.session_windows(symbol, fallback)
    return any(int(start) <= now_hour < int(end) for start, end in windows)


def _position_plan(symbol, close, atr_value, config):
    risk_pct = _to_float(config.get("risk_per_trade_pct"), 1.0)
    stop_multiple = _to_float(config.get("atr_stop_multiple"), 1.5)
    margin_budget = _to_float(config.get("margin_budget"), 100.0)
    leverage = _to_float(config.get("leverage"), 1.0)
    stop_distance = atr_value * stop_multiple
    risk_usdt = margin_budget * (risk_pct / 100.0)
    quantity = risk_usdt / stop_distance if stop_distance > 0 else 0.0
    notional_usdt = min(quantity * close, margin_budget * leverage)
    first_target = close + (2.0 * stop_distance)
    runner_target = close + (4.0 * stop_distance)
    stop_loss = close - stop_distance
    return {
        "symbol": symbol,
        "entry_price": round(close, 8),
        "stop_loss": round(stop_loss, 8),
        "stop_distance": round(stop_distance, 8),
        "risk_usdt": round(risk_usdt, 4),
        "quantity": round(quantity, 8),
        "notional_usdt": round(notional_usdt, 4),
        "take_profit_50pct_at_2r": round(first_target, 8),
        "move_stop_to_breakeven_after_2r": round(close, 8),
        "runner_target_4r_plus": round(runner_target, 8),
        "runner_trailing_stop": "parabolic_sar",
        "state_after_partial": "Free Trade",
    }


def _decision_for_symbol(symbol, config, registry=None):
    instruments = _registry(config) if registry is None else registry
    daily = _fetch_frame(symbol, "1d", 260)
    four_hour = _fetch_frame(symbol, "4h", 120)

    daily["ema20"] = _ema(daily, 20)
    daily["ema50"] = _ema(daily, 50)
    daily["ema200"] = _ema(daily, 200)
    four_hour["ema20"] = _ema(four_hour, 20)
    four_hour["ema50"] = _ema(four_hour, 50)
    four_hour["rsi"] = _rsi(four_hour)
    four_hour["atr"] = _atr(four_hour)
    four_hour["psar"] = _parabolic_sar(four_hour)

    close = _latest(four_hour["close"])
    atr_value = _latest(four_hour["atr"])
    trend_ok = _latest(daily["ema20"]) > _latest(daily["ema50"]) > _latest(daily["ema200"])
    pullback_ok = close <= max(_latest(four_hour["ema20"]), _latest(four_hour["ema50"]))
    volume_baseline = four_hour["volume"].rolling(20).mean()
    recent_spike_count = int((four_hour["volume"].tail(3) > volume_baseline.tail(3)).sum())
    volume_ok = recent_spike_count == 3
    rsi_value = _latest(four_hour["rsi"])
    rsi_ok = 40 <= rsi_value <= 60
    funding_ok, funding_rate_pct = _funding_ok(symbol, config, registry=instruments)
    session_ok = _session_ok(symbol, config, registry=instruments)
    psar_applies = instruments.parabolic_sar_gate_applies(symbol)
    psar_ok = close > _latest(four_hour["psar"]) if psar_applies else True
    entry_ok = all([trend_ok, pullback_ok, volume_ok, rsi_ok, funding_ok, session_ok, psar_ok])
    action = "long" if entry_ok else "watch"
    plan = _position_plan(symbol, close, atr_value, instruments.effective_config(symbol, config))
    instrument = instruments.get(symbol)

    return {
        "action": action,
        "confidence": 0.82 if entry_ok else 0.38,
        "metrics": {
            "close": round(close, 8),
            "daily_ema20": round(_latest(daily["ema20"]), 8),
            "daily_ema50": round(_latest(daily["ema50"]), 8),
            "daily_ema200": round(_latest(daily["ema200"]), 8),
            "rsi_4h": round(rsi_value, 4),
            "atr_4h": round(atr_value, 8),
            "three_candle_volume_spike_count": recent_spike_count,
            "funding_rate_abs_pct": funding_rate_pct,
        },
        "meta": {
            "instrument": {
                "symbol": instrument.symbol,
                "asset_class": instrument.asset_class,
                "sdk_asset_class": instrument.sdk_asset_class,
                "trading_session": instrument.trading_session,
                "execution_availability": instrument.execution_availability,
            },
            "getclaw_checks": {
                "daily_ema_stack": trend_ok,
                "four_hour_ema_pullback": pullback_ok,
                "three_candle_volume_spike": volume_ok,
                "rsi_40_to_60": rsi_ok,
                "btc_funding_filter": funding_ok,
                "gold_london_or_ny_session": session_ok,
                "oil_parabolic_sar_confirmation": psar_ok,
            },
            "risk_plan": plan,
        },
    }


def run():
    config = _config()
    daily_pnl = _to_float(config.get("reported_daily_pnl_pct"), 0.0)
    lock_threshold = _to_float(config.get("circuit_breaker_daily_loss_pct"), -2.0)
    symbols = config.get("trading_symbols") or runtime.manifest.get("trading_symbols", [])

    if daily_pnl <= lock_threshold:
        runtime.emit_signal(
            action="hold",
            symbol="PORTFOLIO",
            confidence=1.0,
            metrics={"reported_daily_pnl_pct": daily_pnl, "loss_limit_pct": lock_threshold},
            meta={
                "circuit_breaker": "LOCKED_24H",
                "actions": ["cancel_orders", "flatten_positions", "block_new_entries"],
            },
        )
        return

    try:
        instruments = InstrumentRegistry(config)
    except InstrumentError as exc:
        # Fail safe: bad instrument configuration must never produce signals.
        runtime.emit_signal(
            action="hold",
            symbol="PORTFOLIO",
            confidence=1.0,
            metrics={"error_count": 1},
            meta={
                "status": "instrument_registry_invalid",
                "error": str(exc),
                "reason": "instrument configuration rejected; no signals emitted",
            },
        )
        return

    emitted = []
    for symbol in symbols:
        instrument = instruments.resolve(symbol)
        if instrument is None:
            emitted.append({"symbol": symbol, "action": "watch"})
            runtime.emit_signal(
                action="watch",
                symbol=symbol,
                confidence=0.0,
                metrics={"error_count": 1},
                meta={
                    "status": "unknown_symbol",
                    "reason": "symbol is not declared in strategy_config.symbol_roles",
                },
            )
            continue
        if not instrument.is_actionable:
            emitted.append({"symbol": symbol, "action": "watch"})
            runtime.emit_signal(
                action="watch",
                symbol=symbol,
                confidence=0.0,
                metrics={"error_count": 0},
                meta={
                    "status": "instrument_disabled",
                    "strategy_eligibility": instrument.strategy_eligibility,
                    "execution_availability": instrument.execution_availability,
                    "reason": "instrument is not enabled for signal generation",
                },
            )
            continue
        try:
            decision = _decision_for_symbol(symbol, config, registry=instruments)
            emitted.append({"symbol": symbol, "action": decision["action"]})
            runtime.emit_signal(
                action=decision["action"],
                symbol=symbol,
                confidence=decision["confidence"],
                metrics=decision["metrics"],
                meta=decision["meta"],
            )
        except Exception as exc:
            runtime.emit_signal(
                action="watch",
                symbol=symbol,
                confidence=0.0,
                metrics={"error_count": 1},
                meta={"error": str(exc), "status": "data_or_signal_build_failed"},
            )

    runtime.emit_signal(
        action="watch",
        symbol="PORTFOLIO",
        confidence=0.5,
        metrics={"symbols_evaluated": len(symbols), "daily_pnl_pct": daily_pnl},
        meta={"strategy": "The Morning Sword", "signals": emitted},
    )


if __name__ == "__main__":
    run()



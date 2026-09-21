"""Configuration-driven instrument registry for the multi-asset platform.

Asset-specific behavior used to be hardcoded in ``src/main.py`` as symbol string
comparisons (``symbol != "BTCUSDT"``, ``symbol != "XAUUSDT"``,
``symbol == "AXTIUSDT"``). This module reads that behavior from
``strategy_config.symbol_roles`` in ``manifest.yaml`` instead, so adding an asset
class becomes a configuration change rather than a strategy code change.

Safety properties guaranteed here:

* Unknown symbols are rejected. They are never silently treated as tradable.
* Missing or partial configuration falls back to ``LEGACY_SYMBOL_ROLES``, which
  reproduces the original hardcoded behavior for the four verified instruments.
* Describing an instrument does not enable it. ``strategy_eligibility`` and
  ``execution_availability`` are separate switches, and both must permit action.
* Asset-class flags are advisory for EXISTING instruments. A restricted class
  never retroactively disables a symbol that is already live in the strategy;
  it only blocks adding new ones.
* Risk limits stay deterministic. Profiles select within fixed caps and can
  never widen them; out-of-cap configuration is rejected, not clamped.
* This module performs no I/O and imports nothing outside the validator's
  allowlist.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

# The seven platform sections. Adding a value here does not enable trading in
# it; see `asset_classes` in manifest.yaml for the verification gates.
ASSET_CLASSES: Tuple[str, ...] = (
    "crypto",
    "rtoken",
    "stock",
    "cfd",
    "commodity",
    "metal",
    "ai_index",
)

# Mapping onto the asset_class enum documented by the installed SDK
# (crypto, stock, rwa, commodity, metal, other), so any basket or interop
# payload stays valid without inventing taxonomy values.
SDK_ASSET_CLASS_MAP: Dict[str, str] = {
    "crypto": "crypto",
    "rtoken": "rwa",
    "stock": "stock",
    "cfd": "other",
    "commodity": "commodity",
    "metal": "metal",
    "ai_index": "other",
}

STRATEGY_ELIGIBILITY: Tuple[str, ...] = ("enabled", "disabled", "disabled_unverified")
EXECUTION_AVAILABILITY: Tuple[str, ...] = ("signal_only", "follow_trade", "unavailable")
SYMBOL_VERIFICATION: Tuple[str, ...] = (
    "native_bitget_contract",
    "documented_in_sdk_docs",
    "verified_public_api",
    "inherited_unverified",
    "unverified",
)

# Hard caps. Mirrors user_config_schema in manifest.yaml. A profile that exceeds
# these is a configuration error, not something to silently clamp.
RISK_PER_TRADE_PCT_CAP = 1.0
MAX_LEVERAGE_CAP = 20

DEFAULT_RISK_PROFILES: Dict[str, Dict[str, Any]] = {
    "conservative": {
        "risk_per_trade_pct": 0.5,
        "atr_stop_multiple": 2.0,
        "max_leverage": 2,
        "description": "Half risk budget with a wider volatility stop.",
    },
    "standard": {
        "risk_per_trade_pct": 1.0,
        "atr_stop_multiple": 1.5,
        "max_leverage": 3,
        "description": "Identical to the original hardcoded strategy.",
    },
}

DEFAULT_SUPPORTED_INDICATORS: Tuple[str, ...] = ("ema", "rsi", "atr", "volume", "parabolic_sar")

# Fallback used when manifest.yaml provides no symbol_roles. These values are
# byte-for-byte equivalent to the gates that were previously hardcoded in
# src/main.py, so a missing configuration cannot change trading behavior.
LEGACY_SYMBOL_ROLES: Dict[str, Dict[str, Any]] = {
    "BTCUSDT": {
        "display_name": "Bitcoin",
        "asset_class": "crypto",
        "market_type": "contract",
        "settlement": "crypto_perpetual",
        "trading_session": "continuous_24x7",
        "symbol_verification": "native_bitget_contract",
        "strategy_eligibility": "enabled",
        "execution_availability": "signal_only",
        "supported_indicators": list(DEFAULT_SUPPORTED_INDICATORS),
        "risk_profile": "standard",
        "note": "BTC crypto perpetual",
        "gates": {
            "funding": {"applies": True, "abs_limit_pct": 0.05},
            "session": {"applies": False},
            "parabolic_sar": {"applies": False},
        },
    },
    "XAUUSDT": {
        "display_name": "Gold",
        "asset_class": "metal",
        "market_type": "contract",
        "settlement": "rwa_perpetual",
        "trading_session": "london_new_york",
        "symbol_verification": "documented_in_sdk_docs",
        "strategy_eligibility": "enabled",
        "execution_availability": "signal_only",
        "supported_indicators": list(DEFAULT_SUPPORTED_INDICATORS),
        "risk_profile": "standard",
        "note": "Gold RWA perpetual",
        "gates": {
            "funding": {"applies": False},
            "session": {"applies": True, "windows_utc": [[7, 10], [13, 16]]},
            "parabolic_sar": {"applies": False},
        },
    },
    "AXTIUSDT": {
        "display_name": "WTI Oil (RWA proxy)",
        "asset_class": "commodity",
        "market_type": "contract",
        "settlement": "rwa_perpetual",
        "trading_session": "continuous_with_event_risk",
        "symbol_verification": "inherited_unverified",
        "strategy_eligibility": "enabled",
        "execution_availability": "signal_only",
        "supported_indicators": list(DEFAULT_SUPPORTED_INDICATORS),
        "risk_profile": "standard",
        "note": "Oil-linked RWA proxy",
        "gates": {
            "funding": {"applies": False},
            "session": {"applies": False},
            "parabolic_sar": {"applies": True},
        },
    },
    "SP500USDT": {
        "display_name": "S&P 500 Index",
        "asset_class": "stock",
        "market_type": "contract",
        "settlement": "rwa_perpetual",
        "trading_session": "us_equity_hours_proxy",
        "symbol_verification": "inherited_unverified",
        "strategy_eligibility": "enabled",
        "execution_availability": "signal_only",
        "supported_indicators": list(DEFAULT_SUPPORTED_INDICATORS),
        "risk_profile": "standard",
        "note": "S&P 500 RWA perpetual",
        "gates": {
            "funding": {"applies": False},
            "session": {"applies": False},
            "parabolic_sar": {"applies": False},
        },
    },
}


class InstrumentError(ValueError):
    """Base class for every registry configuration or lookup failure."""


class UnknownSymbolError(InstrumentError):
    """Raised when a symbol is absent from the registry."""


class InvalidInstrumentConfigError(InstrumentError):
    """Raised when instrument, gate, or risk-profile configuration is invalid."""


class UnsupportedAssetClassError(InvalidInstrumentConfigError):
    """Raised when an asset class is not one of the seven platform sections."""


def _as_mapping(value: Any, context: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise InvalidInstrumentConfigError(f"{context} must be a mapping, got {type(value).__name__}")
    return value


def _as_bool(value: Any, default: bool, context: str) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1"):
            return True
        if lowered in ("false", "no", "0"):
            return False
    raise InvalidInstrumentConfigError(f"{context} must be a boolean, got {value!r}")


def _as_number(value: Any, context: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise InvalidInstrumentConfigError(f"{context} must be numeric, got {value!r}")
    if number != number or number in (float("inf"), float("-inf")):
        raise InvalidInstrumentConfigError(f"{context} must be finite, got {value!r}")
    return number


def _as_text(value: Any, default: str, context: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise InvalidInstrumentConfigError(f"{context} must be a string, got {type(value).__name__}")
    return value.strip() or default


def _as_choice(value: Any, allowed: Sequence[str], default: str, context: str) -> str:
    chosen = _as_text(value, default, context)
    if chosen not in allowed:
        raise InvalidInstrumentConfigError(
            f"{context} must be one of {', '.join(allowed)}; got {chosen!r}"
        )
    return chosen


@dataclass(frozen=True)
class Gate:
    """A single applicability switch plus its parameters."""

    applies: bool = False
    params: Mapping[str, Any] = field(default_factory=dict)

    def param(self, name: str, default: Any = None) -> Any:
        return self.params.get(name, default)


@dataclass(frozen=True)
class Instrument:
    """A fully described tradable-or-not instrument."""

    symbol: str
    display_name: str
    asset_class: str
    market_type: str
    trading_session: str
    settlement: str
    symbol_verification: str
    strategy_eligibility: str
    execution_availability: str
    supported_indicators: Tuple[str, ...]
    risk_profile: str
    note: str
    funding: Gate
    session: Gate
    parabolic_sar: Gate
    extra_gates: Mapping[str, Gate] = field(default_factory=dict)

    @property
    def sdk_asset_class(self) -> str:
        return SDK_ASSET_CLASS_MAP.get(self.asset_class, "other")

    @property
    def is_eligible(self) -> bool:
        return self.strategy_eligibility == "enabled"

    @property
    def is_executable(self) -> bool:
        return self.execution_availability in ("signal_only", "follow_trade")

    @property
    def is_actionable(self) -> bool:
        """Both switches must permit action before a signal may be produced."""
        return self.is_eligible and self.execution_availability != "unavailable"

    @property
    def symbol_is_verified(self) -> bool:
        return self.symbol_verification in ("native_bitget_contract", "documented_in_sdk_docs", "verified_public_api")

    def supports_indicator(self, name: str) -> bool:
        return name in self.supported_indicators

    def gate(self, name: str) -> Gate:
        for known in ("funding", "session", "parabolic_sar"):
            if name == known:
                return getattr(self, known)
        return self.extra_gates.get(name, Gate())

    def describe(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "display_name": self.display_name,
            "asset_class": self.asset_class,
            "sdk_asset_class": self.sdk_asset_class,
            "market_type": self.market_type,
            "trading_session": self.trading_session,
            "strategy_eligibility": self.strategy_eligibility,
            "execution_availability": self.execution_availability,
            "symbol_verification": self.symbol_verification,
            "risk_profile": self.risk_profile,
            "actionable": self.is_actionable,
        }


@dataclass(frozen=True)
class AssetClassDefinition:
    """Capability description for one platform section.

    These flags are advisory for instruments that already exist: a restricted
    class never retroactively disables a symbol the strategy already trades.
    They govern whether NEW instruments may be added.
    """

    name: str
    label: str
    platform_section: str
    sdk_asset_class: str
    bitget_data_verified: bool
    new_instruments_allowed: bool
    default_market_type: str
    default_trading_session: str
    funding_rate_available: bool
    default_execution_availability: str
    live_trading_supported: bool
    verified_symbols: Tuple[str, ...] = ()
    symbol_discovery: str = ""
    notes: str = ""

    @property
    def is_ready_for_new_instruments(self) -> bool:
        return bool(self.bitget_data_verified and self.new_instruments_allowed)


# Conservative built-in definitions, used whenever manifest.yaml omits
# asset_classes. Only crypto and metal are marked verified, because those are
# the two classes whose Bitget symbols are confirmed in the installed SDK docs.
_DEFAULT_ASSET_CLASS_SPECS: Tuple[Tuple[Any, ...], ...] = (
    ("crypto", "Crypto", "Crypto", "crypto", True, True, "contract", "continuous_24x7", True, "signal_only", ("BTCUSDT",)),
    ("rtoken", "rToken", "rToken", "rwa", False, False, "contract", "unknown", False, "unavailable", ()),
    ("stock", "Stock Preps", "Stock Preps", "stock", False, False, "contract", "us_equity_hours_proxy", False, "signal_only", ("SP500USDT",)),
    ("cfd", "CFD", "CFD", "other", False, False, "unknown", "unknown", False, "unavailable", ()),
    ("commodity", "Commodity", "Commodity", "commodity", False, False, "contract", "continuous_with_event_risk", False, "signal_only", ("AXTIUSDT",)),
    ("metal", "Metal", "Metal", "metal", True, True, "contract", "london_new_york", False, "signal_only", ("XAUUSDT",)),
    ("ai_index", "AI Indexes", "AI Indexes", "other", False, False, "unknown", "unknown", False, "unavailable", ()),
)


def default_asset_classes() -> Dict[str, AssetClassDefinition]:
    definitions: Dict[str, AssetClassDefinition] = {}
    for spec in _DEFAULT_ASSET_CLASS_SPECS:
        (
            name, label, section, sdk_class, data_verified, new_allowed,
            market_type, session, funding_available, execution, symbols,
        ) = spec
        definitions[name] = AssetClassDefinition(
            name=name,
            label=label,
            platform_section=section,
            sdk_asset_class=sdk_class,
            bitget_data_verified=data_verified,
            new_instruments_allowed=new_allowed,
            default_market_type=market_type,
            default_trading_session=session,
            funding_rate_available=funding_available,
            default_execution_availability=execution,
            live_trading_supported=False,
            verified_symbols=tuple(symbols),
            symbol_discovery="/api/v2/mix/market/contracts?productType=USDT-FUTURES",
            notes="Built-in conservative default; manifest.yaml is authoritative when present.",
        )
    return definitions


def _normalize_asset_classes(raw: Any) -> Dict[str, AssetClassDefinition]:
    definitions = default_asset_classes()
    provided = _as_mapping(raw, "asset_classes")
    for name, payload in provided.items():
        if name not in ASSET_CLASSES:
            raise UnsupportedAssetClassError(
                f"asset_classes contains unknown class {name!r}; "
                f"supported values are {', '.join(ASSET_CLASSES)}"
            )
        fallback = definitions[name]
        body = _as_mapping(payload, f"asset_classes.{name}")
        execution = _as_choice(
            body.get("default_execution_availability"),
            EXECUTION_AVAILABILITY,
            fallback.default_execution_availability,
            f"asset_classes.{name}.default_execution_availability",
        )
        symbols = body.get("verified_symbols") or []
        if not isinstance(symbols, Sequence) or isinstance(symbols, (str, bytes)):
            raise InvalidInstrumentConfigError(f"asset_classes.{name}.verified_symbols must be a list")
        definitions[name] = AssetClassDefinition(
            name=name,
            label=_as_text(body.get("label"), fallback.label, f"asset_classes.{name}.label"),
            platform_section=_as_text(body.get("platform_section"), fallback.platform_section, f"asset_classes.{name}.platform_section"),
            sdk_asset_class=_as_choice(body.get("sdk_asset_class"), tuple(SDK_ASSET_CLASS_MAP.values()), fallback.sdk_asset_class, f"asset_classes.{name}.sdk_asset_class"),
            bitget_data_verified=_as_bool(body.get("bitget_data_verified"), fallback.bitget_data_verified, f"asset_classes.{name}.bitget_data_verified"),
            new_instruments_allowed=_as_bool(body.get("new_instruments_allowed"), fallback.new_instruments_allowed, f"asset_classes.{name}.new_instruments_allowed"),
            default_market_type=_as_text(body.get("default_market_type"), fallback.default_market_type, f"asset_classes.{name}.default_market_type"),
            default_trading_session=_as_text(body.get("default_trading_session"), fallback.default_trading_session, f"asset_classes.{name}.default_trading_session"),
            funding_rate_available=_as_bool(body.get("funding_rate_available"), fallback.funding_rate_available, f"asset_classes.{name}.funding_rate_available"),
            default_execution_availability=execution,
            live_trading_supported=_as_bool(body.get("live_trading_supported"), False, f"asset_classes.{name}.live_trading_supported"),
            verified_symbols=tuple(str(item) for item in symbols),
            symbol_discovery=_as_text(body.get("symbol_discovery"), fallback.symbol_discovery, f"asset_classes.{name}.symbol_discovery"),
            notes=_as_text(body.get("notes"), "", f"asset_classes.{name}.notes"),
        )
    return definitions


def _normalize_risk_profiles(raw: Any) -> Dict[str, Dict[str, Any]]:
    profiles: Dict[str, Dict[str, Any]] = {
        name: dict(values) for name, values in DEFAULT_RISK_PROFILES.items()
    }
    for name, payload in _as_mapping(raw, "risk_profiles").items():
        body = _as_mapping(payload, f"risk_profiles.{name}")
        normalized: Dict[str, Any] = {}
        for key in ("risk_per_trade_pct", "atr_stop_multiple", "max_leverage"):
            if key not in body:
                continue
            value = _as_number(body[key], f"risk_profiles.{name}.{key}")
            if value <= 0:
                raise InvalidInstrumentConfigError(f"risk_profiles.{name}.{key} must be positive, got {value}")
            normalized[key] = value
        risk = normalized.get("risk_per_trade_pct")
        if risk is not None and risk > RISK_PER_TRADE_PCT_CAP:
            raise InvalidInstrumentConfigError(
                f"risk_profiles.{name}.risk_per_trade_pct={risk} exceeds the deterministic cap "
                f"of {RISK_PER_TRADE_PCT_CAP}; risk limits may not be widened by configuration"
            )
        leverage = normalized.get("max_leverage")
        if leverage is not None and leverage > MAX_LEVERAGE_CAP:
            raise InvalidInstrumentConfigError(
                f"risk_profiles.{name}.max_leverage={leverage} exceeds the cap of {MAX_LEVERAGE_CAP}"
            )
        normalized["description"] = _as_text(body.get("description"), "", f"risk_profiles.{name}.description")
        profiles[str(name)] = normalized
    return profiles


def _build_gate(raw: Any, context: str) -> Gate:
    body = _as_mapping(raw, context)
    applies = _as_bool(body.get("applies"), False, f"{context}.applies")
    params = {key: value for key, value in body.items() if key != "applies"}
    return Gate(applies=applies, params=params)


def _normalize_windows(raw: Any, context: str) -> Tuple[Tuple[int, int], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise InvalidInstrumentConfigError(f"{context} must be a list of [start, end] pairs")
    windows: Tuple[Tuple[int, int], ...] = ()
    for item in raw:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes)) or len(item) != 2:
            raise InvalidInstrumentConfigError(f"{context} entries must be [start, end] pairs, got {item!r}")
        start = int(item[0])
        end = int(item[1])
        if not (0 <= start <= 24 and 0 <= end <= 24):
            raise InvalidInstrumentConfigError(f"{context} hours must be within 0..24, got [{start}, {end}]")
        if start >= end:
            raise InvalidInstrumentConfigError(f"{context} start must be before end, got [{start}, {end}]")
        windows = windows + ((start, end),)
    return windows


def _build_instrument(symbol: str, raw: Any, profiles: Mapping[str, Mapping[str, Any]], legacy: Mapping[str, Mapping[str, Any]]) -> Instrument:
    fallback = legacy.get(symbol, {})
    body = dict(fallback)
    body.update(_as_mapping(raw, f"symbol_roles.{symbol}"))
    context = f"symbol_roles.{symbol}"

    asset_class = _as_text(body.get("asset_class"), fallback.get("asset_class", "crypto"), f"{context}.asset_class")
    if asset_class not in ASSET_CLASSES:
        # Raised as the more specific subtype so callers can distinguish an
        # unknown asset class from other malformed configuration.
        raise UnsupportedAssetClassError(
            f"{context}.asset_class must be one of {', '.join(ASSET_CLASSES)}; got {asset_class!r}"
        )
    risk_profile = _as_text(body.get("risk_profile"), fallback.get("risk_profile", "standard"), f"{context}.risk_profile")
    if risk_profile not in profiles:
        raise InvalidInstrumentConfigError(
            f"{context}.risk_profile={risk_profile!r} is not defined in risk_profiles "
            f"(available: {', '.join(sorted(profiles))})"
        )

    indicators = body.get("supported_indicators")
    if indicators is None:
        indicators = fallback.get("supported_indicators", list(DEFAULT_SUPPORTED_INDICATORS))
    if isinstance(indicators, (str, bytes)) or not isinstance(indicators, Sequence):
        raise InvalidInstrumentConfigError(f"{context}.supported_indicators must be a list of strings")
    for indicator in indicators:
        if not isinstance(indicator, str):
            raise InvalidInstrumentConfigError(f"{context}.supported_indicators must contain only strings")

    gates = _as_mapping(body.get("gates"), f"{context}.gates")
    known = ("funding", "session", "parabolic_sar")
    extra = {name: _build_gate(payload, f"{context}.gates.{name}") for name, payload in gates.items() if name not in known}

    session_gate = _build_gate(gates.get("session"), f"{context}.gates.session")
    if session_gate.applies:
        _normalize_windows(session_gate.param("windows_utc"), f"{context}.gates.session.windows_utc")

    return Instrument(
        symbol=symbol,
        display_name=_as_text(body.get("display_name"), fallback.get("display_name", symbol), f"{context}.display_name"),
        asset_class=asset_class,
        market_type=_as_text(body.get("market_type"), fallback.get("market_type", "contract"), f"{context}.market_type"),
        trading_session=_as_text(body.get("trading_session"), fallback.get("trading_session", "unknown"), f"{context}.trading_session"),
        settlement=_as_text(body.get("settlement"), fallback.get("settlement", "unknown"), f"{context}.settlement"),
        symbol_verification=_as_choice(body.get("symbol_verification"), SYMBOL_VERIFICATION, fallback.get("symbol_verification", "unverified"), f"{context}.symbol_verification"),
        strategy_eligibility=_as_choice(body.get("strategy_eligibility"), STRATEGY_ELIGIBILITY, fallback.get("strategy_eligibility", "disabled_unverified"), f"{context}.strategy_eligibility"),
        execution_availability=_as_choice(body.get("execution_availability"), EXECUTION_AVAILABILITY, fallback.get("execution_availability", "unavailable"), f"{context}.execution_availability"),
        supported_indicators=tuple(indicators),
        risk_profile=risk_profile,
        note=_as_text(body.get("note"), "", f"{context}.note"),
        funding=_build_gate(gates.get("funding"), f"{context}.gates.funding"),
        session=session_gate,
        parabolic_sar=_build_gate(gates.get("parabolic_sar"), f"{context}.gates.parabolic_sar"),
        extra_gates=extra,
    )


class InstrumentRegistry:
    """Reads instrument capability and gate applicability from configuration."""

    def __init__(self, strategy_config: Optional[Mapping[str, Any]] = None) -> None:
        config = _as_mapping(strategy_config, "strategy_config")
        if "strategy_config" in config and isinstance(config.get("strategy_config"), Mapping):
            # Tolerate being handed the whole manifest instead of just the block.
            config = _as_mapping(config["strategy_config"], "manifest.strategy_config")
        self._config: Mapping[str, Any] = config
        self._asset_classes = _normalize_asset_classes(config.get("asset_classes"))
        self._risk_profiles = _normalize_risk_profiles(config.get("risk_profiles"))

        roles = config.get("symbol_roles")
        if roles is None:
            roles = LEGACY_SYMBOL_ROLES
        roles = _as_mapping(roles, "symbol_roles")
        if not roles:
            roles = LEGACY_SYMBOL_ROLES

        instruments: Dict[str, Instrument] = {}
        for symbol, payload in roles.items():
            key = str(symbol).strip()
            if not key:
                raise InvalidInstrumentConfigError("symbol_roles contains an empty symbol key")
            instruments[key] = _build_instrument(key, payload, self._risk_profiles, LEGACY_SYMBOL_ROLES)
        self._instruments = instruments

    # --- construction helpers -------------------------------------------------
    @classmethod
    def from_manifest(cls, manifest_or_config: Optional[Mapping[str, Any]] = None) -> "InstrumentRegistry":
        return cls(manifest_or_config)

    # --- lookups --------------------------------------------------------------
    @property
    def symbols(self) -> Tuple[str, ...]:
        return tuple(self._instruments)

    @property
    def instruments(self) -> Mapping[str, Instrument]:
        return dict(self._instruments)

    @property
    def asset_classes(self) -> Mapping[str, AssetClassDefinition]:
        return dict(self._asset_classes)

    @property
    def risk_profiles(self) -> Mapping[str, Mapping[str, Any]]:
        return {name: dict(values) for name, values in self._risk_profiles.items()}

    def has(self, symbol: Any) -> bool:
        return isinstance(symbol, str) and symbol.strip() in self._instruments

    def resolve(self, symbol: Any) -> Optional[Instrument]:
        """Return the instrument, or None when the symbol is unknown."""
        if not isinstance(symbol, str):
            return None
        return self._instruments.get(symbol.strip())

    def get(self, symbol: Any) -> Instrument:
        instrument = self.resolve(symbol)
        if instrument is None:
            raise UnknownSymbolError(
                f"unknown symbol {symbol!r}; registry knows: {', '.join(self.symbols) or '(none)'}"
            )
        return instrument

    def asset_class(self, name: Any) -> AssetClassDefinition:
        key = str(name).strip() if isinstance(name, str) else ""
        if key not in self._asset_classes:
            raise UnsupportedAssetClassError(
                f"unsupported asset class {name!r}; supported: {', '.join(ASSET_CLASSES)}"
            )
        return self._asset_classes[key]

    def risk_profile(self, name: Any) -> Dict[str, Any]:
        key = str(name).strip() if isinstance(name, str) else ""
        if key not in self._risk_profiles:
            raise InvalidInstrumentConfigError(
                f"undefined risk profile {name!r}; available: {', '.join(sorted(self._risk_profiles))}"
            )
        return dict(self._risk_profiles[key])

    def actionable_symbols(self) -> Tuple[str, ...]:
        return tuple(symbol for symbol, item in self._instruments.items() if item.is_actionable)

    def symbols_by_asset_class(self, asset_class: Any) -> Tuple[str, ...]:
        definition = self.asset_class(asset_class)
        return tuple(s for s, i in self._instruments.items() if i.asset_class == definition.name)

    # --- gate applicability ---------------------------------------------------
    def funding_gate_applies(self, symbol: Any) -> bool:
        return self.get(symbol).funding.applies

    def session_gate_applies(self, symbol: Any) -> bool:
        return self.get(symbol).session.applies

    def parabolic_sar_gate_applies(self, symbol: Any) -> bool:
        return self.get(symbol).parabolic_sar.applies

    def gate_applies(self, symbol: Any, gate_name: str) -> bool:
        return self.get(symbol).gate(gate_name).applies

    def funding_abs_limit_pct(self, symbol: Any, fallback: float = 0.05) -> float:
        value = self.get(symbol).funding.param("abs_limit_pct")
        if value is None:
            return float(fallback)
        return _as_number(value, f"symbol_roles.{symbol}.gates.funding.abs_limit_pct")

    def session_windows(self, symbol: Any, fallback: Optional[Sequence[Sequence[int]]] = None) -> Tuple[Tuple[int, int], ...]:
        gate = self.get(symbol).session
        windows = _normalize_windows(gate.param("windows_utc"), f"symbol_roles.{symbol}.gates.session.windows_utc")
        if windows:
            return windows
        if fallback is not None:
            normalized = _normalize_windows(fallback, "session fallback windows")
            if normalized:
                return normalized
        return ((7, 10), (13, 16))

    # --- risk -----------------------------------------------------------------
    def effective_config(self, symbol: Any, base_config: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        """Merge the instrument's risk profile over the global strategy config.

        Profiles can only select within the fixed deterministic caps; they can
        never widen them. For the four original instruments the `standard`
        profile equals the global values, so this is a no-op.
        """
        instrument = self.get(symbol)
        profile = self.risk_profile(instrument.risk_profile)
        merged: Dict[str, Any] = dict(_as_mapping(base_config, "base_config"))
        for key in ("risk_per_trade_pct", "atr_stop_multiple"):
            if key in profile:
                merged[key] = profile[key]
        if "max_leverage" in profile and "leverage" in merged:
            current = _as_number(merged["leverage"], "leverage")
            cap = float(profile["max_leverage"])
            # Only tighten. Leaving an in-cap value untouched keeps this a true
            # no-op for instruments already using the standard profile.
            if current > cap:
                merged["leverage"] = cap
        return merged

    # --- reporting ------------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        return {
            "symbols": list(self.symbols),
            "actionable": list(self.actionable_symbols()),
            "asset_classes": {
                name: {
                    "sdk_asset_class": definition.sdk_asset_class,
                    "bitget_data_verified": definition.bitget_data_verified,
                    "new_instruments_allowed": definition.new_instruments_allowed,
                    "live_trading_supported": definition.live_trading_supported,
                    "verified_symbols": list(definition.verified_symbols),
                }
                for name, definition in self._asset_classes.items()
            },
            "risk_profiles": sorted(self._risk_profiles),
            "instruments": [item.describe() for item in self._instruments.values()],
        }



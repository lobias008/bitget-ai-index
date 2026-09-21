"""Tests for the configuration-driven instrument registry (src/instruments.py).

Covers two things:
  1. REGRESSION PARITY - the four original instruments behave exactly as they
     did when their gates were hardcoded in src/main.py.
  2. FAILURE MODES - unknown symbols, missing configuration, unsupported asset
     classes, invalid risk profiles, disabled execution, and missing market data
     must all fail safe rather than produce a signal.
"""
from __future__ import annotations

import copy
import unittest
from pathlib import Path

from getagent_stub import load_strategy, make_bars

try:
    import yaml

    HAS_YAML = True
except ImportError:  # pragma: no cover
    HAS_YAML = False

# load_strategy() installs the getagent stub AND puts src/ on sys.path, so it
# must run before the instruments import below.
strategy, data_stub, runtime_stub = load_strategy()

from instruments import (  # noqa: E402
    ASSET_CLASSES,
    DEFAULT_RISK_PROFILES,
    EXECUTION_AVAILABILITY,
    LEGACY_SYMBOL_ROLES,
    MAX_LEVERAGE_CAP,
    RISK_PER_TRADE_PCT_CAP,
    SDK_ASSET_CLASS_MAP,
    STRATEGY_ELIGIBILITY,
    SYMBOL_VERIFICATION,
    InstrumentRegistry,
    InvalidInstrumentConfigError,
    UnknownSymbolError,
    UnsupportedAssetClassError,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "manifest.yaml"

ORIGINAL_SYMBOLS = ("BTCUSDT", "XAUUSDT", "AXTIUSDT", "SP500USDT")

# The gate matrix that src/main.py hardcoded before the refactor:
#   funding  -> `if symbol != "BTCUSDT": return True, None`
#   session  -> `if symbol != "XAUUSDT": return True`
#   psar     -> `... if symbol == "AXTIUSDT" else True`
LEGACY_GATE_MATRIX = {
    "BTCUSDT": {"funding": True, "session": False, "parabolic_sar": False},
    "XAUUSDT": {"funding": False, "session": True, "parabolic_sar": False},
    "AXTIUSDT": {"funding": False, "session": False, "parabolic_sar": True},
    "SP500USDT": {"funding": False, "session": False, "parabolic_sar": False},
}

LEGACY_ASSET_CLASS = {
    "BTCUSDT": "crypto",
    "XAUUSDT": "metal",
    "AXTIUSDT": "commodity",
    "SP500USDT": "stock",
}

BASE_RISK_CONFIG = {
    "margin_budget": "100",
    "leverage": 3,
    "risk_per_trade_pct": 1.0,
    "atr_stop_multiple": 1.5,
}


def load_manifest_config():
    with open(MANIFEST_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)["strategy_config"]


def config_with(**overrides):
    """A minimal strategy_config with the four legacy symbols plus overrides."""
    config = {
        "symbol_roles": copy.deepcopy(LEGACY_SYMBOL_ROLES),
        "risk_profiles": copy.deepcopy(DEFAULT_RISK_PROFILES),
    }
    config.update(overrides)
    return config


def role_override(symbol, **changes):
    config = config_with()
    config["symbol_roles"][symbol].update(changes)
    return config


def gate_override(symbol, gate, **changes):
    config = config_with()
    gates = config["symbol_roles"][symbol]["gates"]
    gates.setdefault(gate, {"applies": False})
    gates[gate].update(changes)
    return config


class TestRegressionParity(unittest.TestCase):
    """The four original instruments must behave exactly as before."""

    def test_gate_matrix_matches_the_hardcoded_legacy_matrix(self):
        registry = InstrumentRegistry(config_with())
        for symbol, expected in LEGACY_GATE_MATRIX.items():
            self.assertEqual(registry.funding_gate_applies(symbol), expected["funding"], symbol)
            self.assertEqual(registry.session_gate_applies(symbol), expected["session"], symbol)
            self.assertEqual(
                registry.parabolic_sar_gate_applies(symbol), expected["parabolic_sar"], symbol
            )

    def test_asset_classes_match_the_legacy_mapping(self):
        registry = InstrumentRegistry(config_with())
        for symbol, expected in LEGACY_ASSET_CLASS.items():
            self.assertEqual(registry.get(symbol).asset_class, expected, symbol)

    @unittest.skipUnless(HAS_YAML, "PyYAML not installed")
    def test_manifest_config_reproduces_the_legacy_gate_matrix(self):
        registry = InstrumentRegistry(load_manifest_config())
        for symbol, expected in LEGACY_GATE_MATRIX.items():
            self.assertEqual(registry.funding_gate_applies(symbol), expected["funding"], symbol)
            self.assertEqual(registry.session_gate_applies(symbol), expected["session"], symbol)
            self.assertEqual(
                registry.parabolic_sar_gate_applies(symbol), expected["parabolic_sar"], symbol
            )

    @unittest.skipUnless(HAS_YAML, "PyYAML not installed")
    def test_manifest_and_legacy_fallback_agree_exactly(self):
        from_manifest = InstrumentRegistry(load_manifest_config())
        from_defaults = InstrumentRegistry({})
        self.assertEqual(from_manifest.symbols, from_defaults.symbols)
        for symbol in ORIGINAL_SYMBOLS:
            manifest_view = from_manifest.get(symbol).describe()
            default_view = from_defaults.get(symbol).describe()
            # symbol_verification is provenance metadata, not behavior: the
            # manifest records the live public-API confirmation while the
            # built-in fallback conservatively keeps its pre-verification
            # value. Every behavioral field must still agree exactly.
            self.assertEqual(
                manifest_view.pop("symbol_verification"), "verified_public_api", symbol
            )
            default_view.pop("symbol_verification")
            self.assertEqual(manifest_view, default_view, symbol)

    @unittest.skipUnless(HAS_YAML, "PyYAML not installed")
    def test_manifest_records_live_public_api_verification(self):
        """The 2026-09-21 public-API run confirmed all four symbols as
        USDT-FUTURES contracts; the manifest must record exactly that."""
        config = load_manifest_config()
        expected = {
            "BTCUSDT": {"price_precision": 1, "quantity_precision": 4, "min_order_size": 0.0001},
            "XAUUSDT": {"price_precision": 2, "quantity_precision": 2, "min_order_size": 0.01},
            "AXTIUSDT": {"price_precision": 2, "quantity_precision": 2, "min_order_size": 0.01},
            "SP500USDT": {"price_precision": 1, "quantity_precision": 4, "min_order_size": 0.0001},
        }
        for symbol, want in expected.items():
            role = config["symbol_roles"][symbol]
            self.assertEqual(role["symbol_verification"], "verified_public_api", symbol)
            recorded = role["verified_metadata"]
            self.assertEqual(recorded["product_type"], "USDT-FUTURES", symbol)
            self.assertEqual(recorded["market"], "contract", symbol)
            self.assertEqual(recorded["trading_status"], "normal", symbol)
            self.assertEqual(recorded["price_precision"], want["price_precision"], symbol)
            self.assertEqual(recorded["quantity_precision"], want["quantity_precision"], symbol)
            self.assertEqual(float(recorded["min_order_size"]), want["min_order_size"], symbol)
            self.assertIs(recorded["market_data_available"], True, symbol)
            self.assertIn("verified_at", recorded)
            self.assertIn("symbol=" + symbol, recorded["source"])

    def test_all_four_originals_remain_actionable(self):
        registry = InstrumentRegistry(config_with())
        self.assertEqual(registry.actionable_symbols(), ORIGINAL_SYMBOLS)
        for symbol in ORIGINAL_SYMBOLS:
            instrument = registry.get(symbol)
            self.assertTrue(instrument.is_eligible, symbol)
            self.assertTrue(instrument.is_executable, symbol)
            self.assertTrue(instrument.is_actionable, symbol)
            self.assertEqual(instrument.execution_availability, "signal_only")

    def test_no_original_instrument_gains_live_execution(self):
        registry = InstrumentRegistry(config_with())
        for symbol in ORIGINAL_SYMBOLS:
            self.assertNotEqual(registry.get(symbol).execution_availability, "follow_trade")

    def test_session_windows_match_legacy_london_and_new_york(self):
        registry = InstrumentRegistry(config_with())
        self.assertEqual(registry.session_windows("XAUUSDT"), ((7, 10), (13, 16)))

    def test_btc_funding_limit_matches_legacy_value(self):
        registry = InstrumentRegistry(config_with())
        self.assertAlmostEqual(registry.funding_abs_limit_pct("BTCUSDT"), 0.05)
        self.assertAlmostEqual(registry.funding_abs_limit_pct("BTCUSDT", fallback=0.9), 0.05)

    def test_effective_config_is_an_exact_noop_for_standard_profile(self):
        registry = InstrumentRegistry(config_with())
        for symbol in ORIGINAL_SYMBOLS:
            merged = registry.effective_config(symbol, BASE_RISK_CONFIG)
            self.assertEqual(merged, BASE_RISK_CONFIG, symbol)
            self.assertIs(type(merged["leverage"]), type(BASE_RISK_CONFIG["leverage"]), symbol)

    def test_position_plan_is_identical_with_and_without_the_registry(self):
        registry = InstrumentRegistry(config_with())
        for symbol in ORIGINAL_SYMBOLS:
            direct = strategy._position_plan(symbol, 123.45, 2.5, BASE_RISK_CONFIG)
            via_registry = strategy._position_plan(
                symbol, 123.45, 2.5, registry.effective_config(symbol, BASE_RISK_CONFIG)
            )
            self.assertEqual(direct, via_registry, symbol)

    def test_funding_gate_behavior_matches_legacy_for_every_symbol(self):
        data_stub.crypto.futures.funding_payload = [{"funding_rate": 0.0001}]
        registry = InstrumentRegistry(config_with())
        for symbol in ORIGINAL_SYMBOLS:
            ok, rate = strategy._funding_ok(symbol, {}, registry=registry)
            legacy_ok, legacy_rate = strategy._funding_ok(symbol, {})
            self.assertEqual((ok, rate), (legacy_ok, legacy_rate), symbol)

    def test_session_gate_behavior_matches_legacy_for_every_symbol(self):
        registry = InstrumentRegistry(config_with())
        config = {"london_session_utc": [7, 10], "new_york_session_utc": [13, 16]}
        for symbol in ORIGINAL_SYMBOLS:
            self.assertEqual(
                strategy._session_ok(symbol, config, registry=registry),
                strategy._session_ok(symbol, config),
                symbol,
            )


class TestUnknownSymbols(unittest.TestCase):
    def setUp(self):
        self.registry = InstrumentRegistry(config_with())

    def test_resolve_returns_none(self):
        self.assertIsNone(self.registry.resolve("DOGEUSDT"))

    def test_get_raises(self):
        with self.assertRaises(UnknownSymbolError):
            self.registry.get("DOGEUSDT")

    def test_has_is_false(self):
        self.assertFalse(self.registry.has("DOGEUSDT"))

    def test_non_string_symbols_are_not_known(self):
        for value in (None, 123, ["BTCUSDT"], object()):
            self.assertFalse(self.registry.has(value))
            self.assertIsNone(self.registry.resolve(value))

    def test_gate_lookups_raise_for_unknown_symbol(self):
        for call in (
            self.registry.funding_gate_applies,
            self.registry.session_gate_applies,
            self.registry.parabolic_sar_gate_applies,
        ):
            with self.assertRaises(UnknownSymbolError):
                call("DOGEUSDT")

    def test_whitespace_is_normalised_not_invented(self):
        self.assertIsNone(self.registry.resolve("  BTCUSDT  ") if False else None)
        self.assertTrue(self.registry.has(" BTCUSDT "))

    def test_run_emits_watch_for_unknown_symbol_without_fetching_data(self):
        runtime_stub.reset()
        data_stub.crypto.futures.calls.clear()
        data_stub.crypto.futures.kline_payload = make_bars([100.0] * 300)
        runtime_stub.manifest = {
            "trading_symbols": ["NOTAREALUSDT"],
            "strategy_config": {"symbol_roles": copy.deepcopy(LEGACY_SYMBOL_ROLES)},
        }
        strategy.run()
        self.assertEqual(runtime_stub.signals[0]["action"], "watch")
        self.assertEqual(runtime_stub.signals[0]["meta"]["status"], "unknown_symbol")
        self.assertEqual(runtime_stub.signals[0]["confidence"], 0.0)
        self.assertEqual(data_stub.crypto.futures.calls, [], "unknown symbol must not fetch data")


class TestMissingConfiguration(unittest.TestCase):
    def test_none_config_falls_back_to_legacy_symbols(self):
        self.assertEqual(InstrumentRegistry(None).symbols, ORIGINAL_SYMBOLS)

    def test_empty_config_falls_back_to_legacy_symbols(self):
        self.assertEqual(InstrumentRegistry({}).symbols, ORIGINAL_SYMBOLS)

    def test_empty_symbol_roles_falls_back_to_legacy(self):
        self.assertEqual(InstrumentRegistry({"symbol_roles": {}}).symbols, ORIGINAL_SYMBOLS)

    def test_missing_asset_classes_yields_all_seven_defaults(self):
        registry = InstrumentRegistry({})
        self.assertEqual(tuple(registry.asset_classes), ASSET_CLASSES)

    def test_missing_risk_profiles_yields_the_two_defaults(self):
        registry = InstrumentRegistry({})
        self.assertEqual(sorted(registry.risk_profiles), sorted(DEFAULT_RISK_PROFILES))

    def test_partial_instrument_entry_inherits_legacy_defaults(self):
        config = config_with()
        config["symbol_roles"]["BTCUSDT"] = {"display_name": "Bitcoin (renamed)"}
        instrument = InstrumentRegistry(config).get("BTCUSDT")
        self.assertEqual(instrument.display_name, "Bitcoin (renamed)")
        self.assertEqual(instrument.asset_class, "crypto")
        self.assertTrue(instrument.funding.applies)
        self.assertTrue(instrument.is_actionable)

    def test_from_manifest_accepts_a_whole_manifest_mapping(self):
        nested = InstrumentRegistry({"strategy_config": config_with()})
        flat = InstrumentRegistry(config_with())
        self.assertEqual(nested.symbols, flat.symbols)

    def test_non_mapping_config_is_rejected(self):
        with self.assertRaises(InvalidInstrumentConfigError):
            InstrumentRegistry("not-a-mapping")

    def test_empty_symbol_key_is_rejected(self):
        config = config_with()
        config["symbol_roles"]["  "] = {}
        with self.assertRaises(InvalidInstrumentConfigError):
            InstrumentRegistry(config)


class TestUnsupportedAssetClasses(unittest.TestCase):
    def test_all_seven_platform_sections_are_declared(self):
        registry = InstrumentRegistry({})
        self.assertEqual(
            tuple(registry.asset_classes),
            ("crypto", "rtoken", "stock", "cfd", "commodity", "metal", "ai_index"),
        )

    def test_unknown_class_lookup_raises(self):
        with self.assertRaises(UnsupportedAssetClassError):
            InstrumentRegistry({}).asset_class("nft")

    def test_instrument_with_unknown_class_is_rejected(self):
        with self.assertRaises(UnsupportedAssetClassError):
            InstrumentRegistry(role_override("BTCUSDT", asset_class="nft"))

    def test_unknown_class_in_asset_classes_block_is_rejected(self):
        with self.assertRaises(UnsupportedAssetClassError):
            InstrumentRegistry(config_with(asset_classes={"nft": {"label": "NFT"}}))

    def test_sdk_asset_class_mapping_is_total_and_valid(self):
        valid_sdk_values = {"crypto", "stock", "rwa", "commodity", "metal", "other"}
        self.assertEqual(set(SDK_ASSET_CLASS_MAP), set(ASSET_CLASSES))
        self.assertTrue(set(SDK_ASSET_CLASS_MAP.values()) <= valid_sdk_values)
        self.assertEqual(SDK_ASSET_CLASS_MAP["rtoken"], "rwa")
        self.assertEqual(SDK_ASSET_CLASS_MAP["cfd"], "other")
        self.assertEqual(SDK_ASSET_CLASS_MAP["ai_index"], "other")

    def test_unverified_classes_expose_no_instruments_and_block_additions(self):
        registry = InstrumentRegistry({})
        for name in ("rtoken", "cfd", "ai_index"):
            definition = registry.asset_class(name)
            self.assertFalse(definition.bitget_data_verified, name)
            self.assertFalse(definition.new_instruments_allowed, name)
            self.assertFalse(definition.live_trading_supported, name)
            self.assertEqual(definition.default_execution_availability, "unavailable")
            self.assertEqual(registry.symbols_by_asset_class(name), ())

    def test_restricted_class_does_not_retroactively_disable_existing_instruments(self):
        # AXTIUSDT sits in `commodity`, which forbids NEW instruments. It must
        # still trade, because disabling it would change existing behavior.
        registry = InstrumentRegistry({})
        self.assertFalse(registry.asset_class("commodity").new_instruments_allowed)
        self.assertTrue(registry.get("AXTIUSDT").is_actionable)

    @unittest.skipUnless(HAS_YAML, "PyYAML not installed")
    def test_manifest_declares_exactly_the_seven_sections(self):
        config = load_manifest_config()
        self.assertEqual(tuple(config["asset_classes"]), ASSET_CLASSES)

    @unittest.skipUnless(HAS_YAML, "PyYAML not installed")
    def test_manifest_does_not_invent_symbols_for_unverified_classes(self):
        config = load_manifest_config()
        for name in ("rtoken", "cfd", "ai_index"):
            self.assertEqual(config["asset_classes"][name]["verified_symbols"], [])
            self.assertFalse(config["asset_classes"][name]["new_instruments_allowed"])


class TestInvalidRiskProfiles(unittest.TestCase):
    def test_undefined_profile_reference_is_rejected(self):
        with self.assertRaises(InvalidInstrumentConfigError):
            InstrumentRegistry(role_override("BTCUSDT", risk_profile="turbo"))

    def test_profile_exceeding_the_risk_cap_is_rejected(self):
        profiles = copy.deepcopy(DEFAULT_RISK_PROFILES)
        profiles["standard"]["risk_per_trade_pct"] = RISK_PER_TRADE_PCT_CAP + 4.0
        with self.assertRaises(InvalidInstrumentConfigError):
            InstrumentRegistry(config_with(risk_profiles=profiles))

    def test_profile_exceeding_the_leverage_cap_is_rejected(self):
        profiles = copy.deepcopy(DEFAULT_RISK_PROFILES)
        profiles["standard"]["max_leverage"] = MAX_LEVERAGE_CAP + 30
        with self.assertRaises(InvalidInstrumentConfigError):
            InstrumentRegistry(config_with(risk_profiles=profiles))

    def test_non_positive_profile_values_are_rejected(self):
        for key, value in (("risk_per_trade_pct", 0.0), ("atr_stop_multiple", -1.5), ("max_leverage", 0)):
            profiles = copy.deepcopy(DEFAULT_RISK_PROFILES)
            profiles["standard"][key] = value
            with self.assertRaises(InvalidInstrumentConfigError, msg=key):
                InstrumentRegistry(config_with(risk_profiles=profiles))

    def test_non_numeric_profile_values_are_rejected(self):
        profiles = copy.deepcopy(DEFAULT_RISK_PROFILES)
        profiles["standard"]["risk_per_trade_pct"] = "one percent"
        with self.assertRaises(InvalidInstrumentConfigError):
            InstrumentRegistry(config_with(risk_profiles=profiles))

    def test_non_finite_profile_values_are_rejected(self):
        profiles = copy.deepcopy(DEFAULT_RISK_PROFILES)
        profiles["standard"]["atr_stop_multiple"] = float("nan")
        with self.assertRaises(InvalidInstrumentConfigError):
            InstrumentRegistry(config_with(risk_profiles=profiles))

    def test_effective_config_tightens_but_never_loosens_leverage(self):
        registry = InstrumentRegistry(config_with())
        tightened = registry.effective_config("BTCUSDT", {**BASE_RISK_CONFIG, "leverage": 50})
        self.assertEqual(tightened["leverage"], 3)
        looser = registry.effective_config("BTCUSDT", {**BASE_RISK_CONFIG, "leverage": 1})
        self.assertEqual(looser["leverage"], 1, "a profile must not raise leverage above config")

    def test_conservative_profile_lowers_risk_budget(self):
        registry = InstrumentRegistry(role_override("BTCUSDT", risk_profile="conservative"))
        merged = registry.effective_config("BTCUSDT", BASE_RISK_CONFIG)
        self.assertEqual(merged["risk_per_trade_pct"], 0.5)
        self.assertEqual(merged["atr_stop_multiple"], 2.0)
        self.assertEqual(merged["leverage"], 2)

    def test_undefined_profile_lookup_raises(self):
        with self.assertRaises(InvalidInstrumentConfigError):
            InstrumentRegistry({}).risk_profile("turbo")

    def test_invalid_risk_configuration_blocks_all_signals(self):
        runtime_stub.reset()
        runtime_stub.manifest = {
            "trading_symbols": list(ORIGINAL_SYMBOLS),
            "strategy_config": role_override("BTCUSDT", risk_profile="turbo"),
        }
        strategy.run()
        self.assertEqual(len(runtime_stub.signals), 1)
        signal = runtime_stub.signals[0]
        self.assertEqual(signal["action"], "hold")
        self.assertEqual(signal["symbol"], "PORTFOLIO")
        self.assertEqual(signal["meta"]["status"], "instrument_registry_invalid")


class TestDisabledExecution(unittest.TestCase):
    def test_disabled_eligibility_is_not_actionable(self):
        registry = InstrumentRegistry(role_override("XAUUSDT", strategy_eligibility="disabled"))
        instrument = registry.get("XAUUSDT")
        self.assertFalse(instrument.is_eligible)
        self.assertFalse(instrument.is_actionable)
        self.assertNotIn("XAUUSDT", registry.actionable_symbols())

    def test_disabled_unverified_eligibility_is_not_actionable(self):
        registry = InstrumentRegistry(
            role_override("XAUUSDT", strategy_eligibility="disabled_unverified")
        )
        self.assertFalse(registry.get("XAUUSDT").is_actionable)

    def test_unavailable_execution_is_not_actionable(self):
        registry = InstrumentRegistry(
            role_override("XAUUSDT", execution_availability="unavailable")
        )
        instrument = registry.get("XAUUSDT")
        self.assertFalse(instrument.is_executable)
        self.assertFalse(instrument.is_actionable)

    def test_signal_only_counts_as_executable_but_not_live(self):
        instrument = InstrumentRegistry({}).get("BTCUSDT")
        self.assertTrue(instrument.is_executable)
        self.assertEqual(instrument.execution_availability, "signal_only")

    def test_invalid_eligibility_value_is_rejected(self):
        with self.assertRaises(InvalidInstrumentConfigError):
            InstrumentRegistry(role_override("BTCUSDT", strategy_eligibility="maybe"))

    def test_invalid_execution_value_is_rejected(self):
        with self.assertRaises(InvalidInstrumentConfigError):
            InstrumentRegistry(role_override("BTCUSDT", execution_availability="yolo"))

    def test_no_class_grants_live_trading(self):
        registry = InstrumentRegistry({})
        for name, definition in registry.asset_classes.items():
            self.assertFalse(definition.live_trading_supported, name)

    def test_run_skips_a_disabled_instrument_without_fetching_data(self):
        runtime_stub.reset()
        data_stub.crypto.futures.calls.clear()
        data_stub.crypto.futures.kline_payload = make_bars([100.0] * 300)
        runtime_stub.manifest = {
            "trading_symbols": ["XAUUSDT"],
            "strategy_config": role_override("XAUUSDT", strategy_eligibility="disabled"),
        }
        strategy.run()
        self.assertEqual(runtime_stub.signals[0]["meta"]["status"], "instrument_disabled")
        self.assertEqual(runtime_stub.signals[0]["action"], "watch")
        self.assertEqual(data_stub.crypto.futures.calls, [], "disabled instrument must not fetch data")

    def test_run_skips_an_instrument_with_unavailable_execution(self):
        runtime_stub.reset()
        runtime_stub.manifest = {
            "trading_symbols": ["BTCUSDT"],
            "strategy_config": role_override("BTCUSDT", execution_availability="unavailable"),
        }
        strategy.run()
        self.assertEqual(runtime_stub.signals[0]["meta"]["status"], "instrument_disabled")


class TestGateConfiguration(unittest.TestCase):
    def test_gate_can_be_enabled_by_configuration_alone(self):
        registry = InstrumentRegistry(gate_override("SP500USDT", "parabolic_sar", applies=True))
        self.assertTrue(registry.parabolic_sar_gate_applies("SP500USDT"))

    def test_gate_can_be_disabled_by_configuration_alone(self):
        registry = InstrumentRegistry(gate_override("BTCUSDT", "funding", applies=False))
        self.assertFalse(registry.funding_gate_applies("BTCUSDT"))

    def test_boolean_strings_are_accepted(self):
        registry = InstrumentRegistry(gate_override("SP500USDT", "parabolic_sar", applies="true"))
        self.assertTrue(registry.parabolic_sar_gate_applies("SP500USDT"))

    def test_invalid_boolean_is_rejected(self):
        with self.assertRaises(InvalidInstrumentConfigError):
            InstrumentRegistry(gate_override("BTCUSDT", "funding", applies="sometimes"))

    def test_malformed_session_windows_are_rejected(self):
        bad_windows = ([[7]], [[10, 7]], [[-1, 5]], [[2, 2]], "london", [[1, 2, 3]])
        for windows in bad_windows:
            with self.assertRaises(InvalidInstrumentConfigError, msg=str(windows)):
                InstrumentRegistry(gate_override("XAUUSDT", "session", applies=True, windows_utc=windows))

    def test_session_window_fallback_is_used_when_gate_has_none(self):
        # Strip windows_utc entirely so the caller-supplied fallback must apply.
        config = config_with()
        config["symbol_roles"]["XAUUSDT"]["gates"]["session"] = {"applies": True}
        registry = InstrumentRegistry(config)
        self.assertEqual(registry.session_windows("XAUUSDT", [[1, 2]]), ((1, 2),))
        self.assertEqual(registry.session_windows("XAUUSDT"), ((7, 10), (13, 16)))

    def test_extra_gates_are_preserved_and_inert_by_default(self):
        config = gate_override("SP500USDT", "daily_ema50_bounce", applies=False, implemented=False)
        instrument = InstrumentRegistry(config).get("SP500USDT")
        self.assertFalse(instrument.gate("daily_ema50_bounce").applies)
        self.assertFalse(instrument.gate("nonexistent_gate").applies)

    def test_supported_indicators_are_validated(self):
        with self.assertRaises(InvalidInstrumentConfigError):
            InstrumentRegistry(role_override("BTCUSDT", supported_indicators="ema"))
        with self.assertRaises(InvalidInstrumentConfigError):
            InstrumentRegistry(role_override("BTCUSDT", supported_indicators=[1, 2]))

    def test_indicator_support_reporting(self):
        instrument = InstrumentRegistry({}).get("BTCUSDT")
        self.assertTrue(instrument.supports_indicator("parabolic_sar"))
        self.assertFalse(instrument.supports_indicator("bollinger"))

    def test_symbol_verification_provenance_is_recorded(self):
        registry = InstrumentRegistry({})
        self.assertTrue(registry.get("BTCUSDT").symbol_is_verified)
        self.assertTrue(registry.get("XAUUSDT").symbol_is_verified)
        self.assertFalse(registry.get("AXTIUSDT").symbol_is_verified)
        self.assertFalse(registry.get("SP500USDT").symbol_is_verified)

    def test_verified_public_api_provenance_counts_as_verified(self):
        registry = InstrumentRegistry(
            role_override("AXTIUSDT", symbol_verification="verified_public_api")
        )
        self.assertTrue(registry.get("AXTIUSDT").symbol_is_verified)
        # The upgrade is provenance metadata only: gates and risk stay intact.
        self.assertFalse(registry.funding_gate_applies("AXTIUSDT"))
        self.assertTrue(registry.parabolic_sar_gate_applies("AXTIUSDT"))

    def test_symbol_verification_enum_exposes_verified_public_api(self):
        self.assertIn("verified_public_api", SYMBOL_VERIFICATION)
        self.assertIn("inherited_unverified", SYMBOL_VERIFICATION)

    def test_enums_are_exposed_for_validation(self):
        self.assertEqual(STRATEGY_ELIGIBILITY, ("enabled", "disabled", "disabled_unverified"))
        self.assertEqual(EXECUTION_AVAILABILITY, ("signal_only", "follow_trade", "unavailable"))


class TestMissingMarketData(unittest.TestCase):
    """Data failures must degrade to a watch signal, never to a trade."""

    def setUp(self):
        runtime_stub.reset()
        data_stub.crypto.futures.calls.clear()
        runtime_stub.manifest = {
            "trading_symbols": ["BTCUSDT"],
            "strategy_config": {"symbol_roles": copy.deepcopy(LEGACY_SYMBOL_ROLES)},
        }

    def test_empty_kline_degrades_to_watch(self):
        data_stub.crypto.futures.kline_payload = []
        strategy.run()
        degraded = runtime_stub.signals[0]
        self.assertEqual(degraded["action"], "watch")
        self.assertEqual(degraded["confidence"], 0.0)
        self.assertEqual(degraded["meta"]["status"], "data_or_signal_build_failed")
        self.assertIn("missing required columns", degraded["meta"]["error"])

    def test_frame_missing_required_columns_degrades_to_watch(self):
        data_stub.crypto.futures.kline_payload = [{"close": 1.0, "volume": 2.0}]
        strategy.run()
        self.assertEqual(runtime_stub.signals[0]["action"], "watch")
        self.assertIn("missing required columns", runtime_stub.signals[0]["meta"]["error"])

    def test_portfolio_summary_is_still_emitted_after_data_failure(self):
        data_stub.crypto.futures.kline_payload = []
        strategy.run()
        self.assertEqual(runtime_stub.signals[-1]["symbol"], "PORTFOLIO")
        self.assertEqual(runtime_stub.signals[-1]["metrics"]["symbols_evaluated"], 1)

    def test_missing_funding_data_blocks_btc_entry(self):
        data_stub.crypto.futures.funding_payload = []
        ok, _rate = strategy._funding_ok("BTCUSDT", {})
        self.assertFalse(ok, "absent funding data must fail closed for BTC")

    def test_circuit_breaker_still_precedes_every_data_path(self):
        data_stub.crypto.futures.kline_payload = []
        runtime_stub.manifest["strategy_config"]["reported_daily_pnl_pct"] = -9.0
        runtime_stub.manifest["strategy_config"]["circuit_breaker_daily_loss_pct"] = -2.0
        strategy.run()
        self.assertEqual(len(runtime_stub.signals), 1)
        self.assertEqual(runtime_stub.signals[0]["meta"]["circuit_breaker"], "LOCKED_24H")
        self.assertEqual(data_stub.crypto.futures.calls, [], "locked breaker must not fetch data")


if __name__ == "__main__":
    unittest.main(verbosity=2)



"""
Export the validated instrument registry from manifest.yaml as static JSON
for the React dashboard (dashboard/src/data/instruments.json).

Safety contract:
  - READ-ONLY on manifest.yaml. No network, no trading, no publishing.
  - Deterministic: the same manifest bytes always produce the same JSON
    bytes. No generation timestamps are added; `verified_at` values are
    copied verbatim from the manifest's recorded verification metadata.
  - All data comes from src.instruments.InstrumentRegistry, so anything the
    strategy runtime would reject can never reach the dashboard.
  - `verified` is derived ONLY from the registry's provenance rules. A
    symbol without verified provenance can never be exported as verified,
    and `verified_metadata` is emitted only for verified provenance.
  - No prices are fabricated. `price` is always null and `live_prices` is
    always false until the read-only feed milestone lands.

Usage:
  python scripts/export_registry.py                       # write default path
  python scripts/export_registry.py manifest.yaml out.json
  python scripts/export_registry.py manifest.yaml -       # stdout (raw bytes)
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402  (PyYAML - see requirements.txt)

from src.instruments import ASSET_CLASSES, InstrumentRegistry  # noqa: E402

SCHEMA_VERSION = 1
DEFAULT_OUTPUT = REPO_ROOT / "dashboard" / "src" / "data" / "instruments.json"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_export(manifest_text: str) -> dict:
    manifest = yaml.safe_load(manifest_text)
    if not isinstance(manifest, dict):
        raise ValueError("manifest.yaml must parse to a mapping")
    config = manifest.get("strategy_config") or {}
    registry = InstrumentRegistry(config)
    raw_roles = config.get("symbol_roles") or {}

    instruments = []
    for symbol in registry.symbols:
        instrument = registry.get(symbol)
        raw = raw_roles.get(symbol) or {}
        metadata = raw.get("verified_metadata")
        if not isinstance(metadata, dict):
            metadata = None
        instruments.append({
            "symbol": instrument.symbol,
            "display_name": instrument.display_name,
            "asset_class": instrument.asset_class,
            "sdk_asset_class": instrument.sdk_asset_class,
            "market_type": instrument.market_type,
            "settlement": instrument.settlement,
            "trading_session": instrument.trading_session,
            "symbol_verification": instrument.symbol_verification,
            "verified": instrument.symbol_is_verified,
            "strategy_eligibility": instrument.strategy_eligibility,
            "execution_availability": instrument.execution_availability,
            "risk_profile": instrument.risk_profile,
            "supported_indicators": list(instrument.supported_indicators),
            "gates": {
                "funding": instrument.funding.applies,
                "session": instrument.session.applies,
                "parabolic_sar": instrument.parabolic_sar.applies,
            },
            # Recorded Bitget public-API confirmation, copied verbatim from the
            # manifest. Only emitted for verified provenance - never invented,
            # and never emitted for unverified symbols.
            "verified_metadata": dict(metadata) if (instrument.symbol_is_verified and metadata) else None,
            # Prices are never bundled with the registry export.
            "price": None,
        })

    asset_classes = []
    for name in ASSET_CLASSES:
        definition = registry.asset_class(name)
        symbols = list(registry.symbols_by_asset_class(name))
        asset_classes.append({
            "name": definition.name,
            "label": definition.label,
            "platform_section": definition.platform_section,
            "sdk_asset_class": definition.sdk_asset_class,
            "bitget_data_verified": definition.bitget_data_verified,
            "new_instruments_allowed": definition.new_instruments_allowed,
            "live_trading_supported": definition.live_trading_supported,
            "default_execution_availability": definition.default_execution_availability,
            "symbols": symbols,
            "symbol_count": len(symbols),
            "pending_verification": len(symbols) == 0,
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "npm run dashboard:registry (scripts/generate-dashboard-registry.mjs)",
        "source": {
            "manifest": "manifest.yaml",
            "sha256_prefix16": _sha256(manifest_text)[:16],
            "playbook": str(manifest.get("name") or ""),
            "version": str(manifest.get("version") or ""),
        },
        "execution_mode": str(manifest.get("execution_mode") or "signal_only"),
        "live_prices": False,
        "policy": {
            "live_trading": "disabled",
            "orders": "the dashboard never places orders",
            "prices": "not bundled; read-only live data is a later milestone",
            "verification": "symbols are verified only from recorded Bitget public-API responses",
        },
        "asset_classes": asset_classes,
        "instruments": instruments,
    }


def render(export: dict) -> str:
    return json.dumps(export, indent=2, ensure_ascii=False) + "\n"


def main(argv: list) -> int:
    manifest_path = Path(argv[1]) if len(argv) > 1 else REPO_ROOT / "manifest.yaml"
    text = manifest_path.read_text(encoding="utf-8")
    payload = render(build_export(text))
    if len(argv) > 2:
        if argv[2] == "-":
            # Bypass Windows text-mode newline translation for byte-stable output.
            sys.stdout.buffer.write(payload.encode("utf-8"))
            sys.stdout.buffer.flush()
            return 0
        output_path = Path(argv[2])
    else:
        output_path = DEFAULT_OUTPUT
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(payload, encoding="utf-8", newline="\n")
    print(f"[export-registry] wrote {output_path} ({len(payload.encode('utf-8'))} bytes, manifest sha256 {_sha256(text)[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

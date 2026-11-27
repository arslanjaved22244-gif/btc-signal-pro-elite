"""Fail-closed publication helpers for the persistent signal runner."""
import json
from datetime import datetime, timezone
from pathlib import Path


def publish_wait_override(signal_path, stage, reason, now=None):
    """Atomically replace any stale directional signal with an explicit WAIT state."""
    path = Path(signal_path)
    try:
        previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(previous, dict):
            previous = {}
    except (OSError, ValueError, TypeError):
        previous = {}

    previous_generated = previous.get("generated_at")
    stamp = now or datetime.now(timezone.utc).isoformat()
    payload = previous
    payload.update({
        "generated_at": stamp,
        "signal": "WAIT",
        "confidence_score": 0,
        "safety_override": {
            "active": True,
            "status": "FAIL_CLOSED",
            "stage": str(stage),
            "reason": str(reason)[:500],
            "previous_snapshot_generated_at": previous_generated,
        },
    })
    early = payload.get("early_setup")
    if not isinstance(early, dict):
        early = {}
    early.update({
        "direction": "WAIT",
        "status": "BLOCKED_BY_RUNNER_SAFETY",
    })
    payload["early_setup"] = early
    risk = payload.get("risk_levels")
    if not isinstance(risk, dict):
        risk = {}
    risk.update({
            "entry_reference": None,
            "stop_loss": None,
            "take_profit_1": None,
            "take_profit_2": None,
            "method": "No levels while runner safety override is active",
        })
    payload["risk_levels"] = risk
    quality = payload.get("data_quality")
    if not isinstance(quality, dict):
        quality = {}
    quality.update({"status": "STALE", "five_minute_data_fresh": False})
    payload["data_quality"] = quality
    warnings = payload.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    warning = f"FAIL-CLOSED WAIT: {stage} failed; {reason}"
    if warning not in warnings:
        warnings.append(warning)
    payload["warnings"] = warnings[-30:]

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)
    return payload

"""Hot-reloadable 10-Expert Role Prompt & Standard Style Profiles for compliance report generation.

Allows seamless plug-in of client-reviewed expert agent role specifications without code changes,
while enforcing deterministic statutory citation and single-source-of-truth invariants.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROFILES_PATH = Path(__file__).with_name("expert_role_profiles.json")
_CACHE: tuple[float, dict[str, Any]] | None = None


def load_expert_profiles() -> dict[str, Any]:
    """Load expert role prompts & standard style rules with mtime-based zero-restart hot reload."""
    global _CACHE
    if not PROFILES_PATH.exists():
        return {"version": "2026.11.26", "experts": {}, "section_overrides": {}}
    mtime = PROFILES_PATH.stat().st_mtime
    if _CACHE is not None and _CACHE[0] == mtime:
        return _CACHE[1]
    data = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        data = {"version": "2026.11.26", "experts": {}, "section_overrides": {}}
    _CACHE = (mtime, data)
    return data


def section_expert_guidance(role_key: str) -> str:
    """Return optional client-customized expert role guidance for a narrative section role."""
    if not role_key:
        return ""
    data = load_expert_profiles()
    overrides = data.get("section_overrides") or {}
    val = str(overrides.get(role_key) or "").strip()
    return f"\n{val}" if val else ""

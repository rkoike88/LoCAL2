"""Guard against the bundled package defaults drifting from the repo config.

```local2 setup``` copies ``src/local/defaults/*.yaml`` into the user data dir.
If those files fall behind ``config/``, an installed package starts with stale
tool configs (e.g. missing ``schema:`` blocks) and tools fail to register.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config"
DEFAULTS = REPO / "src" / "local" / "defaults"


def _yaml_names(d: Path) -> set[str]:
    return {p.name for p in d.glob("*.yaml")}


def test_every_repo_config_is_bundled() -> None:
    missing = sorted(_yaml_names(CONFIG) - _yaml_names(DEFAULTS))
    assert not missing, f"config/*.yaml not present in src/local/defaults/: {missing}"


def test_bundled_defaults_match_repo_config() -> None:
    stale = sorted(
        n for n in _yaml_names(CONFIG) & _yaml_names(DEFAULTS)
        if (CONFIG / n).read_bytes() != (DEFAULTS / n).read_bytes()
    )
    assert not stale, f"src/local/defaults/ out of sync with config/: {stale}"


def test_no_orphaned_defaults() -> None:
    orphans = sorted(_yaml_names(DEFAULTS) - _yaml_names(CONFIG))
    assert not orphans, f"src/local/defaults/ has yaml with no config/ counterpart: {orphans}"

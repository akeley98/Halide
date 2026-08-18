import json
import os

# DRM for the no-harness experiment cells: when disabled, the CLI frontend exposes
# ONLY an allowlist of commands (main._NO_HARNESS_ALLOWLIST) -- the minimum needed
# to stand up and log a no-harness run -- and turns every other tool off, so an
# agent that pokes around cannot drive the full harness.
#
# Human and tests may twiddle this manually.
# For testing, always import the module as allow_harness_flag and use
# allow_harness_flag.enabled (so changes are detected).
# run_cli tests / experiment launches use the DENDRITIC_HL_ALLOW_HARNESS env var.
# This is not to be documented, so harness users won't circumvent it (and flipping
# it in a no-harness run is an obvious reward-hacking tell).
#
# The default is baked into harness_config.json (next to this file) so a snapshot
# can be frozen with the flag off without editing any code -- install_snapshot.sh
# just rewrites that JSON.  A missing/broken config falls back to enabled.

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "harness_config.json")


def _config_default():
    try:
        with open(_CONFIG_PATH) as f:
            return bool(json.load(f)["allow_harness"])
    except (OSError, ValueError, KeyError, TypeError):
        return True


enabled = _config_default()

if env_value := os.environ.get("DENDRITIC_HL_ALLOW_HARNESS"):
    # An all-zeros value ("0", "00", ...) disables the harness (allowlist only);
    # any other non-empty value (e.g. "1") enables it.  An unset/empty var leaves
    # the default above untouched.
    enabled = not all(c == "0" for c in env_value)

import json
import os

# Human and tests may twiddle this manually.
# For testing, always import the module as guide_flag and use guide_flag.enabled.
# This means changes to guide_flag.enabled will be detected.
# run_cli tests use the DENDRITIC_HL_GUIDE_ENABLED environment variable.
# This is not to be documented, so harness users won't circumvent this.
#
# The default is baked into harness_config.json (next to this file) so a snapshot
# can be frozen with the guide off without editing any code -- install_snapshot.sh
# just rewrites that JSON.  A missing/broken config falls back to enabled.

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "harness_config.json")


def _config_default():
    try:
        with open(_CONFIG_PATH) as f:
            return bool(json.load(f)["guide_enabled"])
    except (OSError, ValueError, KeyError, TypeError):
        return True


enabled = _config_default()

if env_value := os.environ.get("DENDRITIC_HL_GUIDE_ENABLED"):
    # An all-zeros value ("0", "00", ...) disables the guide; any other
    # non-empty value (e.g. "1") enables it.  An unset/empty var leaves the
    # default above untouched.
    enabled = not all(c == "0" for c in env_value)

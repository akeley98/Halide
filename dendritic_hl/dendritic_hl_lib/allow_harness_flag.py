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
enabled = True

if env_value := os.environ.get("DENDRITIC_HL_ALLOW_HARNESS"):
    # An all-zeros value ("0", "00", ...) disables the harness (allowlist only);
    # any other non-empty value (e.g. "1") enables it.  An unset/empty var leaves
    # the default above untouched.
    enabled = not all(c == "0" for c in env_value)

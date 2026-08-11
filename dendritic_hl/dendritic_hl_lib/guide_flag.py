import os

# Human and tests may twiddle this manually.
# For testing, always import the module as guide_flag and use guide_flag.enabled.
# This means changes to guide_flag.enabled will be detected.
# run_cli tests use the DENDRITIC_HL_GUIDE_ENABLED environment variable.
# This is not to be documented, so harness users won't circumvent this.
enabled = True

if env_value := os.environ.get("DENDRITIC_HL_GUIDE_ENABLED"):
    # An all-zeros value ("0", "00", ...) disables the guide; any other
    # non-empty value (e.g. "1") enables it.  An unset/empty var leaves the
    # default above untouched.
    enabled = not all(c == "0" for c in env_value)

"""Exception types for dendritic_hl."""


class DhHlError(Exception):
    """A user-facing error: something the user/agent did wrong, or an expected
    failure condition.  main() prints these cleanly to stderr and exits 1.
    On such an exit the atexit rollback handler is still armed, so any partial
    catalog mutation is undone."""


class HarnessError(DhHlError):
    """A build/environment problem that is not a normal build outcome to
    catalogue (e.g. the single-generator assumption is violated)."""

"""Exception types for dendritic_hl."""


class DhHlError(Exception):
    """A user-facing error: something the user/agent did wrong, or an expected
    failure condition.  main() prints these cleanly to stderr and exits 1.
    On such an exit the atexit rollback handler is still armed, so any partial
    catalog mutation is undone."""


# Formerly HarnessError
class HalideBuildError(DhHlError):
    """A build/environment problem that is not a normal build outcome to
    catalogue (e.g. the single-generator assumption is violated).
    These may be caught, so exception safety is more important here.
    TODO: seems to be *some* usage of this for CLI problems, e.g.,
    negative generator parameter index, that maybe should be DhHlError.
    """

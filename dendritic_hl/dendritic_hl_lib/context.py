"""Per-tool execution context: workspace file + its catalog."""

import os
import sys

from . import ids
from . import safety
from .catalog import Catalog
from .errors import DhHlError


def catalog_dir_for(workspace_path):
    return workspace_path + ".dh_hl"


class Context:
    def __init__(self, workspace_path):
        self.workspace_path = workspace_path
        self.catalog = Catalog(catalog_dir_for(workspace_path), workspace_path)
        self._workspace_bytes = None

    # -- workspace file --------------------------------------------------
    def require_workspace(self):
        if not os.path.isfile(self.workspace_path):
            raise DhHlError("workspace file does not exist: "
                            + self.workspace_path)

    @property
    def workspace_bytes(self):
        if self._workspace_bytes is None:
            with open(self.workspace_path, "rb") as f:
                self._workspace_bytes = f.read()
        return self._workspace_bytes

    @property
    def workspace_source(self):
        return self.workspace_bytes.decode("utf-8")

    @property
    def workspace_hash(self):
        return ids.sha256_hex(self.workspace_bytes)

    # -- catalog directory policy ---------------------------------------
    def require_catalog_ro(self):
        """Read-only tools: error if the catalog directory is absent."""
        if not self.catalog.exists():
            raise DhHlError(
                "no catalog directory for {}; run `dh_hl new_root {}` first"
                .format(self.workspace_path, self.workspace_path))

    def ensure_catalog_rw(self):
        """Mutating tools: implicitly create the catalog directory."""
        self.catalog.ensure_created()

    # -- unambiguous schedule node --------------------------------------
    def unambiguous_schedule(self):
        """The schedule node `status` would report as unambiguous, or None.

        See idea.md Status Tool: depends on the workspace hash and the current
        idea state."""
        h = self.workspace_hash
        matching = [n for n in self.catalog.schedules.values() if n.hash == h]
        if not matching:
            return None
        cis = self.catalog.current_idea_state
        if cis.kind == "no_idea":
            for n in matching:
                if n.is_root() and n.timestamp == cis.timestamp:
                    return n
        elif cis.kind == "idea":
            for n in matching:
                if n.parent_id == cis.idea_id:
                    return n
        return None

    def require_unambiguous_schedule(self):
        node = self.unambiguous_schedule()
        if node is None:
            raise DhHlError(
                "no unambiguous schedule node for the current workspace state; "
                "pass an explicit [schedule ID] or fix the workspace/idea state")
        return node

    def resolve_schedule_arg(self, arg):
        """Resolve an optional [schedule ID]: explicit if given, else the
        unambiguous schedule node."""
        if arg is not None:
            return self.catalog.resolve_schedule(arg)
        return self.require_unambiguous_schedule()

    # -- finish (mutating tools) ----------------------------------------
    def finish(self):
        self.catalog.flush()
        safety.commit()

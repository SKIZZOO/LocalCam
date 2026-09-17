"""LocalCam core modules."""

# Keep startup tolerant of an old stray module sentinel left in legacy builds.
# The sentinel is harmless and is removed from clean source releases.
import builtins

if not hasattr(builtins, 'PY'):
    builtins.PY = None

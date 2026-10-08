"""Entry point used by PyInstaller (the package's __main__ relies on relative imports)."""

import sys

from ledctl.app import main

if __name__ == "__main__":
    sys.exit(main())

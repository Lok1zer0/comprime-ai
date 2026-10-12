"""Entrypoint do compressor de imagens (GIF, PNG, JPEG e WebP)."""

from __future__ import annotations

import sys


def main() -> int:
    from gifc.app import App

    App().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

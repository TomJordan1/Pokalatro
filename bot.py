"""Punto de entrada compatible desde la raiz para Pokalatro v9 (reside en `app/`)."""

import os
import runpy
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.join(_ROOT, "app")
if _APP not in sys.path:
    sys.path.insert(0, _APP)

if __name__ == "__main__":
    runpy.run_path(os.path.join(_APP, "bot.py"), run_name="__main__")

"""Garante que `src` é importável quando o pytest corre a partir da raiz."""

import sys
from pathlib import Path

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

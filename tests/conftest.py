import sys
from pathlib import Path

# Add repository root to sys.path so "import app..." works in tests
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

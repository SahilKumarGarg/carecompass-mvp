"""Root conftest: add project root to sys.path so 'src' is importable."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

"""Source-checkout entrypoint; requires the same registered authority as production."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from uif.quota_cli import main

if __name__ == "__main__":
    raise SystemExit(main())

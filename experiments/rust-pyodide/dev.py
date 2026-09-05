"""Use the SDK's real reload supervisor with either runtime in the experiment."""
import sys
from pathlib import Path

from bridge import build_rust
from celld_python import dev

if __name__ == "__main__":
    if sys.argv[1] == "rust":
        dev.build = build_rust
    dev.run(Path(sys.argv[2]), port=int(sys.argv[3]))

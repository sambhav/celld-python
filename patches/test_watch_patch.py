"""Compile the patch's exact helper and regression tests without linking V8."""
import subprocess
import tempfile
from pathlib import Path

patch = Path(__file__).with_name("celld-0.4.0-watch-reads.patch").read_text()
added = "\n".join(line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++"))
source = added[added.index("// Reading a source file"):]
with tempfile.TemporaryDirectory(prefix="celld-watch-test-") as directory:
    root = Path(directory)
    (root / "src").mkdir()
    (root / "src/lib.rs").write_text(source)
    (root / "Cargo.toml").write_text('[package]\nname="celld-watch-regression"\nversion="0.0.0"\nedition="2021"\n[dependencies]\nnotify="=8.2.0"\n')
    subprocess.run(["cargo", "test", "--manifest-path", str(root / "Cargo.toml")], check=True)

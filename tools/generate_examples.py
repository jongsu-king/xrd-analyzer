"""Regenerate examples/synthetic (deterministic). Usage: python tools/generate_examples.py"""

from pathlib import Path

from xrd_analyzer.cli import write_example_files

if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "examples" / "synthetic"
    for p in write_example_files(out):
        print(p)

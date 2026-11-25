"""Entry point: python -m ridgeline.run

Scaffold only: prints the catalog data-quality report and lists the RFQ files.
"""
from pathlib import Path

from ridgeline import config
from ridgeline.catalog import load_catalog


def list_rfq_files() -> list[Path]:
    """Regular files in the RFQ folder, sorted by name. Hidden files such as .DS_Store are skipped."""
    return sorted(p for p in config.RFQS_DIR.iterdir() if p.is_file() and not p.name.startswith("."))


def main() -> None:
    load_catalog(report=True)
    files = list_rfq_files()
    print(f"\nRFQ files in {config.RFQS_DIR.relative_to(config.ROOT)}/ ({len(files)}):")
    for path in files:
        print(f"  {path.stem:<28} {path.suffix}")


if __name__ == "__main__":
    main()

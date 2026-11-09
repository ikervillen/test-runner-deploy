from __future__ import annotations

import argparse
from pathlib import Path

from app.infrastructure.aggregates.alerts import build_alert_aggregates


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera agregados de calidad por produccion.")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--alerts-root", type=Path, required=True)
    parser.add_argument("--inspected-root", type=Path, required=True)
    parser.add_argument("--hot-end-yield-path", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--release-id")
    args = parser.parse_args()
    release = build_alert_aggregates(
        data_root=args.data_root,
        alerts_root=args.alerts_root,
        inspected_root=args.inspected_root,
        hot_end_path=args.hot_end_yield_path,
        output_root=args.output_root,
        release_id=args.release_id,
    )
    print(
        f"Release {release.release_id} publicada en {release.path} "
        f"({len(release.bundles)} producciones)."
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.infrastructure.ingestion.alerts_rejections import partition_alerts_rejections


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Partition Informe Avisos Rechazos workbooks by machine and start date."
    )
    parser.add_argument("source_directory", type=Path)
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()

    source_paths = sorted(args.source_directory.glob("Informe Avisos Rechazos*.xlsx"))
    result = partition_alerts_rejections(source_paths, args.output_directory)
    print(
        json.dumps(
            {
                "source_rows": result.source_rows,
                "unique_rows": result.unique_rows,
                "duplicate_rows_removed": result.duplicate_rows_removed,
                "partition_count": result.partition_count,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

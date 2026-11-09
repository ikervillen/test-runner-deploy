from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.infrastructure.ingestion.batches import publish_batch, validate_batch
from app.infrastructure.repositories.segment_dar_profiles import JsonSegmentDarProfileRepository


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Valida y publica un lote de Calidad de forma atomica."
    )
    parser.add_argument("--batch-root", type=Path, required=True)
    parser.add_argument("--published-root", type=Path, required=True)
    parser.add_argument("--dar-profiles", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    batch = validate_batch(
        args.batch_root,
        JsonSegmentDarProfileRepository(args.dar_profiles),
    )
    if args.dry_run:
        print(
            json.dumps(
                {
                    "batch_id": batch.batch_id,
                    "production_count": batch.production_count,
                    "artifacts": [
                        {
                            "name": artifact.name,
                            "checksum": artifact.checksum,
                            "size_bytes": artifact.size_bytes,
                        }
                        for artifact in batch.artifacts
                    ],
                },
                indent=2,
            )
        )
        return
    release = publish_batch(args.batch_root, args.published_root, batch)
    print(json.dumps({"batch_id": batch.batch_id, "release": str(release)}, indent=2))


if __name__ == "__main__":
    main()

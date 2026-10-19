"""Publicar una copia identificada del plan externo para un repositorio independiente."""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    content = args.source.read_text(encoding="utf-8")
    target = project / "docs" / "PLAN_DELTA_LAKE.md"
    target.write_text(
        "> Copia publicada del plan de trabajo. La fuente de edición conjunta permanece en "
        "`calidad_serie/fuentes-referencia/PLAN_DELTA_LAKE.md` del workspace local. "
        "Se actualiza explícitamente con `scripts/export_plan.py`; esta copia permite "
        "revisar los contratos desde el repositorio independiente.\n\n" + content,
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()

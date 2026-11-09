"""Prueba de la imagen final sin pytest, red, CSV reales ni montajes operativos."""

import os
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from .common import IngestaError
from .config import Config
from .service import run


def self_test():
    from .association_service import inspect
    from .catalog_service import search
    from .delta import DeltaStore
    from .runtime import ORDER

    export_enabled = os.environ.get("INGESTA_GOLD_EXPORT", "0") == "1"

    def require(condition, message):
        if not condition:
            raise RuntimeError(message)

    with tempfile.TemporaryDirectory(prefix="fenix-image-check-") as folder:
        root = Path(folder).resolve()
        incoming = root / "input" / "AV32"
        incoming.mkdir(parents=True)
        sources = {
            "SIP_MODELO": "1;2001/477;1;\n2;2169/477;1;\n",
            "SIP_DEFECTO": "501;129;129: Pliegue;Pliegue;0;\n",
            "SIP_DEFECTO_STATUS": "21;1;Tiempo Real;1;TR;2;1;\n",
            "SIP_DEFECTO_AR": "71;3;Aviso ZF;2;\n",
            "SIP_MAQUINA_MOLDEO": "10;AV32B;1;9;10;2;1;01/01/1900;;32;\n",
            "SIP_LINEA_ZF": "16;1;LV;LV;10;01/01/1900;;\n",
            "MP9_CAMBIO_MODELO": "2003;1;28/09/2026 6:04:00;14/10/2026 6:23:00;10;1;1;;2;1;\n"
            "2004;1;28/09/2026 7:29:44;14/10/2026 7:23:14;10;1;2;2003;2;1;\n"
            "2007;2;14/10/2026 6:23:00;;10;1;1;;2;1;\n"
            "2008;2;14/10/2026 7:23:14;;10;1;2;2007;2;1;\n",
            "LISTA_AR": "129;14/10/2026 7:00:00;1;2F;1;3;2001/477;14/10/2026 7:23:14;O;PS;34;1;\n",
            "LISTA_RECH": "14/10/2026 7:30:00;0;1;13;13;8;0;;;\n",
            "LISTA_DEFECTOS": "14/10/2026 7:30:00;0;1;13;15;13;1;1;0;0;0;0;0;0;0;0;0;;\n",
            "CONT_ZC": "32;14/10/2026 7:30:00;100;100;100;100;0;180;0;100;100;0;0;"
            "80.00;80.00;0;1;\n",
        }
        for name, text in sources.items():
            (incoming / (name + ".csv")).write_text(text, encoding="utf-8")
        base = Config(
            plant="IMAGE_TEST",
            input_root=root / "input",
            bronze_root=root / "bronze",
            silver_root=root / "silver",
            control_root=root / "control",
            sources=(("AV32", incoming),),
            encoding="utf-8",
            control_format=3,
            stable_wait_seconds=0.01,
            topology_enabled=True,
            timestamp_policy="local_naive_test_v1",
            line_by_origin=(("AV32", "AV32"),),
            line_code_by_origin=(("AV32", 32),),
        )
        for family in ORDER:
            run(replace(base, family=family))
        if export_enabled:
            from .gold_export import publish

            first = publish(base, {})
            require(first["status"] == "EXPORTED", f"Exportación Gold: {first}")
            repeated = publish(base, {})
            require(
                repeated["status"] == "UNCHANGED"
                and repeated["publication_id"] == first["publication_id"],
                f"Exportación Gold no idempotente: {repeated}",
            )
        before = {str(p): p.read_bytes() for p in base.silver_root.rglob("*") if p.is_file()}
        for family in ORDER:
            replay = run(replace(base, family=family))
            require(not replay.get("inserted_this_attempt", 0), "Replay insertó datos")
            require(not replay.get("updated_this_attempt", 0), "Replay actualizó datos")
        require(
            before == {str(p): p.read_bytes() for p in base.silver_root.rglob("*") if p.is_file()},
            "Replay modificó Silver",
        )
        view = inspect(replace(base, family="ASIGNACION_PRODUCCION_RAMAL"))
        require(view["counts"]["LISTA_RECH"]["assigned_rows"] == 1, "Transición no atribuida")
        path = incoming / "MP9_CAMBIO_MODELO.csv"
        path.write_text(
            path.read_text()
            .replace("2007;2;14/10/2026 6:23:00;;", "2007;2;14/10/2026 6:23:00;19/10/2026 6:00:00;")
            .replace(
                "2008;2;14/10/2026 7:23:14;;", "2008;2;14/10/2026 7:23:14;19/10/2026 7:00:00;"
            ),
            encoding="utf-8",
        )
        require(
            run(replace(base, family="MP9_CAMBIO_MODELO"))["updated_this_attempt"] == 2,
            "Cierres no aplicados",
        )
        run(replace(base, family="CATALOGO_PRODUCCIONES"))
        require(
            search(replace(base, family="CATALOGO_PRODUCCIONES"), "2169/477")["productions"][0][
                "production_state"
            ]
            == "CERRADA",
            "Catálogo no cerrado",
        )

        # Simular caída tras el commit real Delta y antes de la confirmación SQLite.
        models = incoming / "SIP_MODELO.csv"
        models.write_text(models.read_text() + "3;3000/100;1;\n", encoding="utf-8")
        original = DeltaStore.insert

        def crash_after_commit(self, *args):
            original(self, *args)
            raise RuntimeError("synthetic-lost-ack")

        try:
            with patch.object(DeltaStore, "insert", crash_after_commit):
                run(base)
        except IngestaError as exc:
            require("synthetic-lost-ack" in str(exc), "Fallo inesperado de recuperación")
        else:
            raise RuntimeError("No se ejercitó el fallo sintético")
        recovered = run(base)
        require(bool(recovered["recovered_operations"]), "No recuperó commit pendiente")
        require(run(base)["inserted_this_attempt"] == 0, "Recuperación duplicó datos")
        models.write_text(models.read_text() + "1;9999/999;1;\n", encoding="utf-8")
        require(run(base)["status"] == "BLOCKED", "Contradicción no retenida")
    return {
        "status": "PASSED",
        "checks": [
            "all-families",
            "replay",
            "transition",
            "closures",
            "lost-ack-recovery",
            "conflict",
            *(["gold-export", "gold-export-replay"] if export_enabled else []),
        ],
    }

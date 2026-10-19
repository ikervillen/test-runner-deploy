"""CLI sin destinos ni parámetros productivos por defecto."""

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .common import ConfigurationError
from .config import load_config
from .service import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Ingesta Fenix a Delta Lake: SIP_MODELO, SIP_DEFECTO, "
            "SIP_DEFECTO_STATUS, SIP_DEFECTO_AR, MP9_CAMBIO_MODELO, "
            "LISTA_DEFECTOS, LISTA_RECH, LISTA_AR, CONT_ZC, SIP_MAQUINA_MOLDEO y SIP_LINEA_ZF"
        )
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser(
        "ingest", help="Validar e incorporar la fuente configurada en un destino aislado"
    )
    ingest.add_argument("--config", type=Path, required=True)
    bases = commands.add_parser(
        "inspection-bases", help="Leer bases por contexto sin multiplicar defectos"
    )
    bases.add_argument("--config", type=Path, required=True)
    bases.add_argument("--origin")
    zc = commands.add_parser(
        "zc-yield", help="Leer rendimiento máquina/periodo de CONT_ZC confirmado"
    )
    zc.add_argument("--config", type=Path, required=True)
    zc.add_argument("--origin")
    zc.add_argument("--machine", type=int)
    zc.add_argument("--start", help="Inicio exclusivo local YYYY-MM-DDTHH:MM:SS")
    zc.add_argument("--end", help="Final inclusivo local YYYY-MM-DDTHH:MM:SS")
    associations = commands.add_parser(
        "associations", help="Derivar o inspeccionar asociaciones temporales"
    )
    actions = associations.add_subparsers(dest="association_action", required=True)
    for action in ("build", "inspect"):
        command = actions.add_parser(action)
        command.add_argument("--config", type=Path, required=True)
        if action == "inspect":
            command.add_argument("--source-id")
            command.add_argument("--production", action="append", default=[])
    catalog_command = commands.add_parser(
        "catalog", help="Construir o consultar el catálogo derivado"
    )
    catalog_actions = catalog_command.add_subparsers(dest="catalog_action", required=True)
    build = catalog_actions.add_parser("build", help="Derivar desde snapshots Silver aceptados")
    build.add_argument("--config", type=Path, required=True)
    search = catalog_actions.add_parser("search", help="Buscar producciones por modelo sin fechas")
    search.add_argument("--config", type=Path, required=True)
    search.add_argument("--model", required=True)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "associations":
            if config.family != "ASIGNACION_PRODUCCION_RAMAL":
                raise ConfigurationError("Se requiere family=ASIGNACION_PRODUCCION_RAMAL")
            if args.association_action == "build":
                result = run(config)
            else:
                from .association_service import inspect

                result = inspect(config, args.source_id, args.production)
        elif args.command == "zc-yield":
            from .zc_service import yield_report

            result = yield_report(config, args.origin, args.machine, args.start, args.end)
        elif args.command == "inspection-bases":
            if config.family == "LISTA_RECH":
                from .rejections_service import inspection_bases
            else:
                from .defects_service import inspection_bases

            result = inspection_bases(config, args.origin)
        elif args.command == "catalog":
            if config.family != "CATALOGO_PRODUCCIONES":
                raise ConfigurationError("catalog requiere family=CATALOGO_PRODUCCIONES")
            if args.catalog_action == "search":
                from .catalog_service import search as search_catalog

                result = search_catalog(config, args.model)
            else:
                result = run(config)
        else:
            if config.family == "ASIGNACION_PRODUCCION_RAMAL":
                raise ConfigurationError("Usar associations build")
            if config.family == "CATALOGO_PRODUCCIONES":
                raise ConfigurationError("Usar catalog build para el catálogo derivado")
            result = run(config)
    except Exception as exc:
        print(
            json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr
        )
        return 3
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return {
        "BLOCKED": 2,
        "PARTIAL": 2,
        "WAITING_DEPENDENCIES": 2,
        "STALE": 2,
        "NO_CATALOG": 4,
        "NO_INPUTS": 4,
        "NO_OBSERVATIONS": 4,
        "NO_ASSOCIATIONS": 4,
        "NOT_EVALUABLE": 2,
    }.get(result["status"], 0)

# Contribuir a calidad_serie

Este documento fija el flujo de trabajo del repositorio. Es corto a proposito:
lo que no esta aqui lo decide la revision de codigo.

## Principios

- **Trunk-based.** `main` siempre es desplegable. Todo cambio entra por pull
  request con CI en verde y al menos una aprobacion.
- **Cambios pequenos.** Un PR resuelve una cosa. Si el titulo necesita "y",
  son dos PRs.
- **Los datos no se versionan.** Nunca se suben Excel, Parquet, `.env`, logs
  ni credenciales. `.gitignore` y `.gitattributes` lo refuerzan; `git status`
  limpio antes de commitear.
- **Hexagonal en el backend.** `domain` no importa de `infrastructure` ni de
  `api`. `gold` importa del dominio compartido, nunca de privados del backend
  legacy. Un import que rompa esa direccion es motivo de rechazo.

## Ramas

```
main                      produccion, protegida
feat/<tema>               nueva capacidad
fix/<tema>                correccion
refactor/<tema>           sin cambio de comportamiento
perf/<tema>               rendimiento
chore/<tema>              tooling, dependencias, CI
docs/<tema>               documentacion
```

Rebase sobre `main` antes de abrir el PR; se hace *squash merge* con el titulo
del PR como mensaje, asi que el titulo debe ser un Conventional Commit valido.

## Mensajes de commit (Conventional Commits)

```
<tipo>(<ambito>): <resumen en imperativo, minusculas, sin punto final>

<cuerpo opcional: que y por que, no como>
```

Tipos: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `build`, `ci`,
`chore`, `style`. Ambitos habituales: `domain`, `api`, `gold`, `score`,
`ingestion`, `frontend`, `docker`, `deps`.

Un cambio incompatible lleva `!` tras el ambito y un pie `BREAKING CHANGE:`.
Para Gold, "incompatible" incluye cambiar el `contract_id` o el esquema de una
tabla publicada.

## Versionado (SemVer)

`pyproject.toml` y `package.json` comparten version y se etiquetan juntos:

- **MAJOR**: cambio de contrato Gold, de la API publica o del formato de los
  agregados que obliga a republicar/reingestar.
- **MINOR**: capacidad nueva compatible (endpoint, tabla, metrica, flag).
- **PATCH**: correccion sin cambio de contrato.

Cada release actualiza `CHANGELOG.md`, sube la version en ambos manifiestos en
un commit `chore(release): vX.Y.Z` y crea la etiqueta anotada `vX.Y.Z`.

## Antes de abrir el PR

```powershell
uv sync --dev --extra gold
pre-commit install --install-hooks     # una vez por clon
uv run ruff check backend
uv run ruff format --check backend
uv run mypy
uv run --extra gold pytest backend/tests -q
npm run format:check && npm test && npm run build
```

CI ejecuta exactamente estos comandos; si fallan en local, fallaran en CI.
La cobertura combinada no puede bajar de `fail_under` en `pyproject.toml`.

## Revision de codigo

El revisor comprueba, en este orden:

1. **Correccion**: tests que fallarian sin el cambio; casos limite cubiertos.
2. **Contratos**: esquemas Pydantic, `contract_id` Gold y `TABLES.md` al dia.
3. **Arquitectura**: direccion de dependencias, sin duplicar logica de dominio
   (score, severidades, configuracion) fuera de `backend/app/domain`.
4. **Operacion**: metricas, logs estructurados y documentacion de runbook si el
   cambio afecta a despliegue o a datos.
5. **Legibilidad**: nombres, tamano de funciones, ausencia de imports dentro de
   bucles o funciones salvo para romper ciclos declarados.

Comentarios con prefijo: `blocking:`, `nit:`, `question:`. Un PR se aprueba sin
`blocking:` abiertos. El autor resuelve hilos; el revisor los cierra.

## Codigo

- Python 3.11, `ruff` (lint + format, linea 100), `mypy` estricto fuera de
  `app.gold` y gradual dentro (ver `[tool.mypy]`). Anadir tipos a modulos Gold
  al tocarlos; no anadir `# type: ignore` sin codigo de error y motivo.
- Funciones puras en `domain`; E/S en `infrastructure`; `api` solo adapta.
- Nada de `print` en codigo de aplicacion: `structlog`.
- Tests deterministas: fixtures sinteticas, sin datos reales, sin red.
- TypeScript estricto, Prettier, Vitest. Componentes por responsabilidad; un
  componente > 300 lineas se divide.

## Seguridad

Vulnerabilidades: ver `SECURITY.md`. No se abren issues publicas.

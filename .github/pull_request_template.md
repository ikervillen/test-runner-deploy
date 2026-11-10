<!-- Titulo del PR = Conventional Commit (se usa como mensaje del squash). -->

## Que cambia y por que

<!-- Dos o tres frases. Enlaza el issue o el documento de plan en docs/. -->

## Tipo de cambio

- [ ] `feat` / `fix` / `perf` (cambia comportamiento observable)
- [ ] `refactor` / `style` / `chore` (sin cambio de comportamiento)
- [ ] Cambio de contrato Gold, API publica o esquema de agregados (**MAJOR**)

## Como se ha verificado

<!-- Comandos ejecutados y resultado. Si algo no se pudo ejecutar, dilo. -->

```
uv run ruff check backend && uv run ruff format --check backend
uv run mypy
uv run --extra gold pytest backend/tests -q
npm run format:check && npm test && npm run build
```

## Lista de comprobacion

- [ ] Tests que fallarian sin este cambio
- [ ] `domain` no importa de `infrastructure`/`api`; `gold` no importa privados legacy
- [ ] Esquemas, `contract_id`, `gold/TABLES.md` y docs de operacion actualizados si aplica
- [ ] Sin datos reales, Excel, Parquet, `.env` ni credenciales en el diff
- [ ] Entrada en `CHANGELOG.md` bajo `[Unreleased]`

## Notas para quien revisa

<!-- Donde mirar primero, decisiones discutibles, riesgos de despliegue. -->

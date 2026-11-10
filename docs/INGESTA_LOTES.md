# Ingesta de lotes

La interfaz no acepta archivos de datos. Un administrador prepara un lote en un
directorio de staging y lo valida antes de publicar una version inmutable.

Cada lote contiene `batch.json`, el directorio `payload/` y los siete
artefactos obligatorios: `mia`, `alerts_rejections`, `inspected_totals`,
`hot_end_yield`, `severity`, `mdl_normalization` y `mia_catalog`. El descriptor
declara para cada produccion su linea, periodo, modelo de botella, segmento y
perfil DAR. Todas las rutas son relativas al lote.

Validacion sin publicar:

```powershell
.\scripts\run-backend-module.ps1 app.infrastructure.ingestion.publish `
  --batch-root D:\CalidadSerieStaging\2026-08-26-lote-01 `
  --published-root D:\CalidadSeriePublicados `
  --dar-profiles config\segment_dar_profiles.json `
  --dry-run
```

La publicacion valida la identidad y compatibilidad del perfil DAR, calcula
checksums de artefactos y copia el lote a
`releases/<batch_id>`. Solo entonces actualiza `current.json` mediante reemplazo
atomico. Un lote no se puede sobrescribir; la reversibilidad consiste en volver
a apuntar `current.json` a una release previamente validada.

La transformacion desde los Excel de avisos/rechazos e inspeccionadas debe
realizarse antes de este paso mediante los adaptadores versionados de
`app.infrastructure.ingestion`. Los originales se conservan fuera del lote
publicado.

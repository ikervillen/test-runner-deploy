# Catálogo de segmentos y configuración DAR provisional

Fuente: `Segmentos.xlsx`, hoja `WorkSheet1`, columnas A (`Código`), C (`Familia`)
y D (`Estado`). El libro se lee y archiva por SHA-256, sin modificarlo ni subirlo a Git.

Importación del 06/11/2026: 563 modelos vigentes y 12 en preparación, 575 modelos
admitidos en 13 familias. Se excluyen 956 obsoletos y 3 obsoletos técnicos.
Se conservan literalmente las clasificaciones, incluidas `Tarro` y `Tarros`.
No se equipara Vino pesado a Vino premium ni se inventan tipos de cierre.

La correspondencia Código→Familia procede del libro. Para cada segmento sigue
pendiente la definición DAR específica. El usuario autoriza temporalmente los
mismos DAR: se copia explícitamente el perfil existente `vino_ligero_v1` a un
perfil `provisional_<segmento>_v1` por familia, con los mismos grupos/códigos.
Esta configuración lleva `estado_dar=PROVISIONAL`; no acredita un criterio
industrial específico por segmento. Sin perfil común explícito queda `PENDING`.
Los resultados calculados con esa configuración se identifican como
`PROVISIONAL_DAR`, y la aplicación debe mostrar esa condición.

La importación conserva SHA-256, fila, estado original y perfil común de origen.
Los modelos desconocidos no heredan una familia ni un perfil automáticamente.
La clasificación debe enlazarse por código exacto con los modelos disponibles
en Fenix; no utilizar similitud ni orden de filas.

```powershell
$env:PYTHONPATH = (Resolve-Path backend).Path
uv run --extra gold python -m app.gold import-segments `
  --workbook Segmentos.xlsx `
  --bundle var/gold_validation/catalogs.json `
  --output var/gold_validation/catalogs-segments-provisional.json `
  --common-profile vino_ligero_v1
```

El bundle, informe y copia de fuente se guardan en `var/`, excluido de Git.
Al aprobar DAR específicos, sustituir solo los perfiles afectados mediante una
nueva versión; conservar configuración y resultados previos.

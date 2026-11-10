# Particionado de avisos y rechazos

## Objetivo

Los informes `Informe Avisos Rechazos*.xlsx` se consolidan y se dividen por:

1. Línea física, construida como `Planta + Línea` (`AV23` o `AV32`).
2. Día calendario de `Fecha inicio V.`.

La estructura resultante es:

```text
avisos_rechazos_parquet/
├── AV23/
│   ├── 2024-02-13.parquet
│   └── ...
├── AV32/
│   ├── 2024-01-11.parquet
│   └── ...
├── manifest.csv
└── metadata.json
```

Un evento que empieza antes de medianoche y termina al día siguiente permanece en la
partición de su fecha de inicio. `event_start` y `event_end` conservan el intervalo exacto.

## Integridad y trazabilidad

- Los archivos se escriben con compresión Zstandard.
- Los duplicados exactos entre informes se consolidan.
- `event_id` identifica de forma determinista el contenido del evento.
- `source_files` conserva todos los informes en los que apareció cada evento.
- `manifest.csv` permite localizar archivos por línea y fecha sin abrir todos los Parquet.

## Relación con los defectos históricos

Los informes identifican el defecto mediante códigos del maestro `SIP_DEFECTO`.
El histórico `AV-2024-2026` utiliza códigos de `SIP_MP9_DEFECTO_MIA`.

No son la misma clave. No debe utilizarse automáticamente el resto de dividir el código
del informe entre 100: por ejemplo, `201`, `301` y `401` producirían el código `1`, aunque
representan defectos físicos diferentes.

Para correlacionar un evento con la serie temporal se necesita una tabla maestra explícita:

```text
report_defect_code -> mia_defect_code
```

Una vez validada, cada aviso o rechazo podrá representarse como un intervalo
`[event_start, event_end]` superpuesto a las ventanas de 10 minutos de la línea. La cavidad
del informe permitirá acotar el ramal cuando se defina y valide la regla que relaciona
cavidades con ramales para cada línea.

## Ejecución reproducible

Desde la raíz del proyecto:

```powershell
$env:PYTHONPATH="backend"
uv run python backend/scripts/partition_alerts_rejections.py `
  "C:\Users\iker.villen\Desktop\AI_tool\Datos" `
  "C:\Users\iker.villen\Desktop\AI_tool\Datos\avisos_rechazos_parquet"
```

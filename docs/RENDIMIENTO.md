# Medicion de rendimiento

El presupuesto de rendimiento se valida contra una API ya arrancada y datos
reales publicados. No se sustituyen los resultados por estimaciones del
navegador ni por la cache local del frontend.

## Casos obligatorios

Para cada linea (AV23 y AV32), medir y conservar el JSON de resultados para:

1. una produccion;
2. todas las producciones de la linea;
3. todas las producciones de ambas lineas;
4. al menos cinco consultas concurrentes sobre el caso completo.

El archivo de seleccion contiene exactamente el cuerpo de
`POST /api/customer-score/query`. Ejemplo:

```json
{
  "selection": {
    "production_ids": ["AV32_PROD_001"],
    "branches": [],
    "metric": "rejected_count",
    "window": "10m",
    "filters": {}
  }
}
```

Ejecutar cinco repeticiones:

```powershell
.\scripts\run-backend-module.ps1 app.infrastructure.benchmark.customer_score `
  --selection .\benchmarks\av32-all.json `
  --repetitions 5 `
  --output .\benchmarks\results\av32-all.json
```

Para verificar cinco consultas simultaneas, la misma herramienta ejecuta cada
repeticion como un lote concurrente. Por ejemplo, una ronda de cinco usuarios:

```powershell
.\scripts\run-backend-module.ps1 app.infrastructure.benchmark.customer_score `
  --selection .\benchmarks\av32-all.json `
  --repetitions 1 `
  --concurrency 5 `
  --output .\benchmarks\results\av32-all-concurrent.json
```

El informe identifica explicitamente `batches`, `concurrency` y el numero total
de consultas (`runs`), ademas de percentiles y cache hit/miss.

Las consultas simultaneas con la misma seleccion, configuracion y version de
datos se resuelven mediante una unica ejecucion en vuelo: la primera calcula y
las restantes esperan ese mismo resultado. El informe las marca como
`cache_hit=true`. Esto protege CPU y almacenamiento ante peticiones duplicadas,
pero no sustituye el presupuesto de latencia de una consulta fria distinta.

El informe recoge p50/p95 de transporte y del calculo del servidor, volumen de
ficheros/filas, y cache hit/miss. Los informes se guardan fuera de Git si
incluyen identificadores o datos operativos.

## Presupuesto inicial pendiente de aprobacion

No se fija un numero ficticio antes de medir el hardware destino. Antes de la
puesta en produccion, Calidad e Infraestructura deben aprobar por escrito:

| Medida | Objetivo a acordar |
| --- | --- |
| p95 de una produccion | ms/s |
| p95 de una linea completa | ms/s |
| p95 con cinco usuarios | ms/s |
| Memoria maxima del backend | MiB |
| Concurrencia maxima de calculo | consultas |

El backend ya limita las consultas pesadas mediante configuracion. La siguiente
fase de agregados se justifica unicamente si estos resultados no cumplen el
presupuesto acordado.

## Paquetes A/R por produccion

La primera optimizacion materializada no altera la formula: agrupa los
ficheros diarios de avisos/rechazos que intersectan cada produccion en un unico
Parquet por produccion. Conserva los eventos completos para que sigan siendo
exactos los minutos-cavidad, DAR, filtros y el detalle individual de turno.
La misma release genera tasas MIA por produccion y por turno, a partir de los
rechazos MIA y las botellas inspeccionadas reales deduplicadas por
fecha/hora-ramal. Tambien convierte `Rendimiento_ZC.xlsx` a observaciones
Parquet para que la consulta interactiva no tenga que parsear el Excel. Las
selecciones con todos los ramales y sin filtros avanzados usan esas tasas; las
parciales siguen resolviendose contra origen.

```powershell
.\scripts\run-backend-module.ps1 app.infrastructure.aggregates.build `
  --data-root D:\CalidadSerieData\AV-2024-2026_parquet `
  --alerts-root D:\CalidadSerieData\avisos_rechazos_parquet `
  --inspected-root D:\CalidadSerieData\inspeccionadas_totales_parquet `
  --hot-end-yield-path D:\CalidadSerieData\Rendimiento_ZC.xlsx `
  --output-root D:\CalidadSerieData\quality_aggregates `
  --release-id 2026-08-26-01
```

La publicacion crea `releases/<id>` y actualiza `current.json` de forma
atomica. `APP_QUALITY_AGGREGATE_ROOT` activa su uso. Cada paquete registra el
tamanio y fecha de modificacion de todas las particiones origen; si alguno
cambia, la API descarta el agregado y vuelve al origen, sin ofrecer un
resultado potencialmente obsoleto. ZC conserva el mismo fallback al Excel y,
cuando existe el agregado vigente, carga sus observaciones desde Parquet.

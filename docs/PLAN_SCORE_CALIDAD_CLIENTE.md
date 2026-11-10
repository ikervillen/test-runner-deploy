# Plan de integracion: score alternativo de calidad de produccion

## Objetivo

Crear una pestana nueva de la aplicacion para valorar la calidad de cada produccion con un score alternativo, paralelo a los analisis actuales. Esta pestana no sustituye al historico, al analisis estadistico ni al semaforo de criticidades existente. Debe servir como una lectura agregada y explicable, orientada a cliente, que combine:

- Desviacion historica de defectos MIA individuales.
- Exposicion por categorias AQL.
- DAR como bloque especifico.
- Rendimiento de zona caliente como penalizacion independiente de estabilidad de proceso.

El resultado debe ofrecer dos lecturas paralelas:

- `Penalizacion_Total`: suma ponderada sin normalizar. Es la metrica principal para ranking, porque permite que las peores producciones destaquen con valores claramente mas altos.
- `Score_Normalizado`: media ponderada en escala 0-10. Es la metrica secundaria para interpretar la intensidad media del problema con una escala estable.

## Formula general

La pestana debe calcular siempre dos scores.

### Penalizacion total sin normalizar

Esta sera la metrica principal de ranking:

```text
Penalizacion_Total =
  Sumatorio Score_MIA_i
+ 10 * Score_Criticos
+ 5  * Score_Graves
+ 3  * Score_Mayores
+ 2  * Score_Menores
+ 2  * Score_Leves
+ 8  * Score_DAR
+ 2  * Score_ZC
```

Ventaja: una produccion con muchos problemas graves acumula mas penalizacion y queda claramente separada del resto.

Riesgo: la penalizacion total depende del numero de defectos MIA evaluados. Por eso debe mostrarse siempre junto al peso total evaluado.

### Score normalizado 0-10

Esta sera la metrica secundaria de interpretacion:

```text
Score_Normalizado =
Penalizacion_Total
/
Peso_Total_Evaluado
```

Donde:

```text
Peso_Total_Evaluado =
numero_defectos_MIA_evaluados
+ 10
+ 5
+ 3
+ 2
+ 2
+ 8
+ 2
```

Si algun bloque esta desactivado o no tiene configuracion valida, su peso debe excluirse de `Peso_Total_Evaluado` y debe generarse una advertencia.

Punto clave: en la parte AQL el peso se aplica una vez por categoria, no una vez por defecto. Si el AQL del cliente esta definido por categoria, la categoria completa debe generar un unico score.

## Fuentes de datos

| Termino | Fuente principal | Uso |
|---|---|---|
| Producciones, fechas e intervalos | `AV-2024-2026_parquet/manifest.csv` | Identificar inicio, fin, linea y ramales de cada produccion |
| MIA individual | `AV-2024-2026_parquet` | Calcular porcentaje de rechazo actual por defecto MIA |
| Historico MIA | `AV-2024-2026_parquet` | Calcular media historica y sigma historica por defecto MIA |
| Categorias AQL | `Tabla defectos con criticidades.xlsx` | Mapear codigo de defecto a Critico, Grave, Mayor, Menor o Leve |
| Minutos-cavidad AQL | `AV-2024-2026_parquet` | Calcular presencia por categoria en minutos-cavidad |
| DAR | `avisos_rechazos_parquet` preferentemente | Calcular eventos o exposicion asociada a DAR si DAR procede de informes de avisos/rechazos |
| Rendimiento ZC | `Rendimiento_ZC.xlsx` | Calcular rendimiento real de zona caliente por produccion |
| Catalogo de defectos | `COD_DEF.xlsx` y/o etiquetas en tablas existentes | Mostrar nombres legibles de codigos |

La implementacion debe documentar, para cada componente del score, que fuente ha usado y cuantas filas/archivos ha leido. Esto es importante para trazabilidad.

## Parte MIA

Cada defecto MIA se analiza individualmente y pesa 1.

Para cada produccion y defecto MIA:

```text
porcentaje_rechazo_actual =
rechazos_defecto / botellas_inspeccionadas * 100
```

Despues se compara contra su historico:

```text
z = (porcentaje_rechazo_actual - media_historica_defecto) / sigma_historica_defecto
```

La funcion de puntuacion sera:

| Condicion | Score |
|---|---:|
| z <= 0 | 0 |
| z = 1 | 1 |
| z = 2 | 5 |
| z = 2,72 | 10 |
| z > 2,72 | 10 |

Entre puntos se aplicara interpolacion lineal:

```text
0 <= z < 1:
  score = z

1 <= z < 2:
  score = 1 + (z - 1) * 4

2 <= z < 2,72:
  score = 5 + (z - 2) * (5 / 0,72)

z >= 2,72:
  score = 10
```

Si `sigma_historica_defecto = 0`, no se debe dividir por cero. Regla propuesta:

```text
si sigma = 0 y actual <= media:
  Score_MIA = 0

si sigma = 0 y actual > media:
  usar fallback configurable
```

El fallback debe estar en configuracion. Opciones:

- `max_score`: asignar 10.
- `fixed_score`: asignar un valor definido, por ejemplo 5.
- `ignore_if_low_sample`: excluir si no hay suficiente historico.

Salida recomendada por defecto MIA:

| Defecto | % actual | Media historica | Sigma | z | Score | Peso | Contribucion |
|---|---:|---:|---:|---:|---:|---:|---:|

## Parte AQL por categoria

No se comparan defectos individuales contra AQL individual. Se agrupan todos los defectos de una misma categoria.

Categorias:

- Criticos.
- Graves.
- Mayores.
- Menores.
- Leves.

Para cada categoria:

```text
minutos_cavidad_categoria =
Sumatorio minutos_cavidad de todos los defectos de esa categoria
```

Ejemplo:

```text
Categoria Graves:
- Defecto A: 120 minutos-cavidad
- Defecto B: 80 minutos-cavidad
- Defecto C: 50 minutos-cavidad

Total Graves = 250 minutos-cavidad
```

Despues:

```text
minutos_cavidad_totales_serie =
duracion_serie_minutos * numero_cavidades_activas
```

Y:

```text
exposicion_categoria_pct =
minutos_cavidad_categoria / minutos_cavidad_totales_serie * 100
```

Comparacion contra AQL:

```text
ratio_AQL_categoria =
exposicion_categoria_pct / AQL_categoria_cliente
```

Score:

```text
Score_categoria = min(10, 5 * ratio_AQL_categoria)
```

Ejemplo:

```text
AQL graves cliente = 2 %
Exposicion graves = 2,60 %

ratio = 2,60 / 2 = 1,30
Score_Graves = 5 * 1,30 = 6,5
Contribucion_Graves = 6,5 * 5 = 32,5
```

Pesos:

| Categoria | Peso |
|---|---:|
| Criticos | 10 |
| Graves | 5 |
| Mayores | 3 |
| Menores | 2 |
| Leves | 2 |

Salida recomendada:

| Categoria | Min-cavidad afectados | Min-cavidad total | Exposicion % | AQL cliente | Ratio AQL | Score | Peso | Contribucion |
|---|---:|---:|---:|---:|---:|---:|---:|---:|

## DAR

DAR se tratara como bloque separado con peso 8.

La fuente preferente sera `avisos_rechazos_parquet` si DAR corresponde a eventos procedentes de informes de avisos y rechazos. Si finalmente DAR se define sobre defectos de inspeccion de `AV-2024-2026_parquet`, la fuente debe cambiarse explicitamente en configuracion.

Regla normal:

```text
exposicion_DAR_pct =
minutos_cavidad_DAR / minutos_cavidad_totales_serie * 100

Score_DAR =
min(10, 5 * exposicion_DAR_pct / AQL_DAR)

Contribucion_DAR =
Score_DAR * 8
```

Si `AQL_DAR` es cero o practicamente cero, usar una regla especial configurable:

| Situacion | Score_DAR |
|---|---:|
| 0 DAR | 0 |
| Presencia baja | 5 |
| Presencia clara o supera limite | 10 |

Los umbrales de `presencia baja` y `presencia clara` no deben estar hardcodeados.

## Rendimiento de zona caliente

La fuente sera `Rendimiento_ZC.xlsx`.

Calculo recomendado:

```text
rendimiento_real =
Sumatorio RUEDA / Sumatorio (TEORICO_A + TEORICO_B)
```

Siempre dentro del intervalo temporal de la produccion.

Score:

```text
Score_ZC =
min(10, max(0, (rendimiento_objetivo - rendimiento_real) / divisor_penalizacion_zc))
```

Primera version recomendada:

```text
divisor_penalizacion_zc = 2
```

Ejemplo:

```text
Objetivo ZC = 92 %
Real ZC = 84 %
Perdida = 8 puntos

Score_ZC = 8 / 2 = 4
Contribucion_ZC = 2 * 4 = 8
```

El objetivo ZC debe ser configurable por defecto global y, si es necesario, por linea o tipo de produccion.

## Causa principal

La causa principal debe calcularse a partir de las contribuciones ponderadas que forman `Penalizacion_Total`.

Componentes candidatos:

- MIA agregado.
- Criticos.
- Graves.
- Mayores.
- Menores.
- Leves.
- DAR.
- ZC.

Regla:

```text
Causa_Principal =
componente con mayor contribucion ponderada
```

Para MIA, ademas de la contribucion agregada, debe poder mostrarse el defecto MIA que mas contribuye dentro del bloque.

Ejemplo:

```text
Causa principal: Criticos
Detalle: exposicion de defectos criticos aporta 64 puntos, 39 % de la penalizacion total.
```

Si dos componentes estan muy proximos, se recomienda mostrar ambos. El umbral de proximidad debe ser configurable:

```yaml
main_cause:
  include_ties: true
  tie_relative_margin: 0.1
  max_causes: 2
```

## Configuracion explicita

Para evitar valores hardcodeados, la nueva funcionalidad debe leer una configuracion versionada. Puede empezar como `config/customer_score.yaml` o `config/customer_score.json`.

Ejemplo recomendado en YAML:

```yaml
version: 1

score:
  scale_min: 0
  scale_max: 10
  primary_metric: total_penalty
  secondary_metric: normalized_score
  sort_by: total_penalty_desc
  show_main_cause: true
  main_cause:
    include_ties: true
    tie_relative_margin: 0.1
    max_causes: 2
  interpretation:
    good_max: 2
    observed_max: 5
    bad_max: 7
    critical_max: 10

weights:
  mia_defect: 1
  categories:
    Critico: 10
    Grave: 5
    Mayor: 3
    Menor: 2
    Leve: 2
  dar: 8
  zc: 2

mia:
  enabled: true
  defect_codes: []
  # Si esta vacio, usar todos los defectos MIA disponibles o una lista derivada del catalogo.
  historical_scope:
    mode: all_available
    exclude_current_production: true
    min_samples: 10
  sigma_curve:
    points:
      - z: 0
        score: 0
      - z: 1
        score: 1
      - z: 2
        score: 5
      - z: 2.72
        score: 10
    cap_score: 10
  zero_sigma_policy:
    mode: fixed_score_if_above_mean
    fixed_score: 5

aql:
  enabled: true
  category_limits_percent:
    Critico: null
    Grave: null
    Mayor: null
    Menor: null
    Leve: null
  missing_limit_policy: exclude_component
  minute_cavity:
    active_cavities_mode: infer_from_data
    fallback_active_cavities: null

dar:
  enabled: true
  source: avisos_rechazos_parquet
  defect_codes: []
  match_terms: []
  aql_percent: null
  zero_or_near_zero_aql_policy:
    enabled: true
    low_presence_threshold_percent: null
    clear_presence_threshold_percent: null
    low_presence_score: 5
    clear_presence_score: 10

zc:
  enabled: true
  source: Rendimiento_ZC.xlsx
  target_percent:
    default: 92
    by_line: {}
  penalty_divisor_points: 2
  aggregation: weighted_by_theoretical_total

data_sources:
  av_2024_2026_root: default_settings
  alerts_rejections_root: default_settings
  hot_end_yield_path: default_settings
  defect_severity_path: default_settings
  defect_catalog_path: default_settings

warnings:
  fail_on_missing_config: false
  fail_on_missing_aql: false
  show_unclassified_defects: true
```

Campos que deben ser obligatorios antes de usar el score en entorno real:

- AQL por categoria.
- Definicion de DAR.
- Lista de defectos MIA o regla clara para seleccionarlos.
- Politica de historico MIA.
- Objetivo ZC.

## Backend propuesto

Crear un modulo nuevo, sin mezclarlo con el semaforo actual:

```text
backend/app/domain/customer_score.py
backend/app/schemas/customer_score.py
backend/app/application/customer_score.py
backend/app/infrastructure/repositories/customer_score.py
backend/app/api/routes/customer_score.py
```

Endpoint:

```text
POST /api/customer-score/query
```

Entrada:

```json
{
  "production_ids": ["AV32_PROD_007", "AV32_PROD_008"],
  "filters": {
    "defect_codes": [],
    "cavities": [],
    "colors": [],
    "sections": [],
    "machine_codes": []
  },
  "config_overrides": {
    "aql": {
      "category_limits_percent": {
        "Critico": 0.5,
        "Grave": 2.0,
        "Mayor": 4.0,
        "Menor": 6.0,
        "Leve": 8.0
      }
    }
  }
}
```

Salida:

```json
{
  "productions": [
    {
      "production_id": "AV32_PROD_009",
      "total_penalty": 163.8,
      "normalized_score": 4.82,
      "evaluated_weight": 34,
      "status": "observed",
      "main_cause": {
        "component": "Criticos",
        "label": "Exposicion de defectos criticos",
        "contribution": 64.0,
        "share_of_total_penalty": 0.39
      },
      "components": {
        "mia": {
          "score_weighted_sum": 12.4,
          "defect_count": 4,
          "details": []
        },
        "aql_categories": [],
        "dar": {},
        "zc": {}
      },
      "top_contributors": []
    }
  ],
  "meta": {
    "query_time_ms": 0,
    "config_version": 1,
    "files_read": {}
  },
  "warnings": []
}
```

## Frontend propuesto

Nueva pestana:

```text
Score calidad cliente
```

Secciones:

1. Resumen por produccion, ordenado por `Penalizacion_Total` descendente.
2. Barras apiladas de contribucion por bloque.
3. Detalle MIA.
4. Detalle AQL por categoria.
5. Detalle DAR.
6. Detalle ZC.
7. Advertencias de datos/configuracion.

Tabla resumen:

| Produccion | Penalizacion total | Score 0-10 | Peso evaluado | Estado | MIA | Criticos | Graves | Mayores | Menores | Leves | DAR | ZC | Causa principal |
|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|

`Penalizacion total` es la metrica principal para detectar las peores producciones. `Score 0-10` es la lectura normalizada para interpretar la intensidad media del problema. Ambas deben mostrarse juntas para evitar perder contraste y, al mismo tiempo, mantener una escala interpretable.

La `Causa principal` debe ser el componente con mayor contribucion ponderada dentro de `Penalizacion_Total`. Si hay empates o contribuciones muy proximas, se deben mostrar las dos primeras causas.

Estados sugeridos:

| Score 0-10 | Estado |
|---:|---|
| 0 - 2 | Bueno |
| >2 - 5 | Observado |
| >5 - 7 | Malo |
| >7 - 10 | Critico |

Estos umbrales deben venir de configuracion.

## Validaciones necesarias

Antes de implementar, cerrar estas decisiones:

1. Lista exacta de defectos MIA.
2. Si MIA usa todos los defectos MIA o solo una lista acordada.
3. Periodo historico valido para media y sigma.
4. Politica ante sigma cero o historico insuficiente.
5. AQL cliente por categoria.
6. Definicion exacta de DAR.
7. Fuente definitiva de DAR.
8. Objetivo ZC global o por linea.
9. Como inferir numero de cavidades activas.
10. Que hacer con defectos sin criticidad.

## Estrategia de implementacion por pasos

Para evitar una integracion grande y dificil de revisar, se recomienda hacerlo en fases:

### Fase 1: contrato y configuracion

- Crear archivo de configuracion.
- Crear schemas de entrada/salida.
- Crear endpoint vacio o con datos mock trazables.
- Anadir tests de carga de configuracion.

### Fase 2: calculo MIA

- Leer `AV-2024-2026_parquet`.
- Calcular historico por defecto.
- Calcular z-score y `Score_MIA_i`.
- Devolver detalle por defecto.

### Fase 3: calculo AQL por categoria

- Leer criticidades desde `Tabla defectos con criticidades.xlsx`.
- Agrupar defectos por categoria.
- Calcular minutos-cavidad y exposicion.
- Aplicar AQL y pesos por categoria.

### Fase 4: DAR

- Implementar fuente configurada.
- Calcular exposicion o regla especial.
- Anadir desglose y advertencias.

### Fase 5: ZC

- Reutilizar carga de `Rendimiento_ZC.xlsx`.
- Calcular rendimiento ponderado.
- Aplicar score ZC configurable.

### Fase 6: frontend

- Crear pestana.
- Mostrar ranking, desglose y detalles.
- Mostrar advertencias y version de configuracion.

### Fase 7: validacion con casos reales

- Validar manualmente varias producciones AV32 y AV23.
- Comparar el resultado con la lectura esperada por calidad.
- Ajustar pesos, AQL y reglas de DAR solo via configuracion.

## Criterios de aceptacion

- La respuesta incluye `Penalizacion_Total` sin normalizar como metrica principal de ranking.
- La respuesta incluye `Score_Normalizado` entre 0 y 10 como metrica secundaria de interpretacion.
- La tabla principal se ordena por `Penalizacion_Total` descendente.
- La tabla principal muestra la causa principal de la penalizacion.
- Cada componente muestra score, peso y contribucion.
- El peso total evaluado se muestra o queda accesible.
- Ningun AQL, peso, objetivo ZC o umbral de estado queda hardcodeado.
- La parte AQL aplica pesos por categoria, no por defecto individual.
- MIA sigue siendo individual por defecto.
- DAR queda aislado como bloque propio.
- ZC no se mezcla con defectos, solo penaliza estabilidad de proceso.
- La respuesta del backend incluye advertencias cuando falten datos o configuracion.

# Plan de riesgo para avisos, rechazos e incidencias

## 1. Objetivo

Definir una nueva linea de analisis para la aplicacion basada en incidencias con
fecha/hora de apertura y cierre. El objetivo no es solo contar minutos o numero
de eventos, sino caracterizar el tipo de riesgo operacional:

- impacto real sobre el tiempo de produccion;
- persistencia de problemas largos;
- recurrencia de problemas intermitentes;
- concentracion en pocas cavidades;
- dispersion entre muchas cavidades, defectos, turnos o zonas;
- volumen absoluto de botellas potencialmente bajo sospecha;
- proporcion del lote, pedido o franja temporal potencialmente afectada;
- relacion futura con los rechazos registrados por ventana temporal.

La idea principal es:

```text
Riesgo = impacto + patron de aparicion
```

Dos situaciones pueden tener la misma duracion total afectada y representar
riesgos distintos:

```text
1 incidencia x 120 min
8 incidencias x 15 min
```

La primera apunta a persistencia. La segunda apunta a recurrencia o
inestabilidad.

La primera version no debe usar gravedad como multiplicador ni como componente
de scoring. El enfoque inicial debe apoyarse solo en magnitudes observables:

```text
duracion
cavidades afectadas
cavidades activas
velocidad por cavidad
botellas producidas
frecuencia
recurrencia
concentracion
dispersion
```

## 2. Alcance funcional

La funcionalidad deberia incorporarse como una nueva vista o pestana propia,
separada del analisis historico actual de rechazos por ventana. La separacion es
importante porque los datos y la interpretacion son distintos:

- la vista actual analiza rechazos agregados temporalmente;
- la nueva vista analizara incidencias con duracion, recurrencia y afectacion.

Nombre propuesto:

```text
Riesgo de incidencias
```

Esta vista podra compartir filtros globales con el historico:

- linea;
- produccion/lote;
- maquina;
- ramal;
- turno;
- defecto;
- cavidad;
- rango temporal.

La funcionalidad debe implementarse como modulo propio, no como una extension
directa de `statistics`, `defect-analysis` o `quality-scorecard`.

Nombre tecnico propuesto:

```text
incidence-risk
```

Motivo:

- los datos de entrada son incidencias con apertura/cierre, no ventanas de
  rechazo agregadas;
- la semantica de riesgo es distinta a la de calidad historica;
- la convencion de score es distinta: en riesgo, 100 significa peor;
- la normalizacion historica debe poder usar una referencia mas amplia que la
  seleccion visible del usuario.

Desde el primer diseno hay que separar:

```text
analysis_scope   = lo que el usuario quiere analizar y visualizar
reference_scope  = historico comparable usado para calcular percentiles
```

Ejemplo:

```text
analysis_scope:
AV23_PROD_001, AV23_PROD_007

reference_scope:
todas las producciones disponibles de AV23 del mismo modelo de botella
```

Esto evita que los percentiles dependan solo de las producciones seleccionadas
en pantalla.

## 3. Modelo conceptual de datos

Cada registro de aviso/rechazo/incidencia deberia normalizarse a un modelo
interno estable.

Campos minimos:

```text
incidence_id
source_file
event_type              # aviso, rechazo, incidencia, otro
line
machine
branch
cavity
defect_code
defect_description
lot_id
production_id
shift
opened_at
closed_at
duration_minutes
affected_cavities
active_cavities
line_speed_bpm
speed_per_cavity_bpm
total_produced_bottles
order_bottles
status
```

Campos derivados:

```text
duration_minutes = closed_at - opened_at
affected_cavity_minutes = duration_minutes * affected_cavities
lot_minutes = lot_end - lot_start
available_cavity_minutes = lot_minutes * active_cavities
suspect_bottles = duration_minutes * affected_cavities * speed_per_cavity_bpm
suspect_fraction_lot = suspect_bottles / total_produced_bottles
suspect_fraction_order = suspect_bottles / order_bottles
```

Si una incidencia no tiene cierre, debe marcarse explicitamente:

```text
is_open = true
is_censored = true
```

Reglas pendientes de decision:

- cerrar temporalmente en fin de lote;
- cerrar temporalmente en fin de turno;
- cerrar temporalmente en `now`;
- excluir de percentiles de duracion cerrada;
- incluir en exposicion hasta el limite observado.

Campos como velocidad, botellas producidas o tamano de pedido pueden no estar
disponibles en todos los registros. En ese caso:

- la metrica de proceso sigue siendo calculable;
- las metricas de cliente se marcaran como no disponibles o baja confianza;
- no se debe imputar velocidad sin dejarlo indicado en la respuesta.

## 4. Unidad de analisis

Las metricas deberian poder calcularse sobre distintas unidades:

```text
produccion
lote
dia
turno
linea
maquina
ramal
cavidad
defecto
cavidad + defecto
turno + defecto
maquina + cavidad
```

La primera version deberia soportar al menos:

- produccion/lote;
- turno;
- maquina/ramal;
- cavidad;
- defecto;
- cavidad + defecto.

## 5. Metricas atomicas

Antes de crear indices compuestos, hay que calcular metricas directas y
auditables.

### 5.1 Duracion

```text
n_incidencias
duracion_total_min
duracion_media_min
duracion_mediana_min
duracion_max_min
duracion_p90_min
duracion_p95_min
```

Interpretacion:

- media alta: eventos generalmente largos;
- maximo alto: evento extremo;
- p90 alto: cola persistente, no solo un unico outlier.

### 5.2 Exposicion

La metrica base de proceso sera la exposicion normalizada:

```text
exposicion_proceso_normalizada =
sum(duracion_incidencia * numero_cavidades_afectadas)
/
(duracion_lote * numero_cavidades_activas)
```

Ejemplo:

```text
duracion_lote = 480 min
cavidades_activas = 20
minutos-cavidad posibles = 480 * 20 = 9.600
minutos-cavidad afectados = 240
exposicion_proceso_normalizada = 240 / 9.600 = 2,5 %
```

Esta metrica permite comparar producciones con distinto numero de cavidades
activas.

Tambien se conservaran:

```text
affected_cavity_minutes
available_cavity_minutes
exposure_percent
```

Lectura:

```text
1 cavidad afectada en una maquina de 16 cavidades = 6,25 % de capacidad bajo sospecha
1 cavidad afectada en una maquina de 48 cavidades = 2,08 % de capacidad bajo sospecha
```

Para estabilidad de proceso, el numero de cavidades activas es esencial. La
velocidad de linea no altera la proporcion estructural de maquina afectada.

### 5.3 Botellas potencialmente bajo sospecha

La segunda lectura mide volumen fisico potencialmente afectado.

```text
botellas_sospechosas =
sum(duracion_incidencia * cavidades_afectadas * velocidad_por_cavidad)
```

Ejemplos:

```text
60 min * 1 cavidad * 8 botellas/min = 480 botellas sospechosas
60 min * 1 cavidad * 15 botellas/min = 900 botellas sospechosas
```

Esta metrica responde:

```text
Cuantas botellas podrian haber estado bajo sospecha durante la incidencia?
```

No implica que todas sean defectuosas. Representa volumen potencial expuesto,
especialmente relevante si el aviso o rechazo se detecta con retardo.

### 5.4 Proporcion potencialmente afectada

El volumen absoluto no basta para estimar riesgo relativo sobre lote o pedido.

Se conservaran dos proporciones:

```text
fraccion_sospechosa_lote =
botellas_sospechosas / botellas_totales_producidas_lote

fraccion_sospechosa_pedido =
botellas_sospechosas / botellas_totales_pedido
```

Esto separa dos lecturas:

```text
900 botellas sospechosas pueden ser muchas en absoluto.
900 botellas sospechosas pueden ser poca proporcion si el pedido/lote es enorme.
```

Por tanto, el riesgo cliente debe conservar:

- volumen absoluto;
- proporcion relativa sobre lote, pedido o franja temporal.

### 5.5 Frecuencia

```text
n_incidencias
incidencias_por_hora
incidencias_por_turno
incidencias_por_lote
```

Lectura:

- frecuencia alta con duraciones cortas sugiere intermitencia;
- frecuencia baja con duraciones largas sugiere persistencia.

### 5.6 Recurrencia

La recurrencia mide si el problema vuelve con el mismo patron.

Metricas:

```text
reopen_count
repeated_cavity_defect_count
repeated_defect_count
repeated_cavity_count
mean_time_between_recurrences
median_time_between_recurrences
```

Claves de recurrencia propuestas:

```text
cavity + defect_code
machine + cavity + defect_code
branch + cavity + defect_code
defect_code
```

Ejemplo:

```text
Defecto 37 en cavidad 4 aparece 6 veces en el lote.
```

Esto no es solo frecuencia global; es recurrencia localizada.

## 6. Doble enfoque: proceso y cliente

El analisis debe separar dos enfoques porque responden preguntas distintas.

### 6.1 Riesgo de proceso

El riesgo de proceso mide la afectacion estructural de la maquina.

Variables principales:

```text
duracion
cavidades_afectadas
cavidades_activas
frecuencia
recurrencia
concentracion
dispersion
```

Formula base:

```text
exposicion_proceso_normalizada =
sum(duracion * cavidades_afectadas)
/
(duracion_periodo * cavidades_activas)
```

La velocidad no entra en esta metrica. Una cavidad afectada durante una hora en
una maquina de 16 cavidades implica mas afectacion proporcional de proceso que
una cavidad afectada durante una hora en una maquina de 48 cavidades.

### 6.2 Riesgo cliente absoluto

El riesgo cliente absoluto mide volumen potencial de botellas bajo sospecha.

Formula base:

```text
botellas_sospechosas =
sum(duracion * cavidades_afectadas * velocidad_por_cavidad)
```

La velocidad entra aqui porque ante el mismo tiempo de deteccion o reaccion, una
linea rapida genera mas volumen potencialmente afectado.

### 6.3 Riesgo cliente relativo

El riesgo cliente relativo mide la proporcion del lote, pedido o franja temporal
potencialmente afectada.

Formulas:

```text
fraccion_sospechosa_lote =
botellas_sospechosas / botellas_totales_lote

fraccion_sospechosa_pedido =
botellas_sospechosas / botellas_totales_pedido
```

Esta lectura evita concluir que una produccion rapida es siempre peor solo por
generar mas botellas sospechosas en absoluto. Puede ocurrir que el volumen
absoluto sea mayor y la proporcion relativa sea menor.

### 6.4 Interpretacion combinada

Casos esperados:

```text
proceso alto + cliente absoluto bajo:
problema relevante para estabilidad interna, pero bajo volumen expuesto.

proceso bajo + cliente absoluto alto:
poca proporcion de maquina afectada, pero mucho volumen por velocidad.

cliente absoluto alto + cliente relativo bajo:
mucho volumen sospechoso, pero diluido en un lote/pedido grande.

cliente absoluto bajo + cliente relativo alto:
pocas botellas absolutas, pero lote pequeno muy afectado proporcionalmente.

proceso alto + cliente absoluto o relativo alto:
riesgo critico desde ambas lecturas.
```

## 7. Concentracion y dispersion

Esta parte debe diferenciar problemas localizados frente a problemas sistemicos.

### 7.1 Concentracion Top 3

Metrica principal para planta:

```text
concentracion_top3 =
exposicion_de_las_3_peores_cavidades
/
exposicion_total
```

Donde:

```text
exposicion_de_las_3_peores_cavidades =
sum(exposicion_cavidad_i para las 3 cavidades con mayor exposicion)
```

Ejemplo:

```text
exposicion_total = 240 min-cavidad
top3 = 192 min-cavidad
concentracion_top3 = 192 / 240 = 80 %
```

Lectura:

```text
80 % explicado por las 3 peores cavidades => problema concentrado.
```

Ventajas:

- facil de explicar;
- accionable en planta;
- conecta directamente con mantenimiento, molde, cavidad o ajuste.

### 7.2 HHI de cavidades

Como complemento estadistico:

```text
HHI = sum(cuota_i ^ 2)
```

Donde:

```text
cuota_i = exposicion_cavidad_i / exposicion_total
```

Interpretacion:

- HHI alto: exposicion concentrada en pocas cavidades;
- HHI bajo: exposicion repartida entre muchas cavidades.

Ejemplos:

```text
Una cavidad explica todo:
HHI = 1,00

Diez cavidades explican lo mismo:
HHI = 10 * (0,10^2) = 0,10
```

Uso recomendado:

- mostrar Top 3 en dashboard;
- usar HHI como metrica tecnica secundaria;
- guardar ambos en backend para analisis avanzado.

### 7.3 Dispersion

La dispersion debe medir cuantas cavidades participan de forma relevante.

Metricas:

```text
n_cavidades_afectadas
n_cavidades_con_exposicion_relevante
1 - concentracion_top3
1 - HHI_normalizado
entropia_cavidad
```

Lectura:

- alta concentracion: problema localizado;
- alta dispersion: posible problema sistemico o criterio inestable;
- alta frecuencia + alta dispersion: riesgo intermitente dificil de detectar.

## 8. Indices principales

No se debe reducir todo desde el principio a una metrica plana. Primero se
calculan metricas atomicas y despues indices compuestos.

### 8.1 IEP: Indice de Exposicion/Persistencia de proceso

Mide impacto temporal directo y persistencia de duracion.

Propuesta:

```text
IEP =
0,55 * exposicion_proceso_score
+ 0,30 * persistencia_score
+ 0,15 * duracion_max_score
```

Variables:

```text
exposicion_proceso_score
persistencia_score       # p90 o p95 de duracion
duracion_max_score
```

Pregunta que responde:

```text
El problema ha tenido impacto real y/o ha sido largo?
```

### 8.2 IIR: Indice de Inestabilidad/Recurrencia de proceso

Mide intermitencia, repeticion y dispersion.

Propuesta:

```text
IIR =
0,40 * frecuencia_score
+ 0,35 * recurrencia_score
+ 0,25 * dispersion_score
```

Variables:

```text
frecuencia_score
recurrencia_score
dispersion_score
```

Pregunta que responde:

```text
Esto parece un proceso inestable, repetitivo o dificil de detectar?
```

### 8.3 Riesgo cliente absoluto

Mide volumen fisico potencialmente bajo sospecha.

Primera version:

```text
RCA = percentil_historico(botellas_sospechosas)
```

Opcionalmente, cuando exista suficiente historico:

```text
RCA =
0,70 * botellas_sospechosas_score
+ 0,30 * persistencia_cliente_score
```

Pregunta que responde:

```text
Cuanto volumen potencialmente afectado se ha generado?
```

### 8.4 Riesgo cliente relativo

Mide proporcion del lote, pedido o franja temporal potencialmente afectada.

Primera version:

```text
RCR = percentil_historico(fraccion_sospechosa_lote)
```

Si existe dato de pedido:

```text
RCR_pedido = percentil_historico(fraccion_sospechosa_pedido)
```

Pregunta que responde:

```text
Que porcentaje del volumen producido o pedido queda bajo sospecha?
```

### 8.5 IRL: Indice de localizacion

Indice explicativo, no necesariamente parte del riesgo global.

Propuesta:

```text
IRL = concentracion_top3_score
```

Lectura:

```text
IRL alto => problema localizado
IRL bajo + IIR alto => problema disperso o sistemico
```

## 9. Normalizacion

No se recomiendan umbrales fijos iniciales.

Evitar reglas como:

```text
30 min = mucho
8 incidencias = mucho
2 % exposicion = poco
```

La normalizacion recomendada es por percentiles historicos comparables.

La seleccion visible del usuario no debe definir por si sola la poblacion de
percentiles. El usuario puede seleccionar una o varias producciones para
analizarlas, pero los scores deben calcularse contra un historico comparable
mas amplio.

Regla recomendada para primera version:

```text
analysis_scope =
producciones/lotes seleccionados por el usuario

reference_scope =
todas las producciones disponibles de la misma linea y mismo modelo de botella
```

Si el modelo de botella no esta disponible de forma explicita, la referencia
debe quedar degradada y trazada:

```text
reference_scope =
todas las producciones disponibles de la misma linea
```

La respuesta de la API debe devolver siempre:

```text
analysis_scope_summary
reference_scope_summary
reference_population_size
reference_scope_confidence
```

Ejemplo de texto para frontend:

```text
Analizando 2 producciones seleccionadas.
Percentiles calculados contra 18 producciones historicas AV23 del mismo modelo.
```

Ejemplo:

```text
exposicion actual en percentil 80 historico => exposicion_score = 80
frecuencia actual en percentil 90 historico => frecuencia_score = 90
duracion maxima en percentil 95 historico => duracion_max_score = 95
dispersion en percentil 75 historico => dispersion_score = 75
```

Poblaciones comparables:

```text
modelo de botella
linea
maquina
ramal
tipo de defecto
numero de cavidades activas
turno
cliente
produccion/lote
```

Fallbacks si no hay historico suficiente:

- usar historico de la misma linea;
- usar historico de la misma maquina;
- usar todas las producciones del mismo modelo;
- marcar score como baja confianza;
- mostrar metrica bruta aunque no haya score.

Convencion:

```text
0 = bajo riesgo
100 = alto riesgo
```

Esta convencion debe separarse claramente del score de calidad actual, donde
100 representa mejor calidad.

## 10. Riesgos agregados

No se debe mezclar desde el inicio el riesgo de proceso y el riesgo cliente en
un unico numero. Son lecturas distintas y pueden no coincidir.

### 10.1 Riesgo global de proceso

El riesgo global de proceso prioriza el peor de los dos patrones internos:
persistencia o recurrencia.

Formula propuesta:

```text
riesgo_proceso_global =
0,60 * max(IEP, IIR)
+ 0,40 * mean(IEP, IIR)
```

Ejemplo persistente:

```text
IEP = 85
IIR = 30
riesgo_proceso_global = 0,60 * 85 + 0,40 * 57,5 = 74
```

Ejemplo recurrente:

```text
IEP = 55
IIR = 85
riesgo_proceso_global = 0,60 * 85 + 0,40 * 70 = 79
```

### 10.2 Riesgo cliente absoluto

El riesgo cliente absoluto se mantendra separado:

```text
riesgo_cliente_absoluto = RCA
```

Lectura:

```text
cuantas botellas potencialmente bajo sospecha existen frente al historico comparable?
```

### 10.3 Riesgo cliente relativo

El riesgo cliente relativo tambien se mantendra separado:

```text
riesgo_cliente_relativo = RCR
```

Lectura:

```text
que proporcion del lote, pedido o periodo queda potencialmente afectada?
```

### 10.4 Comparacion proceso-cliente

La aplicacion debe permitir ver desacoples:

```text
riesgo_proceso alto + riesgo_cliente bajo
riesgo_proceso bajo + riesgo_cliente absoluto alto
riesgo_cliente absoluto alto + riesgo_cliente relativo bajo
riesgo_proceso alto + riesgo_cliente alto
```

El objetivo no es forzar una respuesta unica, sino mostrar si una incidencia es
relevante por estabilidad interna, por volumen potencial hacia cliente, o por
ambas razones.

## 11. Tipologia de riesgo

Reglas iniciales:

```text
IEP alto + IIR bajo:
persistente/localizado

IEP medio-bajo + IIR alto:
intermitente/recurrente

IEP alto + IIR alto:
critico mixto

IEP bajo + IIR bajo:
bajo/moderado
```

Cruce con concentracion:

```text
concentracion_top3 alta:
localizado

concentracion_top3 baja + muchas cavidades afectadas:
disperso/sistemico
```

Tipos finales recomendados:

```text
persistente localizado
persistente disperso
intermitente localizado
intermitente disperso
critico mixto localizado
critico mixto sistemico
bajo/moderado
```

## 12. Salidas esperadas

Para cada lote, produccion, turno, maquina o dia:

```text
exposicion_proceso_normalizada
minutos_cavidad_afectados
botellas_sospechosas
fraccion_sospechosa_lote
fraccion_sospechosa_pedido
n_incidencias
duracion_media
duracion_maxima
duracion_p90
frecuencia
recurrencia
concentracion_top3
HHI_cavidad
cavidades_afectadas
defecto_mas_recurrente
IEP
IIR
RCA
RCR
IRL
riesgo_proceso_global
riesgo_cliente_absoluto
riesgo_cliente_relativo
tipo_riesgo
confianza_score
```

Ejemplo de explicacion automatica:

```text
Este lote tiene riesgo alto no por una incidencia larga, sino por repeticion de
eventos cortos y dispersos entre varias cavidades. La exposicion total no es
extrema, pero la inestabilidad del proceso si lo es.
```

Otro ejemplo:

```text
Este lote tiene riesgo alto por una incidencia larga y concentrada en una cavidad
concreta. El problema parece localizado y persistente; revisar cavidad, molde o
ajuste asociado.
```

Ejemplo de desacople proceso-cliente:

```text
Este lote tiene afectacion de proceso moderada porque solo una cavidad de muchas
ha estado afectada, pero el volumen absoluto de botellas sospechosas es alto por
la velocidad de linea. Revisar tanto la incidencia como la posible trazabilidad
de producto.
```

## 13. Visualizaciones propuestas

### 13.1 Mapa de proceso IEP vs IIR

Grafico recomendado:

```text
X = IEP
Y = IIR
tamano = riesgo_proceso_global
color = tipo_riesgo
```

Lectura:

- derecha: riesgo persistente;
- arriba: riesgo recurrente;
- arriba derecha: riesgo critico mixto;
- abajo izquierda: riesgo bajo.

### 13.2 Mapa cliente absoluto vs relativo

Grafico recomendado:

```text
X = riesgo_cliente_absoluto
Y = riesgo_cliente_relativo
tamano = botellas_sospechosas
color = fraccion_sospechosa_lote
```

Lectura:

- derecha: muchas botellas sospechosas en absoluto;
- arriba: alta proporcion del lote o pedido afectada;
- derecha abajo: mucho volumen, pero diluido en lote grande;
- izquierda arriba: poco volumen absoluto, pero lote pequeno muy afectado.

### 13.3 Mapa proceso vs cliente

Grafico recomendado:

```text
X = riesgo_proceso_global
Y = riesgo_cliente_relativo
tamano = botellas_sospechosas
color = tipo_desacople
```

Tipos de desacople:

```text
alto proceso / bajo cliente
bajo proceso / alto cliente
alto ambos
bajo ambos
```

### 13.4 Tabla ejecutiva

Columnas:

```text
lote / produccion
riesgo_proceso_global
riesgo_cliente_absoluto
riesgo_cliente_relativo
IEP
IIR
RCA
RCR
tipo_riesgo
exposicion_proceso_normalizada
botellas_sospechosas
fraccion_sospechosa_lote
n_incidencias
duracion_maxima
concentracion_top3
cavidades_afectadas
defecto_principal
```

### 13.5 Gantt de incidencias

```text
X = tiempo
Y = cavidad, defecto o cavidad+defecto
barra = incidencia abierta
color = defecto, tipo o riesgo
```

Utilidad:

- detectar persistencia;
- ver reaperturas;
- localizar solapes;
- distinguir problemas largos de eventos intermitentes.

### 13.6 Pareto de exposicion

Paretos por:

```text
cavidad
defecto
cavidad + defecto
turno
ramal
```

Metrica:

```text
minutos-cavidad afectados
```

### 13.7 Heatmap cavidad x turno

Color:

```text
exposicion_proceso_normalizada
```

Alternativas:

```text
n_incidencias
duracion_p90
riesgo_proceso_global
riesgo_cliente_relativo
```

### 13.8 Matriz defecto x cavidad

Color:

```text
minutos-cavidad afectados
```

Uso:

- localizar combinaciones fisicas recurrentes;
- detectar defectos sistemicos;
- separar problema de cavidad frente a problema de defecto.

## 14. Integracion con rechazos por ventana

Una segunda fase deberia cruzar incidencias con rechazos temporales ya
disponibles.

Preguntas:

```text
Suben los rechazos durante una incidencia?
Suben antes de la apertura?
Bajan despues del cierre?
Hay avisos que anticipan rechazos?
Hay rechazos sin aviso previo?
Hay avisos frecuentes que nunca terminan en rechazo?
```

Metricas:

```text
rechazo_durante_incidencia
rechazo_fuera_incidencia
ratio_durante_fuera
delta_pre_post
lag_max_correlacion
probabilidad_rechazo_despues_de_aviso
```

Esto no debe implementarse hasta que exista una correspondencia fiable entre
defectos de incidencias y defectos de rechazos por ventana.

## 15. Arquitectura propuesta

El modulo debe ser independiente dentro de la aplicacion:

```text
backend/app/.../incidence_risk
src/components o src/features/incidence-risk
```

Debe seguir los mismos criterios tecnicos ya usados:

- FastAPI;
- Pydantic v2;
- arquitectura por capas;
- repositorios separados;
- motor estadistico aislado;
- schemas explicitos;
- tests de contrato y calculo;
- errores uniformes;
- logs con `request_id`.

### 15.1 Backend

Capas:

```text
api/routes/incidence_risk.py
application/incidence_risk.py
domain/incidence_risk.py
infrastructure/repositories/incidence_risk.py
infrastructure/statistics/incidence_risk.py
schemas/incidence_risk.py
```

Responsabilidades:

- repositorio: leer Parquet de avisos/rechazos;
- dominio: modelos inmutables y conceptos de riesgo;
- estadistica: calcular metricas atomicas, percentiles e indices;
- aplicacion: orquestar `analysis_scope`, `reference_scope`, filtros y cache;
- API: exponer endpoints REST;
- frontend: visualizar y explicar.

El backend debe construir dos datasets:

```text
analysis_dataset:
incidencias que cumplen la seleccion visible del usuario

reference_dataset:
incidencias del historico comparable usado para percentiles
```

Las metricas brutas se calculan sobre `analysis_dataset`.

Los percentiles y scores se calculan comparando las metricas del
`analysis_dataset` contra la distribucion del `reference_dataset`.

### 15.2 Endpoints iniciales

```text
GET  /api/incidence-risk/catalog
POST /api/incidence-risk/query
```

Catalogo:

```text
lineas
maquinas
ramales
cavidades
defectos
lotes
turnos
rango_temporal
```

Query:

```text
analysis_scope
reference_scope
group_by
options
requested_views
```

Ejemplo conceptual:

```json
{
  "analysis_scope": {
    "line": "AV23",
    "production_ids": ["AV23_PROD_001", "AV23_PROD_007"],
    "branches": ["AV23-1", "AV23-2"],
    "defect_codes": [],
    "cavities": []
  },
  "reference_scope": {
    "mode": "same_line_same_bottle_model"
  },
  "group_by": "production",
  "requested_views": [
    "executive_table",
    "process_map",
    "customer_map",
    "gantt",
    "pareto"
  ]
}
```

La API no debe aceptar una referencia implicita ambigua. Si el usuario no la
define, se usara el modo por defecto:

```text
same_line_same_bottle_model
```

Si no hay suficiente historico para ese modo, el backend debe degradar de forma
explicita y devolver una advertencia.

## 16. Frontend propuesto

Crear una nueva pestana:

```text
Riesgo de incidencias
```

La pantalla inicial debe ser deliberadamente similar a la pestana de historico
para reducir friccion de uso, pero con textos que aclaren que los percentiles se
calculan contra una referencia historica completa.

Filtros iniciales:

```text
linea
produccion/lote
maquina
ramal
turno
cavidad
defecto
rango temporal
group_by
reference_scope
```

Comportamiento esperado:

```text
El usuario selecciona que producciones quiere ver.
El backend calcula percentiles contra el historico comparable completo.
La pantalla muestra ambas cosas por separado.
```

Texto visible recomendado:

```text
Analisis visible: producciones seleccionadas.
Referencia estadistica: historico completo de la misma linea y modelo de botella.
```

Bloques:

1. Filtros.
2. Tabla ejecutiva.
3. Mapa de proceso IEP vs IIR.
4. Mapa cliente absoluto vs relativo.
5. Mapa proceso vs cliente.
6. Paretos de exposicion.
7. Gantt de incidencias.
8. Heatmaps cavidad-turno y defecto-cavidad.
9. Panel de explicacion automatica.

La vista debe mantener textos de ayuda sobre:

- convencion de riesgo: 100 = peor;
- definicion de exposicion normalizada;
- diferencia entre riesgo de proceso y riesgo cliente;
- diferencia entre botellas sospechosas absolutas y fraccion sospechosa;
- diferencia entre persistencia y recurrencia;
- diferencia entre concentracion y dispersion;
- poblacion historica usada para percentiles.

## 17. Fases de implementacion

### Fase 1. Contrato y datos

- Crear contrato del modulo propio `incidence-risk`.
- Definir `analysis_scope` y `reference_scope`.
- Auditar columnas reales de `avisos_rechazos_parquet`.
- Definir schema interno.
- Resolver apertura/cierre y duraciones.
- Marcar incidencias abiertas o censuradas.
- Asociar produccion/lote/turno si existe informacion suficiente.
- Identificar cavidades activas.
- Identificar velocidad por cavidad si esta disponible.
- Identificar botellas producidas por lote/franja.
- Identificar tamano de pedido si esta disponible.

### Fase 2. Metricas atomicas

- Duraciones.
- Frecuencia.
- Exposicion de proceso normalizada.
- Minutos-cavidad.
- Botellas sospechosas absolutas.
- Fraccion sospechosa de lote.
- Fraccion sospechosa de pedido si existe dato.
- Concentracion Top 3.
- HHI.
- Recurrencia cavidad+defecto.

### Fase 3. Normalizacion historica

- Construir `reference_dataset` independiente del `analysis_dataset`.
- Usar por defecto historico de misma linea y mismo modelo de botella.
- Percentiles por poblacion comparable.
- Fallbacks si no hay suficiente historico.
- Indicador de confianza del score.
- Resumen visible de poblacion de referencia.

### Fase 4. Indices

- IEP.
- IIR.
- RCA.
- RCR.
- IRL.
- Riesgo de proceso global.
- Riesgo cliente absoluto.
- Riesgo cliente relativo.
- Tipo de riesgo.
- Tipo de desacople proceso-cliente.
- Explicacion textual.

### Fase 5. API

- Endpoint catalogo.
- Endpoint query.
- Schemas Pydantic.
- Validacion explicita de `analysis_scope`.
- Validacion explicita de `reference_scope`.
- Errores uniformes.
- Logging con `request_id`.
- Tests.

### Fase 6. Frontend

- Nueva pestana/modulo `Riesgo de incidencias`.
- Filtros similares a historico.
- Selector o texto de referencia historica.
- Tabla resumen.
- Mapa IEP/IIR.
- Mapa cliente absoluto/relativo.
- Mapa proceso/cliente.
- Paretos.
- Gantt.
- Heatmaps.
- Textos explicativos.

### Fase 7. Cruce con rechazos

- Activar solo cuando exista correlacion fiable entre defectos de incidencias y
  defectos de rechazos por ventana.
- Comparar durante/fuera.
- Analisis pre/post.
- Lags temporales.

## 18. Criterios de calidad tecnica

Mantener los criterios ya usados en el proyecto:

- Python moderno.
- FastAPI.
- Pydantic v2.
- Arquitectura por capas.
- Type hints estrictos.
- `mypy`.
- `ruff`.
- `pytest`.
- `pathlib.Path`.
- modulos pequenos.
- funciones legibles.
- sin duplicidad.
- sin hardcodeos injustificados.
- errores API uniformes.
- logging estructurado.
- trazabilidad con `request_id`.
- frontend claro e interpretable.
- graficas con significado fisico.

## 19. Criterios de aceptacion

La primera version se considerara correcta si:

- existe como modulo propio `incidence-risk`;
- separa `analysis_scope` y `reference_scope`;
- calcula metricas brutas solo sobre la seleccion visible;
- calcula percentiles contra el historico comparable completo;
- usa por defecto misma linea y mismo modelo de botella como referencia;
- muestra en frontend el tamano y criterio de la poblacion de referencia;
- calcula exposicion de proceso normalizada por lote/produccion;
- calcula botellas sospechosas absolutas cuando exista velocidad;
- calcula fraccion sospechosa de lote o pedido cuando exista denominador;
- calcula frecuencia y duraciones;
- diferencia persistencia frente a recurrencia;
- calcula concentracion Top 3 y HHI;
- calcula IEP, IIR y riesgo de proceso global;
- calcula RCA y RCR cuando existan datos suficientes;
- clasifica tipo de riesgo;
- identifica desacoples entre riesgo de proceso y riesgo cliente;
- muestra tabla ejecutiva;
- muestra mapa IEP vs IIR;
- muestra mapa cliente absoluto vs relativo;
- muestra mapa proceso vs cliente;
- muestra al menos un Pareto de exposicion;
- explica claramente la formula usada;
- conserva metricas brutas junto a scores normalizados;
- no mezcla convencion de calidad con convencion de riesgo.
- no usa gravedad como multiplicador en la primera version.

## 20. Decisiones pendientes

Antes de implementar hay que decidir:

- como identificar lote o produccion en avisos/rechazos;
- como identificar modelo de botella para construir la referencia historica;
- cuantos lotes/producciones minimos exige `reference_scope`;
- que fallback usar si no hay historico de mismo modelo;
- que hacer con incidencias sin cierre;
- como obtener cavidades activas por lote;
- como obtener velocidad por cavidad o velocidad de linea;
- como calcular botellas producidas por lote/franja;
- como incorporar tamano de pedido si existe;
- si una incidencia puede afectar varias cavidades;
- como mapear defectos de incidencias contra defectos de rechazos;
- poblacion historica minima para percentiles;
- reglas iniciales de alto/medio/bajo para tipologia;
- reglas iniciales de desacople proceso-cliente;
- si el riesgo se calcula por lote, produccion, turno o todos ellos desde la
  primera version.

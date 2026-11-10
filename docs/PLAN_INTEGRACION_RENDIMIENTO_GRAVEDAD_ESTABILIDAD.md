# Plan de integracion: rendimiento zona caliente, gravedad de defectos y estabilidad de inspeccion

## 1. Objetivo

Definir como incorporar tres nuevas lineas de analisis en el dashboard de
calidad de produccion:

1. Rendimiento en zona caliente.
2. Categorizacion de defectos por gravedad.
3. Estabilidad temporal de las maquinas de inspeccion.

El objetivo no es implementar todavia, sino fijar un enfoque funcional,
estadistico y visual coherente con la aplicacion actual.

La idea general es evitar mezclar conceptos distintos en un unico score opaco.
Cada bloque debe aportar una lectura propia:

- rendimiento de fabricacion;
- calidad/rechazo por severidad;
- estabilidad y consistencia de inspeccion;
- riesgo de incidencias, ya tratado en el modulo actual.

Despues, cuando los datos esten entendidos, se podran cruzar estos bloques para
construir indicadores agregados mas maduros.

## 2. Principio de integracion en dashboard

La aplicacion deberia mantener una pantalla principal de lectura ejecutiva y
modulos especificos para el analisis fino.

Propuesta:

1. **Historico de rechazos**
   - Mantener como vista principal de comportamiento temporal y comparativo.

2. **Riesgo de incidencias**
   - Mantener como modulo propio para avisos/rechazos con duracion,
     recurrencia, exposicion y dispersion.

3. **Rendimiento zona caliente**
   - Nuevo bloque o submodulo propio.
   - Debe poder cruzarse con producciones, turnos y dias.

4. **Gravedad de defectos**
   - Debe actuar como capa transversal sobre historico y riesgo.
   - No conviene crear cinco pestanas aisladas desde el inicio si eso duplica
     toda la interfaz.
   - Conviene introducir filtros, agrupaciones y tarjetas por gravedad.

5. **Estabilidad de inspeccion**
   - Nuevo bloque especifico de diagnostico.
   - Debe centrarse en continuidad temporal, cavidades, ramales y diferencias
     entre maquinas/ramales.

## 3. Rendimiento en zona caliente

### 3.1 Datos esperados

Se dispondra de una tabla con rendimiento cada 10 minutos:

```text
C:\Users\iker.villen\Desktop\AI_tool\Datos\Rendimiento_ZC.xlsx
```

Reglas conocidas del fichero:

- Cada franja temporal de 10 minutos aparece con dos filas:
  - `Linea 14` en zona caliente corresponde a `AV23`.
  - `Linea 16` en zona caliente corresponde a `AV32`.
- El fichero puede contener fechas que no pertenecen a producciones actualmente
  disponibles en el dashboard.
- La primera operacion de ingesta debe filtrar por las fechas/rangos de las
  producciones existentes en el dashboard.
- Solo deben conservarse los registros de zona caliente que caigan dentro de
  las mismas producciones ya particionadas para historico/calidad.

Campos a confirmar cuando se revise el Excel:

- timestamp o fecha/hora de medicion;
- linea de zona caliente, con equivalencia ZC 14 -> AV23 y ZC 16 -> AV32;
- ramal, si existe;
- produccion o lote, si viene informado; si no viene, se asignara por rango
  temporal contra el catalogo de producciones existente;
- `Rueda`;
- `TEORICO_A`;
- `TEORICO_B`;
- posibles estados de maquina, parada o cambio.

El rendimiento no viene como columna directa. Debe calcularse:

```text
teorico_total = TEORICO_A + TEORICO_B
rendimiento_zc = Rueda / teorico_total
```

Reglas de calidad del calculo:

- si `teorico_total <= 0`, la ventana debe marcarse como no valida;
- si falta `Rueda`, `TEORICO_A` o `TEORICO_B`, la ventana debe marcarse como
  incompleta;
- el backend debe conservar tanto numerador como denominador para trazabilidad;
- el dashboard debe mostrar cobertura de datos para no comparar turnos
  incompletos como si fueran equivalentes.

### 3.2 Agregacion inicial

Primer enfoque solicitado:

- calcular promedio por lote o turno de 8 horas;
- calcular rendimiento diario a partir de los bloques de 8 horas.

Conviene definir dos niveles:

1. **Ventana base de 10 minutos**
   - Dato bruto original.
   - Util para trazabilidad y analisis fino.
   - Cada ventana debe quedar asignada a:
     - produccion;
     - linea AV23/AV32;
     - timestamp;
     - turno operativo;
     - dia operativo.

2. **Bloque operativo de 8 horas**
   - Unidad natural para turno/lote.
   - Permite comparar manana, tarde, noche o bloques equivalentes.
   - Debe calcularse a partir de las ventanas de 10 minutos validas.
   - La media puede calcularse de dos maneras:
     - media simple de rendimientos de 10 minutos;
     - rendimiento agregado del bloque:
       `sum(Rueda) / sum(TEORICO_A + TEORICO_B)`.
   - Recomendacion: usar como metrica principal el rendimiento agregado del
     bloque, porque pondera correctamente por volumen teorico.

3. **Dia operativo**
   - Agregado de los bloques de 8 horas.
   - Debe respetar la convencion de turno nocturno: el turno 22:00-06:00 se
     asigna al dia en que empieza.
   - Recomendacion: calcular rendimiento diario como
     `sum(Rueda) / sum(teorico_total)` del dia operativo, no como media simple
     de turnos, salvo que se muestre explicitamente como metrica secundaria.

### 3.3 Metricas propuestas

Por produccion:

- rendimiento medio;
- rendimiento mediano;
- p10, p25, p75, p90;
- minimo observado;
- desviacion o MAD;
- porcentaje de ventanas por debajo de referencia;
- peor bloque de 8 horas;
- mejor bloque de 8 horas;
- tendencia temporal del rendimiento;
- cobertura de datos.

Por turno/lote de 8 horas:

- media de rendimiento;
- mediana;
- minimo;
- desviacion robusta;
- numero de ventanas disponibles;
- cobertura real frente a 48 ventanas esperadas si la base es 10 minutos;
- etiqueta de bloque incompleto si falta dato.

Por dia:

- rendimiento medio diario;
- rendimiento ponderado por cobertura;
- diferencia entre turnos del mismo dia;
- rango diario: mejor turno - peor turno;
- estabilidad diaria.

### 3.4 Visualizaciones recomendadas

En una primera version:

1. **Tarjeta ejecutiva por produccion**
   - rendimiento medio zona caliente;
   - peor turno;
   - rendimiento diario medio;
   - cobertura.

2. **Serie temporal**
   - rendimiento cada 10 minutos;
   - media movil;
   - franjas por turno;
   - lineas de referencia si existen objetivos internos.

3. **Barras por turno/lote de 8 horas**
   - una barra por bloque operativo;
   - color por rendimiento relativo;
   - marcar bloques incompletos.

4. **Heatmap dia x turno**
   - filas: dias;
   - columnas: manana/tarde/noche;
   - color: rendimiento medio.

5. **Comparativa entre producciones**
   - boxplot o violin por produccion;
   - ranking de rendimiento medio y estabilidad.

### 3.5 Donde integrarlo

No deberia modificar de entrada los ejes actuales de riesgo ni calidad.

Motivo:

- el rendimiento zona caliente mide eficiencia/productividad;
- los rechazos miden calidad detectada;
- las incidencias miden riesgo operativo o cliente;
- mezclar rendimiento con calidad en un score unico desde el inicio puede
  generar conclusiones confusas.

Propuesta:

1. Anadir una nueva pestana o modulo:
   - `Rendimiento zona caliente`.

2. Anadir resumen en dashboard principal:
   - tarjeta por produccion;
   - semaforo de rendimiento;
   - peor turno;
   - tendencia.

3. Mantenerlo inicialmente como dimension explicativa, no como penalizacion del
   score de calidad.

### 3.6 Cruces utiles con calidad

Una vez validado:

- rendimiento bajo vs aumento de rechazos;
- cambios de rendimiento vs cambios en defectos dominantes;
- rendimiento por turno vs defectos por turno;
- rendimiento por ramal vs diferencias de rechazos entre ramales;
- rendimiento bajo sostenido como posible indicador de ajuste, parada,
  transicion o inestabilidad.

No debe asumirse causalidad. Debe mostrarse como correlacion exploratoria.

## 4. Categorizacion de defectos por gravedad

### 4.1 Datos esperados

Se dispondra de una tabla de clasificacion de defectos con cinco categorias:

- Critico.
- Grave.
- Mayor.
- Menor.
- Leve.

Fichero esperado:

```text
C:\Users\iker.villen\Desktop\AI_tool\Datos\Tabla defectos con criticidades.xlsx
```

Reglas conocidas:

- La primera columna contiene el codigo de defecto antiguo.
- Ese codigo antiguo es el que actualmente necesitamos para cruzar con los
  defectos del historico y de incidencias.
- Existe una columna llamada `Gravedad o Generica`.
- La columna `Gravedad o Generica` clasifica cada defecto como:
  - Critico.
  - Grave.
  - Mayor.
  - Menor.
  - Leve.

Campos a confirmar al revisar el Excel:

- codigo de defecto usado en AV-2024-2026;
- descripcion;
- categoria de gravedad desde `Gravedad o Generica`;
- posible familia o grupo;
- version de la tabla;
- fecha de validez, si aplica;
- si hay defectos sin categoria.

### 4.1.1 Regla de equivalencia defecto-gravedad

La tabla debe convertirse en una tabla maestra normalizada:

```text
old_defect_code
severity_category
source_file
source_column
```

Proceso:

1. Leer `Tabla defectos con criticidades.xlsx`.
2. Tomar la primera columna como `old_defect_code`.
3. Tomar `Gravedad o Generica` como `severity_category`.
4. Normalizar textos:
   - quitar espacios laterales;
   - homogeneizar mayusculas/minusculas;
   - mapear variantes a categorias canonicas:
     `Critico`, `Grave`, `Mayor`, `Menor`, `Leve`.
5. Para cada rechazo/defecto del historico:
   - buscar su codigo en `old_defect_code`;
   - anexar `severity_category`.
6. Para cada aviso/rechazo de incidencias:
   - buscar el codigo de defecto antiguo correspondiente;
   - anexar `severity_category`.
7. Si no existe equivalencia:
   - marcar como `Sin clasificar`;
   - conservar el defecto en los calculos;
   - mostrar el porcentaje sin clasificar en dashboard.

No se debe descartar ningun defecto por no tener gravedad asignada.

### 4.2 Enfoque recomendado

La gravedad debe introducirse primero como capa de segmentacion, no como
multiplicador directo del score.

Motivo:

- evita subjetividad prematura;
- permite ver si las categorias tienen sentido industrial;
- permite detectar errores de mapeo;
- evita que un peso arbitrario distorsione el analisis.

Fase 1:

- mapear cada defecto a su gravedad;
- calcular metricas por gravedad;
- mostrar distribuciones y evoluciones separadas.

Fase 2:

- permitir ponderaciones configurables por gravedad;
- construir scores ponderados;
- comparar score no ponderado vs score ponderado.

Fase 3:

- fijar ponderaciones solo si calidad/planta las valida.

### 4.3 Metricas por gravedad

Por produccion:

- eventos totales por gravedad;
- porcentaje de eventos por gravedad;
- mediana por ventana;
- p90/p95 por ventana;
- ventanas con presencia de defectos criticos/graves;
- episodios sostenidos de defectos criticos/graves;
- defectos dominantes dentro de cada gravedad;
- ramales/cavidades mas afectados por gravedad.

Por turno:

- distribucion de gravedad por turno;
- turno con mayor presencia de criticos/graves;
- comparacion del mismo turno entre producciones;
- cobertura y ventanas validas.

Por dia:

- presencia diaria de defectos criticos/graves;
- dias con perfil de gravedad anomalo;
- tendencia de severidad.

### 4.4 Visualizaciones recomendadas

En dashboard principal:

1. **Tarjeta de severidad**
   - porcentaje de eventos criticos+graves;
   - numero de ventanas con criticos;
   - peor categoria dominante;
   - defectos criticos principales.

2. **Barra apilada por produccion**
   - distribucion porcentual de eventos por gravedad.
   - colores sobrios:
     - Critico: rojo oscuro.
     - Grave: rojo/naranja.
     - Mayor: ambar.
     - Menor: azul/gris.
     - Leve: gris/verde suave.

3. **Serie temporal por gravedad**
   - selector de categorias;
   - posibilidad de ver global o solo critico/grave.

4. **Pareto por gravedad**
   - dentro de cada produccion;
   - defecto dominante dentro de criticos, graves, etc.

Modulo detallado:

- pestana o subpanel `Gravedad de defectos`;
- filtros por categoria;
- matriz produccion x gravedad;
- boxplots por categoria;
- heatmap dia/turno x gravedad;
- comparativa de ramales por gravedad.

### 4.5 Pestanas o filtros

No conviene crear cinco pestanas independientes desde el inicio.

Mejor:

- un unico modulo de gravedad;
- filtros multi-seleccion de gravedad;
- vistas comparativas que muestren las cinco categorias juntas;
- posibilidad de aislar una categoria cuando interese.

La separacion por pestanas solo tendria sentido si cada categoria necesitara
analisis propios muy distintos. En principio, no parece necesario.

### 4.6 Score de calidad ponderado por gravedad

Fase futura:

Definir pesos configurables, por ejemplo:

```text
Critico = 100
Grave   = 60
Mayor   = 30
Menor   = 10
Leve    = 3
```

No son pesos propuestos como definitivos, solo estructura tecnica.

Calculos posibles:

```text
eventos_ponderados =
  sum(eventos_categoria * peso_categoria)

tasa_ponderada_proxy =
  eventos_ponderados / exposicion_nominal
```

Para incidencias:

```text
minutos_ponderados =
  sum(duracion_evento * peso_gravedad_defecto)
```

Conviene mostrar siempre:

- score sin ponderar;
- score ponderado;
- contribucion por categoria;
- sensibilidad a pesos.

### 4.7 Cuidado metodologico

La gravedad no debe ocultar volumen.

Ejemplo:

- pocos defectos criticos pueden ser muy importantes;
- muchos defectos leves pueden indicar inestabilidad operacional;
- ambas lecturas deben coexistir.

Por tanto, el dashboard debe responder por separado:

- que volumen hay;
- que gravedad tiene;
- en que momento aparece;
- en que ramal/cavidad;
- si es sostenido o puntual.

## 5. Estabilidad de maquinas de inspeccion

### 5.1 Objetivo

Analizar si las maquinas de inspeccion detectan defectos de forma estable,
coherente y sostenida en el tiempo, o si aparecen patrones sospechosos:

- picos aislados;
- discontinuidades;
- intermitencias;
- diferencias fuertes entre ramales;
- cavidades con deteccion irregular;
- cambios bruscos de sensibilidad aparente.

### 5.2 Datos necesarios

Campos a revisar cuando se disponga de los datos:

- maquina o linea;
- ramal;
- cavidad;
- defecto detectado;
- timestamp;
- conteo o evento;
- estado de la inspeccion;
- sensibilidad/configuracion si existe;
- avisos/rechazos asociados;
- total inspeccionado si existe.

La utilidad del modulo depende mucho de si tenemos solo rechazos detectados o
tambien datos de inspeccion total/configuracion.

### 5.3 Metricas de estabilidad temporal

Por maquina/ramal/cavidad:

- conteos por ventana;
- mediana y MAD por ventana;
- coeficiente de variacion robusto;
- numero de picos;
- episodios sostenidos;
- ventanas sin deteccion tras actividad previa;
- cambios de nivel;
- autocorrelacion;
- intermitencia: porcentaje de ventanas activas aisladas;
- persistencia: duracion media de episodios.

Por defecto:

- estabilidad temporal del defecto;
- cavidades donde aparece;
- ramales donde aparece;
- aparicion sostenida vs puntual;
- coincidencia entre ramales.

### 5.4 Consistencia entre ramales

Analisis recomendados:

1. **Comparacion de tasas proxy por ramal**
   - mediana;
   - p90/p95;
   - dispersion;
   - diferencia absoluta y relativa.

2. **Correlacion temporal entre ramales**
   - si ambos ramales suben/bajan a la vez;
   - lag entre ramales;
   - correlacion por defecto dominante.

3. **Divergencia de composicion**
   - distribucion de defectos por ramal;
   - distancia Jensen-Shannon o coseno entre perfiles;
   - defectos exclusivos o sobrerrepresentados por ramal.

4. **Cavidades anomalas**
   - cavidades con deteccion sostenida;
   - cavidades con intermitencia extrema;
   - cavidades con salto brusco.

### 5.5 Visualizaciones recomendadas

Panel principal:

- tarjeta de estabilidad por maquina;
- peor ramal;
- cavidad mas inestable;
- defectos con mayor intermitencia;
- indicador de diferencia entre ramales.

Modulo detallado:

1. **Serie temporal por ramal**
   - nivel observado;
   - EWMA;
   - anomalias robustas;
   - cambios sostenidos.

2. **Heatmap cavidad x tiempo**
   - color por conteo/tasa proxy;
   - permite ver cavidades persistentemente activas.

3. **Matriz ramal x defecto**
   - contribucion por defecto en cada ramal;
   - permite ver desajustes de perfil.

4. **Mapa de estabilidad**
   - eje X: persistencia;
   - eje Y: intermitencia;
   - tamano: volumen;
   - color: ramal o defecto.

5. **Ranking de cavidades**
   - cavidades mas persistentes;
   - cavidades mas intermitentes;
   - cavidades con mayor cambio de nivel.

### 5.6 Lectura industrial

Interpretaciones posibles:

- deteccion sostenida en una cavidad:
  - posible problema real de cavidad/molde/ajuste;
  - podria ser estable y atacable.

- picos intermitentes en muchas cavidades:
  - posible inestabilidad de proceso;
  - posible cambio de criterio/sensibilidad;
  - posible efecto de turno o limpieza.

- diferencias fuertes entre ramales:
  - posible diferencia fisica real;
  - posible ajuste distinto de maquinas;
  - posible problema de datos o configuracion.

El dashboard debe evitar afirmar causalidad sin datos de configuracion o
validacion de planta.

## 6. Integracion conjunta de los tres bloques

### 6.1 Vista ejecutiva por produccion

Para cada produccion seleccionada:

- calidad historica:
  - mediana, p95, tendencia, estabilidad.

- riesgo incidencias:
  - cliente;
  - produccion/contencion;
  - inestabilidad;
  - dispersion entre cavidades.

- rendimiento zona caliente:
  - rendimiento medio;
  - peor turno;
  - estabilidad de rendimiento.

- gravedad:
  - porcentaje critico+grave;
  - ventanas con criticos;
  - categoria dominante.

- inspeccion:
  - estabilidad temporal;
  - ramal mas desviado;
  - cavidad mas inestable.

### 6.2 No mezclar demasiado pronto

No conviene que rendimiento, gravedad e inspeccion modifiquen automaticamente
los scores existentes desde la primera version.

Primero deben mostrarse como dimensiones separadas.

Despues, si los resultados son coherentes, se podria crear un score compuesto
con bloques configurables:

```text
score_lote =
  w_calidad * score_calidad
  + w_incidencias * score_incidencias
  + w_gravedad * score_gravedad
  + w_inspeccion * score_inspeccion
  + w_rendimiento * penalizacion_rendimiento
```

Pero debe ser una fase posterior y configurable.

### 6.3 Cruces de alto valor

Analisis recomendados:

- bajo rendimiento zona caliente + aumento de defectos;
- bajo rendimiento + mayor severidad;
- defectos criticos concentrados en un turno;
- inestabilidad de inspeccion + aumento de rechazos;
- ramal con peor estabilidad + peor rendimiento;
- cavidad persistente + incidencia larga;
- dispersion entre cavidades + defectos graves.

## 7. Propuesta de arquitectura funcional

### 7.1 Nuevos modulos

1. `Rendimiento zona caliente`
2. `Gravedad de defectos`
3. `Estabilidad inspeccion`

Podrian aparecer como subpestanas o tarjetas expandibles dentro de un area
`Analisis complementarios`.

### 7.2 Backend

Mantener arquitectura por capas:

- `domain`
  - modelos de rendimiento;
  - modelos de gravedad;
  - modelos de estabilidad inspeccion.

- `schemas`
  - contratos REST;
  - opciones de agregacion;
  - filtros.

- `application`
  - casos de uso por modulo.

- `infrastructure/repositories`
  - lectura parquet/excel preprocesado.

- `infrastructure/statistics`
  - calculos estadisticos.

### 7.3 Frontend

Componentes separados:

- `HotEndYieldPanel`
- `DefectSeverityPanel`
- `InspectionStabilityPanel`

Evitar componentes gigantes y duplicados.

## 8. Preprocesado de datos

### 8.1 Rendimiento zona caliente

Convertir Excel a parquet:

- normalizar timestamps;
- filtrar a los rangos temporales de producciones existentes en el dashboard;
- mapear linea de zona caliente:
  - ZC 14 -> AV23;
  - ZC 16 -> AV32;
- asignar produccion por solape temporal con el catalogo actual;
- calcular `teorico_total = TEORICO_A + TEORICO_B`;
- calcular `rendimiento_zc = Rueda / teorico_total`;
- conservar columnas base:
  - `timestamp`;
  - `zc_line`;
  - `line`;
  - `production_id`;
  - `rueda`;
  - `teorico_a`;
  - `teorico_b`;
  - `teorico_total`;
  - `rendimiento_zc`;
  - `is_valid`;
  - `invalid_reason`;
- validar cobertura;
- separar por linea y produccion si procede;
- guardar metadata de rango temporal.

Agregados derivados:

- tabla de ventanas de 10 minutos;
- tabla de turnos/lotes de 8 horas;
- tabla diaria por produccion y linea.

Para turno y dia, la metrica principal debe ser:

```text
rendimiento_agregado = sum(rueda) / sum(teorico_total)
```

La media simple de rendimientos de 10 minutos puede mantenerse como metrica
secundaria para diagnostico, pero no deberia ser la referencia principal si el
volumen teorico varia entre ventanas.

### 8.2 Gravedad defectos

Crear tabla maestra:

```text
old_defect_code
defect_description
severity_category
severity_version
valid_from
valid_to
source_file
```

Reglas:

- la primera columna del Excel se interpreta como codigo antiguo;
- `Gravedad o Generica` se interpreta como categoria canonica;
- defectos sin mapeo deben aparecer como `Sin clasificar`;
- no deben eliminarse silenciosamente;
- el dashboard debe mostrar porcentaje sin clasificar.

La tabla maestra debe usarse como lookup transversal en:

- historico de rechazos;
- analisis estadistico;
- defectos dominantes;
- riesgo de incidencias;
- futuros scores ponderados por gravedad.

### 8.3 Estabilidad inspeccion

Dependera de la estructura real de los datos.

Primera tarea:

- inventario de columnas;
- granularidad temporal;
- relacion con linea/ramal/cavidad;
- existencia de totales inspeccionados;
- existencia de configuracion de maquina.

## 9. Decisiones pendientes

Cuando se revisen los Excel:

1. Nombre exacto de la columna timestamp en `Rendimiento_ZC.xlsx`.
2. Si `Rendimiento_ZC.xlsx` trae informacion adicional de ramal, lote o estado.
3. Si los turnos de rendimiento deben usar 06-14, 14-22, 22-06.
4. Como tratar ventanas ZC que solapan parcialmente con el inicio/fin de una
   produccion.
5. Si la tabla de gravedad cubre todos los codigos de defecto usados.
6. Como tratar duplicados en la tabla de criticidades si un codigo antiguo
   aparece mas de una vez.
7. Si los pesos por gravedad seran configurables o fijos.
8. Que datos reales existen de maquinas de inspeccion.
9. Si hay total inspeccionado por ramal/cavidad.
10. Si existen cambios de configuracion de inspeccion.
11. Si rendimiento debe afectar scores o solo mostrarse como dimension
    explicativa.

Reglas ya fijadas:

- ZC 14 corresponde a AV23.
- ZC 16 corresponde a AV32.
- El rendimiento ZC se calcula como `Rueda / (TEORICO_A + TEORICO_B)`.
- La primera columna de `Tabla defectos con criticidades.xlsx` es el codigo
  antiguo de defecto necesario.
- `Gravedad o Generica` es la columna de severidad.

## 10. Primera version recomendada

La primera implementacion deberia ser conservadora:

1. Ingestar y validar rendimiento zona caliente.
2. Mostrar rendimiento por turno de 8 horas y rendimiento diario.
3. Ingestar tabla de gravedad.
4. Anadir filtro y tarjetas por gravedad.
5. Mostrar distribucion de severidad por produccion.
6. Crear modulo inicial de estabilidad con:
   - series por ramal;
   - heatmap cavidad x tiempo;
   - ranking de cavidades intermitentes/persistentes.
7. No crear todavia un score global unico que mezcle todo.

## 11. Segunda version

Una vez validada la lectura:

1. Cruzar rendimiento con calidad y gravedad.
2. Cruzar estabilidad de inspeccion con defectos por cavidad/ramal.
3. Anadir ponderacion configurable por gravedad.
4. Anadir score de severidad.
5. Anadir score compuesto de lote, siempre con desglose de contribuciones.

## 12. Riesgos

- Mezclar rendimiento y calidad puede generar lecturas erroneas.
- Ponderar gravedad demasiado pronto puede introducir subjetividad.
- Sin total inspeccionado, muchas metricas seguiran siendo proxy.
- Si la tabla de gravedad no cubre todos los defectos, el score puede quedar
  sesgado.
- Si los datos de inspeccion no incluyen configuracion o totales, la estabilidad
  solo podra inferirse de patrones de deteccion.

## 13. Criterio de exito

La integracion sera util si permite responder:

- Que produccion tuvo peor rendimiento zona caliente y en que turno.
- Si una produccion mala lo fue por volumen, por gravedad o por ambas.
- Si los defectos criticos/graves aparecen sostenidos o puntuales.
- Si las maquinas de inspeccion se comportan de forma estable en el tiempo.
- Si hay ramales o cavidades con comportamiento diferencial.
- Si rendimiento, gravedad e inspeccion ayudan a explicar por que una produccion
  fue peor que otra.

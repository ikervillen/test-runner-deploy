# Plan de comparación estadística de producciones

## 1. Objetivo

Ampliar el visualizador histórico para que, después de consultar una o varias
producciones, el usuario pueda solicitar un análisis estadístico reproducible
de exactamente esa selección.

La ampliación tendrá dos partes coordinadas:

1. Un módulo estadístico en el backend que prepare las observaciones, calcule
   descriptivos, comparaciones, tamaños de efecto, diagnósticos y gráficos.
2. Un modo de comparación en el frontend que divida la zona de trabajo:
   - Izquierda: filtros, resumen y gráficas temporales actuales.
   - Derecha: configuración, resultados y gráficos estadísticos.

El objetivo no es producir únicamente un ranking. El sistema debe responder:

- Qué producción presenta peor calidad.
- Cuánta diferencia existe respecto a las demás.
- Con qué incertidumbre se estima esa diferencia.
- Si la diferencia es estadísticamente compatible con una diferencia real.
- Qué ramales, defectos, cavidades, secciones, códigos ZF, colores o periodos
  explican la diferencia.
- Si el problema es sostenido, puntual, creciente o inestable.

Este documento es únicamente un plan. No implica cambios de código.

## 2. Principios analíticos

### 2.1 No comparar recuentos sin exposición

`rejected_count` depende del volumen inspeccionado y de la duración. No será la
métrica principal para afirmar que una producción es peor.

Hasta disponer del número real de botellas fabricadas o inspeccionadas, se
adoptará una exposición nominal común:

```text
assumed_line_rate_per_minute = 100 botellas/minuto
nominal_line_exposure_10m = 1.000 botellas
```

La métrica provisional será:

```text
proxy_defect_event_rate =
    defect_events / nominal_exposure
```

Se mostrará como **eventos de defecto por 1.000 botellas nominales** o
**tasa proxy**, nunca como tasa real medida. Esta tasa puede ser mayor que `1`
defecto por botella si una botella puede aportar varios defectos.

Con una exposición constante de `1.000` botellas por ventana de `10m`, el valor
de eventos por 1.000 botellas nominales coincide numéricamente con el recuento:

```text
10 eventos / 1.000 botellas nominales = 10 eventos por 1.000
```

Por tanto, la transformación no añade información ni cambia correlaciones,
tendencias o rankings dentro de grupos con igual exposición. Su utilidad es:

- Introducir una unidad comparativa consistente.
- Preparar el sistema para recibir exposiciones reales.
- Escalar correctamente ventanas de distinta duración.
- Hacer explícita la hipótesis de volumen utilizada.

Cuando exista exposición medida, la comparación normalizada será:

```text
measured_defect_event_rate = defect_events / measured_exposure
```

Solo si se dispone de un identificador que permita contar botellas únicas se
calculará:

```text
nonconforming_bottle_proportion =
    unique_rejected_bottles / unique_inspected_bottles
```

Esta segunda métrica sí será una proporción binomial en el intervalo `[0, 1]` y
podrá expresarse como porcentaje.

`rejected_count` seguirá siendo útil para:

- Medir impacto absoluto.
- Construir Pareto de contribuciones.
- Localizar periodos con acumulación de eventos.

### 2.2 Separar defectos de botellas únicas

Con el modelo actual, `LISTA_DEFECTOS_RECH` representa registros o eventos de
defecto. No debe etiquetarse automáticamente como número de botellas únicas
rechazadas ni alimentar pruebas binomiales.

El análisis mostrará explícitamente:

- `quality_unit = defect_event` mientras no exista identificador de botella.
- `exposure_source = assumed_constant` mientras se use la hipótesis nominal.
- Ritmo nominal, exposición de ventana y regla de reparto por ramal.
- Regla de deduplicación utilizada cuando exista `inspected_count` medido.
- Fórmula y filtros aplicados al numerador y al denominador.

Antes de implementar la inferencia se auditará también el nombre actual
`rejection_rate`. Si representa eventos por inspección, el frontend deberá
mostrar una etiqueta semánticamente correcta aunque se mantenga temporalmente
el identificador técnico por compatibilidad.

Si posteriormente se incorpora un identificador fiable de botella, se añadirá
otra unidad analítica sin cambiar silenciosamente la definición existente.

### 2.3 Comparabilidad de la tasa proxy

La tasa proxy será adecuada para estudiar provisionalmente:

- Cambios sostenidos dentro de una producción.
- Inestabilidad por horas o días.
- Diferencias entre producciones del mismo modelo.
- Diferencias entre ramales bajo el supuesto de reparto uniforme.
- Episodios de empeoramiento relativos a una referencia reciente.

No permitirá distinguir entre:

- Deterioro real de calidad.
- Aumento de velocidad de fabricación.
- Parada o reducción parcial de la línea.
- Cambios en cobertura o sensibilidad de inspección.
- Ventanas incompletas.

Las conclusiones se formularán como **degradación proxy por aumento de eventos
de defecto**, no como deterioro demostrado de la proporción real de botellas
no conformes.

### 2.4 Comparar unidades equivalentes

No se compararán directamente observaciones con distinto significado:

- Una tasa de `10m` no se mezclará con una tasa de `4h`.
- Las comparaciones por ramal se harán entre ramales o mediante un agregado de
  línea definido.
- Los filtros seleccionados en la consulta temporal se conservarán.
- Las producciones con exposición insuficiente quedarán marcadas, no
  artificialmente estabilizadas con ceros.

### 2.5 Significancia, magnitud y relevancia industrial

Cada comparación inferencial deberá informar conjuntamente:

- Estimación de la diferencia.
- Intervalo de confianza.
- Tamaño de efecto.
- Valor p ajustado cuando existan comparaciones múltiples.
- Relevancia industrial respecto a un umbral configurable.

Un resultado no se declarará importante solo por tener `p < 0.05`.

### 2.6 Dependencia temporal

Las ventanas consecutivas de una producción pueden estar autocorrelacionadas.
Por ello:

- No se asumirán siempre observaciones independientes.
- El bootstrap principal será por bloques temporales, no necesariamente por
  puntos individuales.
- Los contrastes simples se presentarán con sus supuestos y advertencias.
- Los análisis diarios usarán el día como unidad de agrupación cuando proceda.

## 3. Alcance funcional

### 3.1 Entrada del análisis

El botón **Calcular estadísticas** utilizará la última consulta temporal
ejecutada con éxito:

- Producciones seleccionadas.
- Ramales seleccionados.
- Métrica.
- Ventana: `10m`, `1h` o `4h`.
- Filtros avanzados.

No se analizará una selección que todavía no haya sido consultada. Si el
usuario modifica un selector después de consultar, los resultados temporales y
estadísticos se marcarán como desactualizados hasta ejecutar de nuevo
**Consultar**.

La métrica seleccionada se describirá tal como aparece en las series. Sin
embargo, si el usuario selecciona un recuento absoluto, el backend cargará
también la exposición necesaria para construir una tasa normalizada. El ranking
de calidad no se basará en recuentos absolutos entre producciones de diferente
duración o volumen.

### 3.2 Configuración estadística adicional

El panel derecho permitirá definir:

- Nivel de confianza: por defecto `95%`.
- Producción de referencia opcional.
- Umbral industrial de tasa, si existe.
- Diferencia mínima relevante, con unidad explícita.
- Número de remuestreos bootstrap: valor recomendado `10.000`.
- Semilla aleatoria: fija por defecto para reproducibilidad.
- Unidad de comparación: línea completa o ramal.
- Alineación temporal: calendario o tiempo relativo de producción.
- Análisis y gráficos que se desean calcular.

Los parámetros avanzados permanecerán ocultos inicialmente bajo un bloque
desplegable para no sobrecargar el flujo principal.

La unidad del umbral y de la diferencia mínima dependerá de la métrica:

- Puntos porcentuales para proporción de botellas no conformes.
- Eventos por `100` o `1.000` botellas para tasa de defectos medida.
- Eventos por `1.000` botellas nominales para la tasa proxy.
- Unidades absolutas solo para análisis de impacto, no para ranking de calidad.

La primera versión utilizará por defecto:

```text
exposure_mode = assumed_constant
assumed_line_rate_per_minute = 100
rate_display_scale = 1.000
```

Estos valores serán configuración del backend, no constantes repartidas por el
código. El frontend los mostrará como hipótesis activa y no permitirá
confundirlos con mediciones.

### 3.3 Salida del análisis

La salida incluirá:

- Resumen ejecutivo.
- Tabla descriptiva por producción y ramal.
- Ranking razonado.
- Comparaciones globales y por pares.
- Intervalos de confianza y tamaños de efecto.
- Estabilidad temporal y control estadístico.
- Análisis de contribución y Pareto.
- Diagnósticos por dimensiones disponibles.
- Gráficos estadísticos.
- Advertencias de calidad de datos y supuestos.
- Metadatos necesarios para reproducir el cálculo.

## 4. Grano y preparación de los datos

### 4.1 Tabla analítica canónica

El backend construirá una tabla interna por consulta con, como mínimo:

```text
production_id
line
branch
window_start
calendar_day
elapsed_minutes
production_progress
rejected_count
inspected_count
nominal_exposure
measured_exposure
effective_exposure
exposure_source
proxy_defect_event_rate
measured_defect_event_rate
nonconforming_bottle_proportion  # solo si puede calcularse de forma fiable
defect_code
cavity
section
machine_code
color
```

No todas las columnas estarán presentes en todos los niveles de agregación. Se
definirán dos vistas internas:

- Vista temporal: una fila por producción, ramal y ventana.
- Vista de contribución: filas desglosadas por la dimensión diagnóstica.

### 4.2 Modelo de exposición provisional

La exposición nominal se calculará por duración:

```text
nominal_line_exposure =
    assumed_line_rate_per_minute * window_duration_minutes
```

Con `100 botellas/minuto`:

```text
10m -> 1.000 botellas nominales por línea
1h  -> 6.000 botellas nominales por línea
4h  -> 24.000 botellas nominales por línea
```

Para análisis por ramal, la primera hipótesis será un reparto uniforme entre
los ramales activos de la línea:

```text
nominal_branch_exposure =
    nominal_line_exposure / active_branch_count
```

Si están activos todos los ramales conocidos:

```text
AV23: 2 ramales -> 500 botellas nominales por ramal y ventana de 10m
AV32: 4 ramales -> 250 botellas nominales por ramal y ventana de 10m
```

Esta regla no afirma que todos los ramales tengan realmente la misma velocidad.
Es una aproximación sustituible. El análisis devolverá siempre el número de
ramales usado en el reparto.

`active_branch_count` no será el número de ramales seleccionados en el
frontend. Se obtendrá del catálogo de la producción y, cuando existan señales
de actividad fiables, de los ramales activos en esa ventana. Desmarcar un ramal
para visualizarlo no podrá aumentar artificialmente la exposición asignada a
los restantes.

No se asignará automáticamente exposición nominal a una ventana sin datos. La
presencia de una captura se utilizará provisionalmente como evidencia de
actividad, pero no permite detectar paradas parciales entre capturas. Se
distinguirán:

- Exposición nominal de una ventana observada.
- Exposición desconocida de una ventana ausente.
- Exposición medida, cuando esté disponible.

La selección de exposición efectiva seguirá esta prioridad:

```text
effective_exposure =
    measured_exposure if available
    else nominal_exposure
```

Cada fila conservará `exposure_source` con valores como:

```text
measured
assumed_constant
unknown
```

### 4.3 Agregado de línea

Para comparar producciones completas, la métrica normalizada de línea no será
la media simple de las tasas de sus ramales.

Se calculará como tasa ponderada por exposición:

```text
line_defect_event_rate =
    SUM(branch_defect_events) / SUM(branch_effective_exposure)
```

Esto evita que un ramal con poco volumen tenga el mismo peso que otro con una
exposición mucho mayor. La misma regla se aplicará a una proporción de botellas
no conformes cuando exista el recuento de botellas únicas.

Cuando todas las exposiciones sean nominales y constantes, esta ponderación
equivale a comparar recuentos ajustados por la duración observada. No corrige
variaciones reales de velocidad.

### 4.4 Datos ausentes

Se distinguirán:

- Ventana sin captura.
- Captura con exposición medida igual a cero.
- Ventana válida con cero rechazos.
- Valor inválido por inconsistencia.

Reglas:

- Una ventana ausente no se convertirá en cero.
- Una tasa con denominador cero o desconocido será `null`.
- Los descriptivos indicarán observaciones válidas, ausentes y descartadas.
- Los gráficos mostrarán huecos reales.

### 4.5 Cobertura mínima

Antes de ejecutar inferencia se validará:

- Mínimo de ventanas válidas por producción.
- Mínimo de exposición acumulada.
- Número de días representados.
- Porcentaje de ventanas esperadas con datos.

Los valores exactos serán configurables. Propuesta inicial:

```text
min_valid_windows = 8
min_effective_exposure = 100
min_coverage_ratio = 0.60
```

Si no se cumplen, se devolverán descriptivos, pero los contrastes afectados se
marcarán como `insufficient_data`.

### 4.6 Alineación temporal

Se soportarán dos vistas:

1. **Tiempo calendario:** conserva fecha y hora reales.
2. **Tiempo relativo:** transforma cada producción a:

```text
elapsed_minutes = window_start - production_start
production_progress = elapsed_minutes / production_duration
```

El progreso normalizado estará entre `0` y `1`. Permitirá comparar arranque,
zona central y final de producciones de diferente fecha o duración.

No se interpolarán valores para los contrastes. La interpolación, si se usa
para una línea visual, estará identificada y no alimentará cálculos.

## 5. Estadística descriptiva

### 5.1 Medidas por producción

Para cada producción y, opcionalmente, para cada ramal:

- Rechazos totales.
- Exposición total efectiva.
- Exposición nominal y medida, presentadas por separado.
- Fuente de exposición y porcentaje cubierto por cada fuente.
- Tasa global ponderada.
- Número de ventanas válidas.
- Cobertura temporal.
- Media de las tasas por ventana.
- Mediana.
- Mínimo y máximo.
- Percentiles `P10`, `P25`, `P75`, `P90` y `P95`.
- Rango intercuartílico `IQR = P75 - P25`.
- Varianza y desviación estándar.
- Coeficiente de variación cuando sea interpretable.
- Desviación absoluta mediana, `MAD`.
- Asimetría.
- Curtosis.
- Porcentaje de ventanas por encima del umbral industrial.
- Racha máxima de ventanas por encima del umbral.
- Pendiente temporal robusta.

La tasa global ponderada y la distribución de tasas por ventana se mostrarán
como conceptos diferentes. Toda tasa basada únicamente en la hipótesis de
`100 botellas/minuto` llevará la etiqueta `proxy`.

### 5.2 Tendencia

La tendencia se estimará sobre la tasa por ventana:

- Pendiente de Theil-Sen como estimador robusto principal.
- Regresión lineal ordinaria como dato auxiliar.
- Intervalo de confianza de la pendiente.
- Signo de tendencia: mejora, estable o empeora.

La clasificación requerirá tanto magnitud mínima como incertidumbre compatible
con el cambio. No dependerá únicamente del signo numérico.

### 5.3 Estabilidad entre ramales

Para cada producción:

- Diferencia máxima y mínima entre tasas globales de ramal.
- Desviación estándar ponderada entre ramales.
- Ramal con mayor tasa.
- Contribución de cada ramal al exceso de rechazos.
- Correlación temporal entre ramales cuando haya ventanas comparables.
- Porcentaje de ventanas en las que cada ramal es el peor.

La estabilidad de ramales será un componente diagnóstico, no una prueba de
causalidad.

## 6. Intervalos de confianza

### 6.1 Tasa global

Para una tasa basada en recuentos se calculará un intervalo binomial cuando la
interpretación de los recuentos lo permita:

- Wilson como intervalo por defecto.
- Clopper-Pearson opcional para muestras pequeñas o necesidad conservadora.

Advertencia: si el numerador cuenta múltiples defectos por botella, la
interpretación binomial exacta no es válida. En ese caso el intervalo principal
será bootstrap por bloques sobre ventanas y se documentará como intervalo de
una tasa de eventos.

### 6.2 Descriptivos y diferencias

Para medias, medianas, percentiles, pendientes y diferencias:

- Bootstrap por bloques temporales.
- Bloque inicial propuesto: un día calendario.
- Si una producción dura menos de dos días, bloque de `4h`.
- Método percentil como primera entrega.
- BCa como ampliación si la validación demuestra sesgo relevante.

La semilla, tamaño de bloque y número de remuestreos se devolverán en
metadatos.

## 7. Comparaciones inferenciales

### 7.1 Comparación global de tasas

Cuando el numerador pueda interpretarse como éxitos binomiales:

- Prueba chi-cuadrado de homogeneidad para varias producciones.
- Prueba exacta de Fisher para tablas pequeñas.
- Prueba de dos proporciones para comparaciones por pares.
- Diferencia absoluta de proporciones.
- Riesgo relativo.
- Odds ratio, solo como medida secundaria.

Cuando se analicen tasas de eventos con exposición:

- Modelo de Poisson con `log(effective_exposure)` como offset.
- Comprobación de sobredispersión.
- Regresión binomial negativa si la varianza excede claramente la media.
- Razón de tasas e intervalo de confianza.

La elección del modelo se registrará en `method` y nunca será implícita.
Cuando la exposición sea nominal, estos modelos describirán asociaciones bajo
esa hipótesis y no demostrarán diferencias en tasas reales de fabricación.

### 7.2 Comparación de distribuciones temporales

Sobre tasas por ventana:

- Kruskal-Wallis para más de dos producciones.
- Mann-Whitney U para comparaciones por pares.
- Welch ANOVA como análisis complementario si la distribución y tamaño lo
  permiten.
- Prueba de Levene centrada en la mediana o Brown-Forsythe para dispersión.
- Kolmogorov-Smirnov como diagnóstico de diferencia distributiva completa,
  con advertencia sobre autocorrelación.

Las pruebas sobre ventanas se acompañarán de bootstrap por bloques para evitar
una falsa precisión derivada de tratar todas las ventanas como independientes.

### 7.3 Tamaños de efecto

Se calcularán, según el contraste:

- Diferencia de proporciones en puntos porcentuales, si existen botellas únicas.
- Diferencia de tasas proxy en eventos por `1.000` botellas nominales.
- Riesgo relativo.
- Razón de tasas.
- Diferencia estandarizada de medias, Hedges `g`.
- Cliff's delta para comparaciones no paramétricas.
- Eta cuadrado o epsilon cuadrado para comparaciones globales.
- Razón de varianzas o diferencia de `MAD` para estabilidad.

Cada tamaño de efecto incluirá:

- Estimación.
- Intervalo de confianza.
- Dirección.
- Etiqueta de magnitud configurada, sin sustituir el valor numérico.

### 7.4 Comparaciones múltiples

Con `k` producciones existen `k * (k - 1) / 2` comparaciones por pares.

Se devolverán:

- Valor p bruto.
- Valor p ajustado.
- Método de ajuste.

Benjamini-Hochberg será el método por defecto para controlar la tasa de falsos
descubrimientos. Holm estará disponible cuando se prefiera control estricto
del error familiar.

### 7.5 Producción de referencia

El usuario podrá elegir una producción como referencia. En ese modo:

- Se comparará cada producción únicamente contra la referencia.
- Se reducirá el número de contrastes.
- El forest plot mostrará diferencia o razón respecto a ella.

Si no se elige referencia, se ejecutará comparación global y comparaciones por
pares.

## 8. Control estadístico y comportamiento temporal

### 8.1 Gráficos de control

Según la naturaleza validada de la métrica:

- `p-chart` para proporción de unidades no conformes.
- `u-chart` para defectos por unidad con exposición variable.
- Laney `p'` o `u'` si existe sobredispersión significativa.
- Gráfico de individuales y rango móvil para una métrica continua agregada.

Los límites se calcularán con una línea base explícita. Inicialmente:

- Línea base interna de cada producción para evaluar estabilidad.
- Línea base de una producción de referencia para comparar campañas.

Se detectarán reglas básicas:

- Punto fuera de límites.
- Racha prolongada a un lado de la línea central.
- Tendencia monótona.
- Concentración anómala cerca de los límites.

Las reglas exactas deberán estar versionadas y visibles en la respuesta.

### 8.2 Detección de cambios

Como análisis complementario:

- CUSUM para cambios pequeños y persistentes.
- EWMA para cambios graduales.
- Detección de puntos de cambio mediante PELT como fase posterior.

No se utilizarán para declarar causa. Servirán para localizar cuándo comenzó
un deterioro.

### 8.3 Autocorrelación

Se calculará:

- ACF para retardos limitados.
- Estadístico de Durbin-Watson sobre residuos de tendencia.
- Ljung-Box como diagnóstico opcional.

Si se detecta dependencia relevante, el resultado advertirá que los contrastes
por ventana pueden subestimar la incertidumbre.

## 9. Diagnóstico de las diferencias

### 9.1 Dimensiones

El backend podrá descomponer los resultados por:

- Ramal.
- Código de defecto.
- Cavidad o molde.
- Sección.
- Código de máquina ZF.
- Color.
- Día.
- Franja horaria.
- Tramo relativo de la producción.

Solo se calcularán dimensiones presentes y con cobertura suficiente.

### 9.2 Pareto

Para cada producción:

- Rechazos por categoría.
- Porcentaje sobre rechazos de la producción.
- Porcentaje acumulado.
- Tasa normalizada por exposición cuando el denominador pueda asignarse a la
  categoría.
- Diferencia respecto a la referencia.

El Pareto no usará solo recuentos cuando las exposiciones de las categorías
sean diferentes.

### 9.3 Descomposición del exceso

Para explicar por qué una producción es peor que la referencia:

```text
excess_events =
    observed_rejected_count
    - effective_exposure * reference_defect_event_rate
```

Después se distribuirá el exceso por dimensión cuando la asignación sea
matemáticamente válida.

Se mostrarán:

- Categorías responsables del mayor exceso absoluto.
- Categorías con mayor aumento relativo.
- Cobertura de la explicación: porcentaje del exceso atribuido.
- Categorías con poca exposición, marcadas como inestables.

### 9.4 Interacciones

Para diagnósticos finos se preverán cruces:

- Ramal por defecto.
- Ramal por cavidad.
- Sección por defecto.
- Día por ramal.
- Franja horaria por ramal.

La primera versión limitará el número de categorías y aplicará un umbral de
exposición para evitar matrices dispersas e interpretaciones espurias.

## 10. Ranking de producciones

### 10.1 Ranking principal

El ranking principal se ordenará por tasa global ponderada, de peor a mejor.
Cada fila mostrará:

- Tasa global e intervalo.
- Diferencia frente a la mejor o a la referencia.
- Riesgo o razón de tasas.
- `P95` de tasa por ventana.
- Variabilidad robusta.
- Porcentaje fuera de umbral.
- Tendencia.
- Calidad y cobertura de datos.

### 10.2 Índice compuesto opcional

No se ocultarán las métricas bajo una puntuación única. No obstante, podrá
existir un índice configurable para ordenar:

```text
score =
    w1 * normalized_global_rate
  + w2 * normalized_p95
  + w3 * normalized_variability
  + w4 * normalized_out_of_limit_ratio
  + w5 * normalized_positive_trend
```

Condiciones:

- Pesos visibles y versionados.
- Componentes normalizados con reglas estables.
- Desglose completo de contribuciones.
- Posibilidad de desactivar el índice.
- Análisis de sensibilidad de pesos antes de usarlo como criterio operativo.

La primera entrega no utilizará este índice para afirmar automáticamente qué
producción es peor; será una ayuda de ordenación.

## 11. Gráficos estadísticos

### 11.1 Gráficos interactivos del frontend

Plotly seguirá utilizándose para gráficos interactivos:

- Ranking con intervalos de confianza.
- Curvas superpuestas en tiempo relativo.
- Pareto.
- Heatmaps por día, ramal y hora.
- Gráficos de control.
- Forest plot de tamaños de efecto.
- Matriz de comparaciones por pares.
- Contribución al exceso.

El backend devolverá datos preparados, no instrucciones de presentación
acopladas a React.

### 11.2 Boxplots diarios con Seaborn

El backend generará boxplots diarios con Seaborn:

- Eje X: día relativo o fecha calendario.
- Eje Y: tasa por ventana.
- `hue`: producción o ramal.
- Caja: `Q1`, mediana y `Q3`.
- Bigotes: convención `1.5 * IQR`.
- Puntos atípicos visibles y contabilizados.
- Anotación del número de observaciones y exposición diaria.

No se dibujarán cajas con menos observaciones de las necesarias; se mostrará
una marca de muestra insuficiente.

### 11.3 Violin plots diarios con Seaborn

Los violin plots mostrarán la distribución diaria:

- Densidad con ancho normalizado de forma consistente.
- Mediana y cuartiles superpuestos.
- Puntos observados opcionales mediante strip o swarm limitado.
- `cut=0` para no extender la densidad fuera de los datos.
- Ancho de banda explícito y estable.

En muestras pequeñas se preferirá boxplot más puntos. El backend indicará
cuándo una estimación de densidad no es fiable.

### 11.4 Renderizado de imágenes

Seaborn y Matplotlib residirán en infraestructura, detrás de un `Protocol` de
renderizado. Los servicios estadísticos no dependerán de detalles gráficos.

Propuesta:

- Formato PNG para la primera versión.
- Resolución `144 dpi`.
- Tema coherente con el frontend.
- Tamaño y paleta deterministas.
- Cierre explícito de figuras para no acumular memoria.
- Caché por identificador de análisis y especificación del gráfico.

Las imágenes no se incrustarán como Base64 en la respuesta estadística. Se
servirán mediante un endpoint local de recursos.

### 11.5 Accesibilidad y exportación

Cada gráfico tendrá:

- Título descriptivo.
- Texto alternativo generado a partir del resultado.
- Leyenda y unidades.
- Tabla equivalente descargable en CSV en una fase posterior.
- Botón de descarga PNG para gráficos renderizados.

## 12. Diseño de la API

### 12.1 Catálogo estadístico

```http
GET /api/statistics/catalog
```

Devolverá:

- Análisis disponibles.
- Gráficos disponibles.
- Modelo de exposición activo.
- Ritmo nominal por minuto y reglas de reparto, cuando sean aplicables.
- Niveles de confianza soportados.
- Métodos de ajuste de comparaciones múltiples.
- Límites de producciones y categorías.
- Parámetros por defecto.
- Versión de las definiciones estadísticas.

### 12.2 Solicitud de cálculo

```http
POST /api/statistics/query
```

Petición propuesta:

```json
{
  "selection": {
    "production_ids": ["AV23_PROD_001", "AV23_PROD_002"],
    "branches": ["AV23-1", "AV23-2"],
    "metric": "rejection_rate",
    "window": "1h",
    "filters": {
      "defect_codes": [],
      "cavities": [],
      "colors": [],
      "sections": [],
      "machine_codes": []
    }
  },
  "options": {
    "comparison_level": "production",
    "exposure_mode": "assumed_constant",
    "reference_production_id": "AV23_PROD_001",
    "confidence_level": 0.95,
    "multiple_testing_method": "benjamini_hochberg",
    "minimum_relevant_difference": {
      "value": 0.5,
      "unit": "events_per_1000_inspected"
    },
    "industrial_threshold": null,
    "alignment": "relative",
    "bootstrap_samples": 10000,
    "bootstrap_seed": 4835242,
    "requested_analyses": [
      "descriptive",
      "pairwise",
      "variability",
      "trend",
      "control",
      "contributors"
    ],
    "requested_plots": [
      "ranking",
      "daily_boxplot",
      "daily_violin",
      "relative_series",
      "pareto",
      "heatmap",
      "control_chart",
      "forest_plot"
    ]
  }
}
```

### 12.3 Respuesta

La respuesta estará estructurada, no será un diccionario libre:

```json
{
  "analysis_id": "01J...",
  "status": "completed",
  "definition_version": "1.0.0",
  "selection": {},
  "exposure": {
    "source": "assumed_constant",
    "assumed_line_rate_per_minute": 100,
    "window_minutes": 60,
    "nominal_line_exposure": 6000,
    "branch_allocation": "uniform_among_active_branches",
    "is_measured": false
  },
  "summary": {
    "worst_production_id": "AV23_PROD_002",
    "ranking_basis": "weighted_normalized_quality_rate",
    "headline": "..."
  },
  "descriptive": [],
  "global_tests": [],
  "pairwise_comparisons": [],
  "trends": [],
  "stability": [],
  "control_signals": [],
  "contributors": [],
  "plot_assets": [
    {
      "id": "daily_boxplot",
      "kind": "image/png",
      "url": "/api/statistics/analyses/01J.../plots/daily_boxplot"
    }
  ],
  "warnings": [],
  "meta": {
    "query_time_ms": 0,
    "rows_scanned": 0,
    "cache_hit": false,
    "bootstrap_seed": 4835242,
    "exposure_definition_version": "1.0.0"
  }
}
```

Los objetos reales tendrán modelos Pydantic específicos para cada bloque.

### 12.4 Recursos gráficos

```http
GET /api/statistics/analyses/{analysis_id}/plots/{plot_id}
```

Reglas:

- Solo identificadores generados por el backend.
- Sin rutas de archivo proporcionadas por el cliente.
- `Content-Type` correcto.
- Caché HTTP local con `ETag`.
- Caducidad limitada de análisis en memoria.
- Error `404` uniforme si el análisis ha expirado.

### 12.5 Ejecución síncrona y evolución

La primera versión será síncrona y ejecutará Polars, SciPy, Statsmodels y
Seaborn mediante `asyncio.to_thread()`.

Si las mediciones superan el objetivo de respuesta:

- `POST` devolverá `202 Accepted`.
- Se añadirá `GET /api/statistics/analyses/{analysis_id}`.
- El frontend consultará el estado hasta completarse.

No se incorporará una cola de trabajos antes de medir la necesidad real.

## 13. Arquitectura del backend

### 13.1 Dependencias previstas

- Polars: lectura, filtrado, agrupación y tablas analíticas.
- NumPy: operaciones numéricas.
- SciPy: contrastes y estadísticos básicos.
- Statsmodels: proporciones, modelos de recuento, correcciones y control.
- Seaborn: boxplots y violin plots solicitados.
- Matplotlib: renderizado no interactivo con backend `Agg`.

Las versiones quedarán fijadas en `pyproject.toml` y `uv.lock`.

### 13.2 Capas

```text
API
  -> caso de uso CalculateProductionStatistics
    -> StatisticalDatasetBuilder
    -> DescriptiveStatisticsService
    -> InferentialComparisonService
    -> StabilityAndControlService
    -> ContributionAnalysisService
    -> StatisticalPlotService
      -> repositorio Parquet
      -> librerías estadísticas
      -> renderizador Seaborn
```

Responsabilidades:

- API: validación HTTP, códigos de estado y serialización.
- Aplicación: coordinación, caché y política de ejecución.
- Dominio: definiciones, resultados y reglas independientes de librerías.
- Infraestructura: Polars, SciPy, Statsmodels, Seaborn y almacenamiento temporal.

### 13.3 Contratos internos

Se definirán `Protocol` pequeños:

```text
StatisticalDataRepository
StatisticalTestEngine
BootstrapEngine
StatisticalPlotRenderer
AnalysisResultStore
```

Se usarán:

- `dataclass(frozen=True, slots=True)` para resultados internos.
- Pydantic v2 en los límites HTTP.
- `TypedDict` solo para estructuras estables requeridas por librerías.
- Enums o `Literal` para identificadores cerrados.
- `from __future__ import annotations`.
- Tipado compatible con `mypy --strict`.

### 13.4 Caché

Clave normalizada:

```text
selection
+ statistical_options
+ exposure_configuration
+ definition_version
+ catalog_version
+ parquet_versions
```

Se separarán:

- Caché de tabla analítica.
- Caché de resultados numéricos.
- Caché de imágenes.

Esto permitirá cambiar una visualización sin repetir toda la lectura de datos.

### 13.5 Logging y trazabilidad

Cada cálculo registrará mediante `structlog`:

- `request_id`.
- `analysis_id`.
- Producciones y número de ramales.
- Método estadístico.
- Duración por fase.
- Filas leídas y observaciones válidas.
- Advertencias.
- Uso de caché.

No se registrarán filas originales ni imágenes.

## 14. Diseño del frontend

### 14.1 Estados de la pantalla

Se definirán estados explícitos:

- `idle`: todavía no hay consulta temporal.
- `trend_ready`: hay series actuales y puede calcularse estadística.
- `statistics_loading`: cálculo en curso.
- `statistics_ready`: panel derecho con resultados.
- `statistics_error`: error independiente de la consulta temporal.
- `stale`: los selectores ya no coinciden con el análisis mostrado.

### 14.2 Botón de cálculo

El botón **Calcular estadísticas**:

- Aparecerá tras una consulta temporal válida.
- Estará deshabilitado con menos de dos producciones para comparaciones, aunque
  podrá existir un modo descriptivo de una sola producción.
- Enviará una copia exacta de la consulta, no leerá valores dispersos del DOM.
- Mostrará progreso o estado de carga.
- No sustituirá las gráficas actuales.

### 14.3 Pantalla compartida

Al iniciar el análisis:

```text
+--------------------------------+--------------------------------+
| Consulta y series temporales   | Análisis estadístico           |
|                                |                                |
| Selectores                     | Configuración                  |
| Resumen consulta               | Resumen y ranking              |
| Gráfico producción 1           | Boxplot / violin               |
| Gráfico producción 2           | Pareto / heatmap / control     |
+--------------------------------+--------------------------------+
```

Para escritorio:

- Contenedor `grid`.
- Proporción inicial `50% / 50%`.
- Separador redimensionable como mejora posterior.
- Scroll vertical independiente por panel.
- Cabeceras internas fijas.
- Ancho mínimo para evitar gráficos ilegibles.

Para pantallas estrechas:

- Pestañas `Series` y `Estadística`.
- No se forzarán dos columnas inutilizables.

### 14.4 Conservación del panel izquierdo

El panel izquierdo mantendrá:

- Todos los selectores actuales.
- Botón **Consultar**.
- Resumen de consulta.
- Un gráfico por producción.
- Estados de carga, error y vacío.

Al dividir la pantalla:

- Los controles podrán reorganizarse en más filas.
- Los gráficos Plotly recalcularán su ancho con `ResizeObserver`.
- No se perderá zoom, leyenda o selección sin motivo.

### 14.5 Organización del panel derecho

Orden propuesto:

1. Cabecera con análisis, estado y botón para cerrar.
2. Configuración estadística.
3. Resumen ejecutivo y advertencias.
4. Ranking.
5. Tabla descriptiva.
6. Comparaciones y forest plot.
7. Distribuciones diarias: boxplot y violin.
8. Evolución relativa.
9. Estabilidad y control.
10. Pareto, heatmaps y explicación del exceso.
11. Metadatos y supuestos.

Los bloques serán plegables para evitar una página inmanejable.

### 14.6 Selección de gráficos

El usuario podrá activar o desactivar:

- Ranking.
- Boxplot diario.
- Violin plot diario.
- Curva temporal relativa.
- Pareto.
- Heatmap.
- Gráfico de control.
- Forest plot.
- Matriz de comparaciones.

La selección se enviará al backend para no generar imágenes innecesarias.

### 14.7 Presentación de resultados

Reglas:

- No usar únicamente colores rojo y verde.
- Mostrar unidades en todos los valores.
- Presentar valor exacto e intervalo.
- Mostrar un aviso persistente cuando `exposure_source = assumed_constant`:
  `Resultados normalizados suponiendo 100 botellas/minuto por línea`.
- Etiquetar tasas nominales como `proxy` en tablas, tooltips y descargas.
- Permitir consultar la fórmula y el reparto por ramal desde el propio panel.
- Etiquetar `no concluyente` cuando corresponda.
- Diferenciar `sin diferencia detectada` de `equivalencia demostrada`.
- Mostrar advertencias cerca del resultado afectado.
- Mantener colores de producciones y ramales entre todos los gráficos.

## 15. Validaciones y errores

La API rechazará:

- Menos de una producción para descriptivos.
- Menos de dos producciones para comparaciones.
- Producción de referencia fuera de la selección.
- Métrica no apta para el análisis solicitado.
- Nivel de confianza inválido.
- Bootstrap fuera de límites seguros.
- Umbral negativo.
- Análisis o gráfico desconocido.
- Combinación de dimensión y filtros sin denominador válido.
- Ritmo nominal igual o inferior a cero.
- Modo de exposición solicitado que no esté disponible.
- Mezcla silenciosa de exposición medida y nominal sin identificarla.

Errores de dominio propuestos:

```text
INSUFFICIENT_STATISTICAL_DATA
INVALID_REFERENCE_PRODUCTION
UNSUPPORTED_STATISTICAL_METRIC
INVALID_ANALYSIS_OPTION
STATISTICAL_ASSUMPTION_FAILED
ANALYSIS_EXPIRED
PLOT_NOT_AVAILABLE
```

Se mantendrá el formato global de error y el `request_id` actuales.

## 16. Rendimiento y límites

Objetivos iniciales para seis producciones:

- Preparación de tabla: menos de `2 s`.
- Descriptivos y comparaciones: menos de `2 s`.
- Bootstrap de `10.000` muestras: menos de `5 s`.
- Renderizado de gráficos solicitados: menos de `3 s`.
- Tiempo total objetivo: menos de `8 s` con caché fría.

Límites:

- Máximo seis producciones, coherente con tendencias.
- Máximo de categorías diagnósticas por gráfico.
- Bootstrap acotado, por ejemplo entre `1.000` y `50.000`.
- Imágenes generadas bajo demanda.
- Sin envío de observaciones crudas masivas al navegador.

El rendimiento se medirá por fase antes de introducir precálculo.

## 17. Estrategia de pruebas

### 17.1 Pruebas matemáticas

Fixtures pequeñas con resultados calculados manualmente para:

- Exposición nominal de `10m`, `1h` y `4h`.
- Reparto uniforme de exposición entre ramales.
- Prioridad de exposición medida sobre nominal.
- Tasa ponderada de línea.
- Descriptivos y percentiles.
- Wilson.
- Diferencia de proporciones.
- Riesgo relativo y razón de tasas.
- Mann-Whitney y Kruskal-Wallis.
- Brown-Forsythe.
- Hedges `g` y Cliff's delta.
- Ajustes Benjamini-Hochberg y Holm.
- Pendiente Theil-Sen.
- Límites de gráficos de control.
- Descomposición del exceso.

Se usarán tolerancias numéricas explícitas.

### 17.2 Pruebas de propiedades

Casos invariantes:

- Reordenar filas no cambia resultados.
- Cambiar los ramales seleccionados no cambia la exposición física asignada.
- Multiplicar todos los recuentos por una constante conserva el orden relativo
  esperado bajo exposición nominal común.
- Con exposición nominal constante, recuento y tasa proxy conservan la misma
  correlación y ranking.
- Duplicar categorías sin cambiar totales no altera la tasa global.
- Una producción comparada consigo misma tiene diferencia cero.
- Aumentar rechazos con exposición fija no mejora el ranking.
- Una semilla bootstrap fija produce el mismo resultado.
- Las tasas agregadas permanecen dentro de límites matemáticos válidos.

### 17.3 Pruebas de datos problemáticos

- Denominador cero.
- Ventana sin evidencia de actividad, a la que no debe asignarse exposición.
- Mezcla de ventanas con exposición nominal y medida.
- Ventanas ausentes.
- Una sola observación.
- Exposición muy desigual.
- Todas las tasas iguales.
- Outliers extremos.
- Producciones de distinta duración.
- Categorías muy dispersas.
- Sobredispersión.
- Autocorrelación.
- Datos insuficientes para KDE del violin plot.

### 17.4 Pruebas de API

- Contrato completo.
- Validación de referencia.
- Errores uniformes.
- Caché.
- Caducidad de `analysis_id`.
- Endpoint de imágenes.
- `request_id`.
- Selección exacta de filtros.
- Compatibilidad con `/api/trends/query`.

### 17.5 Pruebas gráficas

- Backend Matplotlib `Agg`, sin dependencia de interfaz gráfica.
- Snapshot o hash perceptual con tolerancia controlada.
- Dimensiones, DPI y tipo MIME.
- Ausencia de figuras abiertas tras renderizar.
- Etiquetas, unidades y leyenda.
- Boxplot y violin con muestras insuficientes.

### 17.6 Pruebas de frontend

- El botón aparece solo con consulta válida.
- Apertura y cierre de pantalla compartida.
- Persistencia del panel izquierdo.
- Estado `stale` al cambiar filtros.
- Carga y error independientes.
- Renderizado de tablas y advertencias.
- Selección de gráficos.
- Reajuste de Plotly al cambiar ancho.
- Vista por pestañas en ancho reducido.

### 17.7 Validación estadística

Además de tests unitarios:

- Comparar resultados con cálculos independientes en un notebook de validación.
- Contrastar SciPy y Statsmodels en casos conocidos.
- Revisar manualmente varias producciones reales.
- Documentar ejemplos de interpretación correcta e incorrecta.

## 18. Documentación y auditabilidad

Se añadirá documentación de:

- Diccionario de métricas.
- Unidad estadística.
- Hipótesis de `100 botellas/minuto` y su fecha o versión de aprobación.
- Regla de exposición nominal para cada ventana y ramal.
- Diferencia entre exposición nominal, medida y efectiva.
- Regla de deduplicación de inspeccionadas.
- Fórmulas.
- Supuestos de cada prueba.
- Criterios de selección automática del método.
- Ajuste por comparaciones múltiples.
- Reglas de control.
- Limitaciones del dato.
- Versionado de definiciones.

Cada respuesta incluirá `definition_version`. Un cambio de fórmula incrementará
esa versión e invalidará la caché correspondiente.

## 19. Plan de implementación incremental

La implementación se dividirá en cambios pequeños y revisables. No se realizará
como una única refactorización o un PR de gran tamaño.

### Paso 1. Contratos y tabla analítica

- Añadir modelos de dominio y Pydantic.
- Crear `StatisticalDataRepository`.
- Construir la vista temporal canónica.
- Implementar el proveedor de exposición nominal de `100 botellas/minuto`.
- Modelar `nominal`, `measured`, `effective` y `exposure_source`.
- Aplicar el reparto provisional por ramales del catálogo.
- Implementar validación de cobertura y datos ausentes.
- Añadir pruebas de preparación.

**Criterio de aceptación:** una selección existente produce una tabla auditable
por producción, ramal y ventana sin alterar `/api/trends/query`; cada tasa
indica si usa exposición nominal o medida.

### Paso 2. Descriptivos

- Implementar exposición, tasa ponderada, percentiles, dispersión y cobertura.
- Añadir intervalos básicos.
- Crear `POST /api/statistics/query` solo para `descriptive`.
- Probar con fixtures y datos reales.

**Criterio de aceptación:** el endpoint devuelve una tabla descriptiva exacta y
reproducible.

### Paso 3. Armazón del panel derecho

- Añadir botón **Calcular estadísticas**.
- Implementar estados y detección `stale`.
- Crear la cuadrícula `50/50`.
- Mantener funcionalidad del panel izquierdo.
- Mostrar resumen y tabla descriptiva.

**Criterio de aceptación:** la pantalla se divide sin romper consulta, gráficos
ni filtros actuales.

### Paso 4. Comparaciones y tamaños de efecto

- Añadir pruebas globales y por pares.
- Implementar tamaños de efecto.
- Ajustar comparaciones múltiples.
- Incorporar referencia opcional.
- Añadir ranking con intervalos.

**Criterio de aceptación:** se puede cuantificar y justificar la diferencia
entre dos o más producciones.

### Paso 5. Bootstrap temporal y tendencia

- Implementar bootstrap por bloques.
- Añadir Theil-Sen e intervalos.
- Detectar autocorrelación.
- Integrar advertencias sobre supuestos.

**Criterio de aceptación:** la incertidumbre no depende de asumir
independencia artificial entre todas las ventanas.

### Paso 6. Boxplots y violin plots con Seaborn

- Añadir renderer y almacén temporal de imágenes.
- Implementar endpoint de recursos.
- Crear boxplots diarios.
- Crear violin plots diarios con protección para muestras pequeñas.
- Integrar imágenes y descarga en el panel derecho.

**Criterio de aceptación:** los gráficos diarios son reproducibles, legibles y
no filtran rutas locales.

### Paso 7. Tiempo relativo y visualizaciones comparativas

- Calcular tiempo transcurrido y progreso.
- Añadir curvas relativas interactivas.
- Incorporar forest plot y matriz de pares.
- Mantener colores estables.

**Criterio de aceptación:** producciones de fechas distintas pueden compararse
por fase de fabricación.

### Paso 8. Diagnóstico y Pareto

- Añadir desgloses por dimensión.
- Implementar Pareto normalizado.
- Calcular contribución al exceso.
- Añadir heatmaps y cruces prioritarios.

**Criterio de aceptación:** el resultado identifica las categorías que explican
la mayor parte del deterioro.

### Paso 9. Control estadístico

- Implementar selección entre `p`, `u`, Laney e individuales.
- Añadir señales, CUSUM y EWMA de forma gradual.
- Incorporar línea base de referencia.

**Criterio de aceptación:** el sistema distingue nivel alto de inestabilidad y
localiza periodos anómalos.

### Paso 10. Índice compuesto opcional

- Definir pesos configurables.
- Mostrar componentes.
- Ejecutar análisis de sensibilidad.
- No activarlo por defecto hasta validación industrial.

**Criterio de aceptación:** el índice nunca oculta la evidencia ni sustituye
las métricas originales.

### Paso 11. Rendimiento y endurecimiento

- Medir cada fase.
- Separar cachés.
- Añadir expiración de análisis.
- Decidir si se necesita ejecución `202 Accepted`.
- Completar logging, documentación y pruebas end-to-end.

**Criterio de aceptación:** cálculo estable dentro de los objetivos medidos y
sin crecimiento de memoria tras ejecuciones repetidas.

## 20. Decisiones que requieren validación antes de interpretar resultados

1. Si `LISTA_DEFECTOS_RECH` cuenta defectos, botellas rechazadas o ambos según
   el contexto.
2. Regla industrial definitiva de deduplicación de inspeccionadas.
3. Validez provisional del ritmo de `100 botellas/minuto` por línea.
4. Si el reparto uniforme entre ramales representa razonablemente el proceso.
5. Cómo detectar ramales realmente activos y paradas parciales.
6. Umbral o tasa objetivo aceptable.
7. Diferencia mínima relevante y su unidad.
8. Unidad primaria para gráficos de control: no conformes o defectos por unidad.
9. Producción o periodo que se considerará línea base.
10. Si el día se define por calendario o por turno industrial.
11. Tamaño de bloque bootstrap más representativo.
12. Dimensiones diagnósticas con denominador de exposición fiable.
13. Pesos, si se aprueba el índice compuesto.

Estas decisiones no bloquean la creación de descriptivos, pero sí condicionan
la interpretación inferencial y las afirmaciones operativas.

## 21. Fuera de alcance inicial

- Predicción de defectos futuros.
- Atribución causal automática.
- Modelos de machine learning.
- Alarmas en tiempo real.
- Modificación de Parquet originales.
- Persistencia histórica de informes.
- Generación automática de decisiones de proceso.
- Uso en red, autenticación o multiusuario.

## 22. Resultado esperado

Tras seleccionar y consultar varias producciones, el usuario podrá pulsar
**Calcular estadísticas**. La aplicación conservará a la izquierda la consulta
y las series actuales y abrirá a la derecha un análisis detallado.

El análisis permitirá ordenar producciones, cuantificar diferencias, estimar
incertidumbre, comprobar estabilidad y localizar las dimensiones que explican
el deterioro. Todas las conclusiones estarán vinculadas a fórmulas, supuestos,
intervalos, tamaños de efecto, filtros y una versión reproducible de la
definición estadística.

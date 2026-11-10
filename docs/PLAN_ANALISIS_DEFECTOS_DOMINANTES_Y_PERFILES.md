# Plan de análisis de defectos dominantes y perfiles de producción

## 1. Objetivo

Ampliar el análisis estadístico actual para estudiar no solo cuántos eventos de
defecto registra cada producción, sino también:

- Qué defectos explican la mayor parte del rechazo.
- Cómo evoluciona temporalmente cada defecto dominante.
- Cómo se comporta la producción después de excluir hasta tres defectos
  seleccionados.
- Si la inestabilidad global procede de unos pocos defectos o está distribuida.
- Qué producciones presentan perfiles de defectos similares.
- Qué combinaciones de defectos explican las diferencias entre producciones.
- Si existen estados, grupos o patrones recurrentes mediante PCA, clustering y
  otros métodos exploratorios.

La funcionalidad será descriptiva y exploratoria. No se presentarán
correlaciones, componentes o agrupaciones como causas físicas demostradas.

## 2. Principios

1. Los defectos dominantes no estarán codificados como `15` y `37`.
2. El usuario podrá seleccionar entre cero y tres códigos.
3. El Pareto ayudará a decidir qué códigos analizar, pero no realizará la
   selección automáticamente sin mostrarla.
4. Se conservarán simultáneamente los resultados totales, dominantes y
   residuales.
5. Los cálculos usarán las mismas reglas de filtrado, ventanas y limpieza de
   valores erróneos que el análisis estadístico existente.
6. Los conteos, contribuciones y tasas proxy se etiquetarán con precisión.
7. Los métodos multivariantes respetarán la naturaleza composicional de los
   porcentajes.
8. La implementación se dividirá en cambios pequeños y revisables.

## 3. Diferencia entre Pareto y PCA

El Pareto responde:

> ¿Qué códigos acumulan más eventos de defecto?

Ordena categorías por recuento o contribución. No estudia correlaciones ni crea
variables nuevas.

El PCA responde:

> ¿Qué combinaciones de defectos explican las principales diferencias entre
> observaciones?

Cada componente principal es una combinación lineal de variables. Por ejemplo,
una componente podría asignar cargas positivas a `15` y `37` y negativas a
otros códigos. No debe identificarse una componente con un único defecto salvo
que sus cargas lo justifiquen.

## 4. Unidades de análisis

Se soportarán dos niveles.

### 4.1 Perfil por producción

Una observación representa una producción completa.

```text
AV23_PROD_001 -> [defecto_0, defecto_1, ..., defecto_49]
```

Uso:

- Comparar campañas.
- PCA global.
- Clustering de producciones.
- Distancias entre distribuciones.

Limitación actual: existen 17 producciones. Es una muestra pequeña para modelos
complejos y para validar grupos estables.

### 4.2 Perfil por ventana temporal

Una observación representa:

```text
producción + ramal + ventana
```

Uso:

- Detectar estados temporales.
- Analizar aparición conjunta de defectos.
- Estudiar retardos.
- Entrenar posteriormente modelos de empeoramiento.

El número de observaciones será mayor, pero las ventanas consecutivas no son
independientes. Las validaciones futuras deberán dividirse por producción o por
bloques temporales, nunca aleatoriamente por filas.

## 5. Selección de defectos dominantes

### 5.1 Interfaz

En el panel derecho se añadirá un bloque:

```text
Defectos dominantes
[ 15 × ] [ 37 × ] [ + Añadir ]
Máximo: 3
```

Reglas:

- Selector múltiple con máximo tres valores.
- Opciones obtenidas del catálogo, sin códigos escritos en React.
- Mostrar código, recuento y contribución para la selección actual.
- Ordenar inicialmente según el Pareto de las producciones seleccionadas.
- Permitir seleccionar códigos que no estén entre los tres primeros.
- Mantener una selección común para todas las producciones comparadas.
- Mostrar advertencia si un código no aparece en alguna producción.

### 5.2 Preselección asistida

Se ofrecerán acciones explícitas:

- `Seleccionar top 1`.
- `Seleccionar top 2`.
- `Seleccionar top 3`.
- `Limpiar selección`.

La preselección podrá calcularse mediante:

- Pareto agregado de la selección, como ayuda general.
- Intersección de códigos dominantes entre producciones.
- Unión de los primeros códigos de cada producción.

La primera versión utilizará el Pareto agregado únicamente para sugerir
valores. Los análisis conservarán los Pareto independientes por producción.

### 5.3 Contrato

Las opciones estadísticas incorporarán:

```json
{
  "dominant_defect_codes": [15, 37],
  "dominant_selection_mode": "manual"
}
```

Validaciones:

- Lista sin duplicados.
- Entre cero y tres códigos.
- Códigos existentes en el catálogo.
- Orden estable para caché y reproducibilidad.

## 6. Descomposición de series

Para cada producción, ramal y ventana \(t\):

\[
Y_{\text{total}}(t)=\sum_d Y_d(t)
\]

Para los códigos seleccionados \(S\):

\[
Y_{\text{dominantes}}(t)=\sum_{d\in S}Y_d(t)
\]

\[
Y_{\text{residual}}(t)=\sum_{d\notin S}Y_d(t)
\]

También se conservará una serie individual para cada \(d\in S\).

La identidad:

\[
Y_{\text{total}}(t)
=Y_{\text{dominantes}}(t)+Y_{\text{residual}}(t)
\]

deberá verificarse mediante pruebas.

### 6.1 Series devueltas

Por cada entidad:

- `total`.
- `dominant_combined`.
- `residual`.
- `defect_15`, por ejemplo.
- `defect_37`, por ejemplo.

La ausencia de registros de un defecto en una ventana se interpretará como cero
solo si existe evidencia de actividad de la entidad en dicha ventana. No se
rellenarán periodos sin datos de producción como ceros.

## 7. Métricas básicas

Para total, dominantes, cada defecto individual y residual:

- Suma de eventos.
- Media por ventana.
- Mediana.
- Mínimo y máximo.
- P10, P25, P75, P90 y P95.
- IQR.
- Desviación estándar.
- MAD.
- Coeficiente de variación cuando la media sea distinta de cero.
- Asimetría y curtosis.
- Pendiente Theil-Sen.
- Autocorrelación en retardos configurados.
- Número y duración de episodios elevados.
- Área acumulada sobre el umbral.

Mientras no exista exposición medida, se conservarán además las tasas proxy
basadas en la hipótesis de producción nominal.

## 8. Contribución y concentración

### 8.1 Contribución por defecto

\[
p_d(t)=\frac{Y_d(t)}{Y_{\text{total}}(t)}
\]

Solo se calculará cuando \(Y_{\text{total}}(t)>0\).

Se mostrarán conjuntamente:

- Conteo absoluto.
- Tasa proxy.
- Contribución porcentual.

Así se evitará interpretar un aumento porcentual causado únicamente por el
descenso de otros defectos como un empeoramiento absoluto.

### 8.2 Concentración dominante

\[
C_S(t)=\frac{\sum_{d\in S}Y_d(t)}{Y_{\text{total}}(t)}
\]

Indicará qué proporción del rechazo se concentra en los códigos seleccionados.

### 8.3 Entropía

\[
H(t)=-\sum_d p_d(t)\log p_d(t)
\]

Interpretación:

- Entropía baja: pocos defectos concentran el rechazo.
- Entropía alta: el rechazo está repartido.

Se añadirá la entropía normalizada:

\[
H_N(t)=\frac{H(t)}{\log(k)}
\]

donde \(k\) es el número de categorías activas. Su rango será aproximadamente
`0–1`.

### 8.4 Número efectivo de defectos

\[
N_{\text{efectivo}}(t)=e^{H(t)}
\]

Expresa cuántos defectos igualmente frecuentes producirían una diversidad
equivalente.

### 8.5 Índice Herfindahl-Hirschman

\[
HHI(t)=\sum_d p_d(t)^2
\]

Valores altos indican concentración en pocos códigos.

### 8.6 Ratios entre dominantes

Para dos códigos seleccionados \(a\) y \(b\):

\[
R_{a,b}(t)=\frac{Y_a(t)+\epsilon}{Y_b(t)+\epsilon}
\]

Se utilizará un pseudoconteo documentado para evitar divisiones por cero. El
ratio se presentará preferentemente en escala logarítmica:

\[
\log R_{a,b}(t)
\]

## 9. Comparación total frente a residual

El objetivo es responder:

> ¿Cuánto cambia la estabilidad aparente de la producción al retirar los
> defectos seleccionados?

Para cada entidad se compararán total y residual mediante:

- Diferencia de medias y medianas.
- Reducción relativa de mediana.
- Reducción de MAD.
- Reducción de IQR.
- Reducción de desviación estándar.
- Cambio del coeficiente de variación.
- Cambio de P95.
- Cambio de pendiente.
- Cambio de autocorrelación.
- Cambio del número de episodios elevados.
- Cambio de duración máxima de episodios.

Ejemplo:

\[
\Delta MAD =
\frac{MAD_{\text{total}}-MAD_{\text{residual}}}
     {MAD_{\text{total}}}
\]

Un valor alto indicará que los defectos seleccionados explicaban gran parte de
la variabilidad absoluta.

No se afirmará que el residual es normal solo porque disminuya su dispersión.
También se evaluarán:

- Nivel residual.
- Tendencia residual.
- Persistencia.
- Distribución entre ramales.
- Presencia de defectos secundarios emergentes.

## 10. Episodios y cambios temporales

### 10.1 Referencia robusta

Para cada serie se calculará una referencia móvil basada inicialmente en:

- Mediana de las últimas `N` ventanas válidas.
- MAD de las mismas ventanas.

El tamaño `N` será configurable y se expresará en ventanas.

### 10.2 Episodio elevado

Definición inicial:

\[
Y(t) > \operatorname{mediana}_{N}(t)
       + k\cdot 1.4826\cdot MAD_N(t)
\]

y persistencia durante al menos `m` ventanas.

Parámetros:

- `baseline_windows`.
- `mad_multiplier`.
- `minimum_episode_windows`.

### 10.3 Características del episodio

- Inicio y final.
- Duración.
- Máximo.
- Exceso medio.
- Área sobre referencia.
- Defectos responsables.
- Ramales afectados.
- Simultaneidad entre ramales.
- Tiempo de recuperación.

### 10.4 Change-point detection

Se evaluarán:

- PELT.
- Binary Segmentation.
- Ruptures con coste `l1` o `rbf`.

Primera versión:

- PELT sobre series suavizadas.
- Coste robusto.
- Penalización configurable.
- Resultado etiquetado como cambio estadístico, no avería.

Se ejecutará sobre:

- Total.
- Cada dominante.
- Dominantes combinados.
- Residual.
- Entropía o concentración.

## 11. Relaciones entre defectos

### 11.1 Correlación contemporánea

Por producción y ramal:

- Spearman como medida principal.
- Pearson solo como complemento.

Spearman es preferible inicialmente por asimetría, ceros y relaciones
monótonas no necesariamente lineales.

### 11.2 Correlación cruzada

Para códigos \(a\) y \(b\):

\[
\rho_{a,b}(l)=corr(Y_a(t),Y_b(t-l))
\]

Retardos propuestos:

- `-12` a `+12` ventanas de 10 minutos.
- Equivalente a dos horas en cada dirección.

Se corregirá por múltiples comparaciones y se exigirán suficientes pares
válidos.

### 11.3 Información mutua

Permitirá detectar dependencia no lineal. Se tratará como medida exploratoria,
sensible al tamaño muestral y a los parámetros del estimador.

### 11.4 Coincidencia de episodios

Se calculará:

- Porcentaje de episodios simultáneos.
- Retardo mediano entre inicios.
- Jaccard entre ventanas marcadas como elevadas.
- Probabilidad condicional de episodio de B después de episodio de A.

### 11.5 Granger y VAR

Solo se habilitarán en una fase avanzada si:

- Hay longitud temporal suficiente.
- Las series pueden hacerse aproximadamente estacionarias.
- El orden del modelo es estable.
- Los residuos pasan diagnósticos básicos.

La interpretación será:

> Aporta capacidad predictiva temporal adicional.

No:

> Es la causa física del otro defecto.

## 12. Perfil composicional de producción

### 12.1 Vector de composición

Para una producción \(p\):

\[
\mathbf{p}_p=(p_{p,1},p_{p,2},\ldots,p_{p,D})
\]

donde:

\[
p_{p,d}=\frac{Y_{p,d}}{\sum_jY_{p,j}}
\]

La suma de componentes es uno. Por ello, aplicar PCA directamente sobre
porcentajes puede generar correlaciones espurias.

### 12.2 Selección de variables

Se excluirán o agruparán códigos extremadamente raros mediante parámetros:

- Presencia mínima en número de producciones.
- Contribución global mínima.
- Número máximo de códigos.

Los códigos excluidos se podrán agrupar como `otros`, pero `otros` no se usará
como una variable ordinaria en todos los modelos sin revisar su efecto.

### 12.3 Tratamiento de ceros

Las transformaciones log-ratio no admiten ceros.

Se aplicará reemplazo multiplicativo de ceros o un pseudoconteo pequeño,
documentando:

- Método.
- Valor.
- Número de celdas afectadas.
- Sensibilidad de resultados a ese valor.

### 12.4 Transformación CLR

\[
clr(p_d)=\log\left(\frac{p_d}{g(\mathbf{p})}\right)
\]

donde \(g(\mathbf{p})\) es la media geométrica de la composición.

La matriz CLR será la entrada inicial para:

- PCA.
- Distancias euclídeas equivalentes a distancia de Aitchison.
- Clustering.

### 12.5 Alternativa ILR

ILR podrá incorporarse si se definen balances interpretables entre grupos de
defectos. CLR será más sencillo para la primera exploración y para explicar
cargas por código.

## 13. PCA

### 13.1 Entradas

Modos:

- `production_composition`: una fila por producción.
- `branch_composition`: una fila por producción y ramal.
- `window_composition`: una fila por ventana.

La primera versión implementará `production_composition`.

Pipeline:

1. Construir recuentos por observación y defecto.
2. Filtrar códigos raros.
3. Convertir a proporciones.
4. Reemplazar ceros.
5. Aplicar CLR.
6. Centrar variables.
7. Ajustar PCA.

No se escalarán automáticamente las columnas CLR a varianza unitaria sin una
justificación adicional, porque alteraría el significado composicional.

### 13.2 Resultados

- Varianza explicada por componente.
- Varianza acumulada.
- Coordenadas de cada producción.
- Cargas de cada defecto.
- Contribución de cada defecto a cada componente.
- Error de reconstrucción.

### 13.3 Gráficos

- Scree plot.
- Plano PC1-PC2.
- Plano PC1-PC3 si procede.
- Biplot con un número limitado de cargas.
- Heatmap de cargas.
- Tabla de defectos más influyentes por componente.

### 13.4 Interpretación

Ejemplo:

- PC1 con cargas altas de `15` y `37`: eje de concentración en dominantes.
- PC2 contraponiendo `15` y `37`: eje de predominio relativo.
- PC3 con defectos secundarios: posible patrón residual.

Las etiquetas se generarán como resúmenes descriptivos, no nombres automáticos
de mecanismos físicos.

### 13.5 Restricción por tamaño muestral

Con 17 producciones:

- El máximo rango útil es reducido.
- Las componentes pueden ser inestables.
- No se usarán demasiados códigos.
- Se mostrará la relación observaciones/variables.
- Se recomendará análisis por producción y ramal para ampliar observaciones,
  manteniendo la dependencia agrupada.

Se añadirá bootstrap de cargas o análisis de sensibilidad antes de interpretar
componentes como robustas.

## 14. Distancias entre perfiles

Se calcularán:

- Distancia de Aitchison sobre CLR.
- Jensen-Shannon sobre proporciones.
- Distancia coseno como complemento descriptivo.

La interfaz permitirá seleccionar una producción y ordenar las demás por
similitud.

Resultados:

- Matriz de distancias.
- Heatmap.
- Producciones más parecidas.
- Producciones más distintas.
- Defectos que explican mayor diferencia entre cada par.

## 15. Clustering

### 15.1 Métodos iniciales

Principal:

- Clustering jerárquico aglomerativo sobre distancia de Aitchison.
- Enlace Ward cuando se trabaje en coordenadas CLR euclídeas.

Complementarios:

- K-means sobre componentes principales seleccionadas.
- HDBSCAN solo cuando haya suficientes observaciones temporales.

No se incluirá UMAP como método de clustering. Podrá usarse únicamente como
proyección visual exploratoria.

### 15.2 Elección del número de grupos

Se evaluarán:

- Silhouette.
- Calinski-Harabasz.
- Davies-Bouldin.
- Dendrograma.
- Estabilidad por bootstrap.

Con 17 producciones, las métricas no serán suficientes para declarar un número
verdadero de grupos. Se ofrecerá un rango pequeño, por ejemplo `2–5`.

### 15.3 Caracterización de grupos

Para cada cluster:

- Producciones integrantes.
- Perfil medio y mediano.
- Defectos sobrerrepresentados.
- Dispersión interna.
- Distancia al resto de clusters.
- Línea y ramales presentes.
- Nivel total de rechazo y concentración.

La agrupación por composición se separará de la agrupación por severidad. Dos
producciones pueden tener el mismo perfil relativo y niveles absolutos muy
distintos.

### 15.4 Análisis paralelo

Se ofrecerán dos modos:

- `composition_only`: forma de la distribución de defectos.
- `composition_and_severity`: composición más indicadores de nivel.

El modo por defecto será `composition_only`.

## 16. Detección de perfiles atípicos

Métodos:

- Distancia robusta al centro en espacio PCA.
- Error de reconstrucción PCA.
- Distancia media a vecinos.
- Isolation Forest en fase posterior.

Con muestras pequeñas se priorizarán distancias transparentes sobre modelos de
anomalía complejos.

Salida:

- Puntuación de atipicidad.
- Producciones de referencia más próximas.
- Defectos que más contribuyen a la anomalía.

## 17. Comparación entre ramales

Para cada producción:

- Perfil composicional por ramal.
- Distancia entre ramales.
- Correlación de series dominantes.
- Correlación residual.
- Coincidencia de episodios.
- Diferencia de concentración dominante.
- Defectos específicos de ramal.

Interpretación:

- Ambos ramales empeoran simultáneamente: señal compatible con causa común.
- Solo uno empeora: señal localizada.
- Perfiles similares con distinta severidad: mismo patrón, distinta intensidad.
- Perfiles distintos: mecanismos o clasificación diferentes.

## 18. Visualizaciones

### 18.1 Bloque de descomposición

- Series superpuestas: total, dominantes y residual.
- Pequeño múltiplo por defecto seleccionado.
- Opción de escala compartida o independiente.
- Conmutador entre conteo, tasa proxy y contribución.

### 18.2 Bloque de estabilidad

- Boxplots total frente a residual.
- Violin plots total frente a residual.
- Tabla de cambios de MAD, IQR, P95 y episodios.
- Change points sobre las series.

### 18.3 Bloque de composición

- Área apilada de contribuciones.
- Evolución de entropía.
- Evolución de HHI.
- Evolución de concentración dominante.
- Ratio entre dominantes.

### 18.4 Bloque multivariante

- Scree plot.
- PCA PC1-PC2.
- Biplot.
- Dendrograma.
- Heatmap de distancias.
- Perfiles medios de cluster.

Todos los gráficos deberán incluir:

- Unidad.
- Nivel de agregación.
- Ventana.
- Códigos seleccionados.
- Advertencia `proxy` cuando corresponda.
- Explicación breve de interpretación.

## 19. Diseño de API

Se recomienda separar el análisis avanzado del endpoint estadístico actual para
no aumentar indefinidamente un único contrato.

### 19.1 Catálogo

```http
GET /api/defect-analysis/catalog
```

Devolverá:

- Defectos disponibles.
- Máximo seleccionable.
- Métricas.
- Métodos PCA.
- Métodos de clustering.
- Rangos de parámetros.
- Versiones de definición.

### 19.2 Descomposición

```http
POST /api/defect-analysis/decomposition
```

Petición:

```json
{
  "selection": {
    "production_ids": ["AV23_PROD_001", "AV23_PROD_002"],
    "branches": ["AV23-1", "AV23-2"],
    "window": "10m",
    "filters": {}
  },
  "dominant_defect_codes": [15, 37],
  "episode_options": {
    "baseline_windows": 12,
    "mad_multiplier": 3.0,
    "minimum_episode_windows": 2
  }
}
```

Respuesta:

- Series descompuestas.
- Métricas total/dominante/residual.
- Indicadores de concentración.
- Episodios.
- Advertencias.
- Metadatos de limpieza y exposición.

### 19.3 Perfiles

```http
POST /api/defect-analysis/profiles
```

Petición:

```json
{
  "selection": {
    "production_ids": ["AV23_PROD_001", "AV23_PROD_002"],
    "branches": []
  },
  "observation_level": "production",
  "profile_mode": "composition_only",
  "rare_category_policy": {
    "minimum_productions": 2,
    "minimum_global_share": 0.001,
    "maximum_categories": 20
  },
  "zero_replacement": {
    "method": "multiplicative",
    "value": 0.000001
  }
}
```

### 19.4 PCA

```http
POST /api/defect-analysis/pca
```

Respuesta:

- Transformación aplicada.
- Códigos incluidos y excluidos.
- Varianza explicada.
- Scores.
- Loadings.
- Diagnósticos.
- URLs de gráficos.

### 19.5 Clustering

```http
POST /api/defect-analysis/clustering
```

Parámetros:

- Método.
- Distancia.
- Número de clusters o rango.
- Número de componentes PCA opcional.
- Semilla.

Respuesta:

- Asignaciones.
- Métricas de calidad.
- Estabilidad.
- Perfiles por cluster.
- URLs de gráficos.

## 20. Modelos de dominio

Contenedores propuestos:

- `DominantDefectSelection`.
- `DecomposedTemporalPoint`.
- `SeriesDecomposition`.
- `ConcentrationMetrics`.
- `EpisodeDefinition`.
- `DefectEpisode`.
- `CompositionProfile`.
- `PcaResult`.
- `PcaLoading`.
- `ClusterAssignment`.
- `ClusterProfile`.
- `ProfileDistance`.

Los modelos HTTP serán Pydantic. Los resultados internos inmutables podrán usar
`dataclass(frozen=True, slots=True)`.

Los motores se aislarán mediante `Protocol`:

- `DefectDecompositionEngine`.
- `CompositionTransformer`.
- `DimensionalityReductionEngine`.
- `ClusteringEngine`.
- `EpisodeDetector`.

No se crearán clases separadas si una función pura pequeña resulta más clara.

## 21. Arquitectura

```text
API
  -> casos de uso de análisis de defectos
      -> repositorio de perfiles/series
      -> descomposición y concentración
      -> episodios y dependencias
      -> transformación composicional
      -> PCA / clustering
      -> renderizadores
```

Reglas:

- Polars prepara tablas y agregaciones.
- NumPy/SciPy realizan operaciones numéricas.
- scikit-learn implementa PCA y clustering.
- Un paquete composicional específico solo se añadirá si reduce riesgo frente a
  implementar CLR y reemplazo de ceros de forma probada.
- Seaborn/Matplotlib renderizan imágenes estadísticas.
- Plotly podrá usarse en el frontend para exploración interactiva cuando el
  contrato devuelva datos suficientemente compactos.

## 22. Caché y reproducibilidad

La clave de caché incluirá:

- Versión de definición.
- Selección de producciones y ramales.
- Ventana y filtros.
- Códigos dominantes.
- Parámetros de episodios.
- Política de categorías raras.
- Reemplazo de ceros.
- Método PCA o clustering.
- Semilla.
- Versión del catálogo.

Cada respuesta incluirá:

- `definition_version`.
- Parámetros efectivos.
- Semilla.
- Número de observaciones.
- Variables incluidas.
- Variables descartadas.
- Advertencias.
- Tiempo de ejecución.

## 23. Validación estadística

### 23.1 Pruebas matemáticas

- Total = dominantes + residual.
- Contribuciones suman uno, salvo redondeo.
- CLR suma aproximadamente cero por fila.
- Distancia de Aitchison es simétrica.
- PCA reproduce resultados con semilla/configuración fija.
- Asignaciones de clustering son reproducibles cuando el método lo permite.

### 23.2 Sensibilidad

Repetir resultados variando:

- Pseudoconteo.
- Umbral de categorías raras.
- Número de componentes.
- Número de clusters.
- Defectos seleccionados.
- Ventana temporal.

Un resultado se considerará robusto si la interpretación principal no cambia
ante variaciones razonables.

### 23.3 Bootstrap

- Intervalos de métricas de concentración.
- Estabilidad de cargas PCA.
- Estabilidad de clusters.

El remuestreo respetará grupos completos:

- Por producción para perfiles globales.
- Por bloques temporales para series.

No se realizará bootstrap ingenuo de ventanas independientes cuando exista
autocorrelación.

### 23.4 Validación industrial

Para cada patrón relevante se preparará una ficha:

- Producciones afectadas.
- Periodo.
- Ramales.
- Defectos responsables.
- Magnitud.
- Persistencia.
- Evidencia estadística.
- Hipótesis física pendiente.
- Datos adicionales necesarios.

## 24. Riesgos

### 24.1 Muestra pequeña

Diecisiete producciones no permiten concluir que los clusters sean universales.
Los resultados serán hipótesis exploratorias.

### 24.2 Datos composicionales

Usar porcentajes sin transformación puede inducir correlaciones espurias.

### 24.3 Falta de exposición real

La severidad seguirá basada parcialmente en producción nominal asumida.

### 24.4 Confusión entre defecto y botella

Los eventos no equivalen necesariamente a botellas únicas rechazadas.

### 24.5 Autocorrelación

Las ventanas temporales consecutivas no son observaciones independientes.

### 24.6 Selección posterior al resultado

Elegir defectos porque dominan el Pareto y después probar su importancia con
los mismos datos introduce sesgo de selección. Los resultados deberán
etiquetarse como exploratorios o validarse en producciones distintas.

### 24.7 Causalidad

PCA, clustering, correlación y Granger no demuestran causas físicas.

## 25. Estrategia de pruebas

### Backend

- Fixtures con defectos conocidos.
- Selección de cero, uno, dos y tres defectos.
- Rechazo de cuatro defectos.
- Defectos ausentes en una producción.
- Ventanas sin actividad.
- Identidad total/dominante/residual.
- Métricas de concentración.
- Reemplazo de ceros y CLR.
- PCA con matriz conocida.
- Clustering reproducible.
- Caché diferenciada por selección.
- Formato uniforme de errores.

### Frontend

- Límite de tres selecciones.
- Sugerencias top 1/2/3.
- Estado desactualizado al cambiar códigos.
- Render de series descompuestas.
- Interpretaciones visibles.
- Estados vacío, carga y error.

### Integración

- Comparar dos producciones reales.
- Seleccionar `15` y `37`.
- Confirmar identidad de series.
- Confirmar que los valores erróneos filtrados no reaparecen.
- Ejecutar PCA y clustering.
- Verificar trazabilidad de parámetros.

## 26. Fases de implementación

Cada fase se implementará y revisará de forma independiente.

### Fase 1. Contratos y selección

- Añadir catálogo de defectos para análisis.
- Añadir selector de hasta tres códigos.
- Añadir contratos Pydantic.
- Añadir validaciones.

**Aceptación:** el usuario puede seleccionar códigos sin ejecutar aún modelos
multivariantes.

### Fase 2. Descomposición temporal

- Crear dataset canónico por defecto y ventana.
- Calcular total, individuales, combinada y residual.
- Añadir series y gráficos.
- Verificar identidad contable.

**Aceptación:** se pueden comparar total y residual para cualquier selección.

### Fase 3. Estabilidad y concentración

- Añadir MAD, IQR, P95 y cambios relativos.
- Añadir concentración, entropía, HHI y número efectivo.
- Añadir ratios entre dominantes.
- Añadir boxplots y evolución.

**Aceptación:** se cuantifica si la variabilidad disminuye al retirar dominantes.

### Fase 4. Episodios

- Implementar referencia robusta.
- Detectar episodios sostenidos.
- Calcular duración, área y recuperación.
- Comparar simultaneidad entre ramales.

**Aceptación:** se identifican periodos concretos auditables.

### Fase 5. Dependencias temporales

- Spearman.
- Correlación cruzada.
- Coincidencia de episodios.
- Información mutua.

**Aceptación:** se describen relaciones contemporáneas y retardadas sin lenguaje
causal.

### Fase 6. Perfiles composicionales

- Construir matriz por producción.
- Filtrar categorías raras.
- Tratar ceros.
- Aplicar CLR.
- Añadir distancias y heatmap.

**Aceptación:** los perfiles son comparables con métodos adecuados para
composiciones.

### Fase 7. PCA

- Implementar motor PCA.
- Añadir scree plot, scores y cargas.
- Añadir diagnósticos y advertencias por muestra pequeña.
- Añadir sensibilidad básica.

**Aceptación:** cada separación entre producciones puede explicarse mediante
cargas de defectos.

### Fase 8. Clustering

- Añadir clustering jerárquico.
- Añadir dendrograma.
- Añadir métricas y caracterización.
- Añadir estabilidad por bootstrap.

**Aceptación:** los grupos son reproducibles y sus perfiles están explicados.

### Fase 9. Modelos temporales avanzados

- Change-point detection.
- PCA/clustering por ventana.
- Isolation Forest o HDBSCAN si el volumen lo justifica.
- VAR/Granger si se cumplen supuestos.

**Aceptación:** solo se habilitan métodos cuyos diagnósticos sean válidos.

### Fase 10. Preparación para ML supervisado

- Definir etiqueta de empeoramiento.
- Construir retardos y variables móviles.
- Evitar fuga temporal.
- Validar dejando producciones completas fuera.
- Comparar regresión logística, árboles y boosting.
- Explicar con permutación y SHAP.

Esta fase queda fuera de la primera ampliación, pero los datasets se diseñarán
para permitirla.

## 27. Prioridad recomendada

Orden de mayor valor inmediato:

1. Selección de hasta tres defectos.
2. Series total/dominantes/residual.
3. Comparación de estabilidad.
4. Concentración y entropía.
5. Episodios y ramales.
6. Perfiles y distancias.
7. PCA.
8. Clustering.
9. Dependencias temporales avanzadas.
10. ML supervisado.

Este orden permite obtener conclusiones interpretables antes de introducir
modelos más complejos.

## 28. Resultado esperado

El usuario podrá seleccionar producciones, ramales y hasta tres defectos desde
el Pareto. La aplicación mostrará:

- Comportamiento total.
- Comportamiento individual de cada dominante.
- Comportamiento conjunto.
- Comportamiento residual.
- Cambio de estabilidad al retirar dominantes.
- Concentración y diversidad de defectos.
- Episodios y relación entre ramales.
- Similitud entre perfiles.
- Componentes PCA y defectos que las explican.
- Clusters exploratorios y sus características.

La salida deberá permitir formular hipótesis industriales concretas sin
confundir peso estadístico, capacidad predictiva y causalidad física.

## 29. Corrección de la interpretación geométrica del PCA

La interpretación de un punto en el plano PCA deberá hacerse usando la
combinación completa de cargas, no atribuyendo cada dirección a un único
defecto.

Para una ventana \(i\), su coordenada en la primera componente es:

\[
PC1_i = \sum_d z_{i,d}l_{d,1}
\]

donde:

- \(z_{i,d}\) es el valor CLR del defecto \(d\) en la ventana.
- \(l_{d,1}\) es la carga de ese defecto en PC1.

Por tanto, una ventana situada a la derecha no puede describirse simplemente
como:

> Tiene más defecto 4.

Si el defecto 4 tiene carga positiva alta en PC1, sí contribuye a desplazar una
ventana hacia la derecha cuando su balance logarítmico relativo es alto. Sin
embargo, la posición final también depende simultáneamente de:

- Los demás defectos con carga positiva.
- Los defectos con carga negativa.
- La magnitud de todos los valores CLR de esa ventana.
- La relación relativa entre defectos, no solo sus cantidades absolutas.

Una formulación correcta sería:

> Las ventanas situadas a la derecha presentan un balance composicional alineado
> con el conjunto de cargas positivas de PC1 y opuesto al conjunto de cargas
> negativas.

### 29.1 Información que deberá mostrar la interfaz

Para cada componente:

- Todas las cargas incluidas en el modelo, no solo las ocho primeras.
- Cargas positivas y negativas separadas.
- Contribución cuadrática aproximada de cada variable:

\[
contrib_{d,k}=\frac{l_{d,k}^{2}}{\sum_j l_{j,k}^{2}}
\]

- Defectos que forman el polo positivo.
- Defectos que forman el polo negativo.
- Calidad de representación de cada defecto en el plano PC1-PC2.

La tabla resumida seguirá mostrando las cargas principales, pero permitirá
expandir la lista completa.

### 29.2 Caracterización de regiones y clusters

Los clusters no se describirán directamente a partir del signo de una carga.
Para cada grupo se calcularán sus diferencias respecto al resto usando los
datos originales:

- Mediana del conteo por defecto.
- Mediana de la contribución por defecto.
- Mediana CLR por defecto.
- Diferencia estandarizada respecto al conjunto restante.
- Intervalo bootstrap de cada diferencia.

La etiqueta descriptiva de un cluster deberá derivarse de estas comparaciones,
no de una inspección visual aislada del plano PCA.

Ejemplo válido:

> El cluster 2 presenta mayor balance relativo de los defectos 4 y 38 y menor
> balance relativo de 2 y 26 que el resto de ventanas.

No:

> El cluster 2 es el cluster del defecto 4.

## 30. PCA completo y PCA residual

Se implementarán dos modos independientes por producción.

### 30.1 PCA completo

Incluye todos los defectos seleccionados por la política de variables.

Objetivo:

- Explicar los principales cambios de composición global.
- Comprobar si los defectos dominantes varían o permanecen relativamente
  constantes.
- Detectar balances entre dominantes y secundarios.

### 30.2 PCA residual

Excluye antes del ajuste los defectos dominantes seleccionados por el usuario.

Pipeline:

1. Retirar las columnas dominantes.
2. Recalcular el total residual de cada ventana.
3. Eliminar ventanas sin eventos residuales o tratarlas mediante una política
   explícita.
4. Renormalizar los defectos restantes para que formen una nueva composición.
5. Aplicar reemplazo de ceros.
6. Aplicar CLR.
7. Ajustar un PCA nuevo, exclusivo de esa producción.

El PCA residual no será una simple ocultación gráfica de cargas. Será un modelo
nuevo sobre una composición nueva.

### 30.3 Comparación entre ambos modelos

Para cada producción:

- Varianza explicada.
- Número de variables.
- Cargas principales.
- Estabilidad bootstrap de cargas.
- Separación de ventanas.
- Calidad y estabilidad de clusters.
- Defectos secundarios descubiertos.

Las coordenadas de ambos modelos no se compararán directamente, porque sus
espacios y variables son distintos.

La comparación deberá responder:

- ¿Los dominantes explican la estructura principal?
- ¿Al retirarlos aparece un patrón secundario estable?
- ¿El residual es homogéneo o contiene varios regímenes?
- ¿Cambian las ventanas identificadas como atípicas?

## 31. Utilidad y análisis temporal de clusters

Los clusters solo se considerarán útiles si pueden relacionarse con
comportamientos temporales y características observables.

### 31.1 Línea temporal de estados

Cada ventana conservará:

- Timestamp.
- Cluster asignado.
- Distancia al centro del cluster.
- Probabilidad o confianza cuando el método la proporcione.
- Total de eventos.
- Eventos dominantes.
- Eventos residuales.
- Ramal.

Se mostrará una banda temporal:

```text
10:00 C1
10:10 C1
10:20 C1
10:30 C2
10:40 C2
```

Esto permitirá distinguir:

- Estados persistentes.
- Cambios graduales.
- Ventanas aisladas.
- Alternancia rápida compatible con ruido.

### 31.2 Persistencia

Por cluster:

- Número de apariciones.
- Número de episodios contiguos.
- Duración media y mediana.
- Duración máxima.
- Porcentaje de ventanas aisladas.
- Probabilidad de permanecer en el mismo cluster.

\[
P(C_{t+1}=i\mid C_t=i)
\]

Un cluster con apariciones aisladas y baja persistencia no deberá interpretarse
como un estado estable de máquina.

### 31.3 Matriz de transiciones

\[
P_{ij}=P(C_{t+1}=j\mid C_t=i)
\]

Se calcularán:

- Matriz completa.
- Transiciones más habituales.
- Estados de entrada y salida.
- Secuencias anteriores a episodios de empeoramiento.

La matriz se calculará por producción y, cuando proceda, por ramal.

### 31.4 Caracterización industrial

Para cada cluster:

- Perfil composicional mediano.
- Eventos totales, dominantes y residuales.
- MAD e IQR.
- Concentración y entropía.
- Ramales predominantes.
- Días y franjas horarias.
- Posición dentro de la producción.
- Episodios elevados contenidos.
- Probabilidad de empeoramiento posterior.

Solo después de esta caracterización podrá proponerse una interpretación física.

### 31.5 Relación con empeoramientos

Para un horizonte \(h\):

\[
P(E_{t+h}=1\mid C_t=c)
\]

donde \(E\) representa la etiqueta de empeoramiento.

Se comparará con la probabilidad base:

\[
lift(c,h)=
\frac{P(E_{t+h}=1\mid C_t=c)}
     {P(E_{t+h}=1)}
\]

Un cluster podría resultar útil como estado precursor si:

- Tiene un lift claramente superior a uno.
- Aparece antes del empeoramiento, no únicamente durante él.
- El resultado se mantiene en producciones distintas.

### 31.6 Validación del clustering

Antes de mostrar clusters como resultados relevantes:

- Silhouette.
- Davies-Bouldin.
- Calinski-Harabasz.
- Estabilidad al cambiar semilla.
- Estabilidad al cambiar número de clusters.
- Estabilidad mediante bootstrap por bloques.
- Comparación contra una única nube sin estructura.
- Persistencia temporal.

K-Means seguirá siendo exploratorio. Si la nube no presenta separación robusta,
la interfaz mostrará:

> No se detectan grupos internos suficientemente estables.

### 31.7 Alternativas

Se evaluarán:

- Clustering jerárquico.
- Gaussian Mixture Models para pertenencia probabilística.
- HDBSCAN para grupos de forma irregular y ruido.
- Hidden Markov Models si el objetivo pasa a ser identificar estados temporales.

Los Hidden Markov Models serán especialmente relevantes si la continuidad
temporal aporta más información que la separación geométrica.

## 32. Análisis específico de defectos dominantes

Los defectos dominantes se estudiarán como series individuales, como conjunto y
en relación con el residual.

### 32.1 Niveles de análisis

Para cada defecto dominante \(d\):

- Conteo \(Y_d(t)\).
- Contribución \(p_d(t)\).
- Tasa proxy.
- Pendiente.
- Mediana móvil.
- MAD móvil.
- Episodios elevados.

Para el conjunto dominante \(S\):

\[
Y_S(t)=\sum_{d\in S}Y_d(t)
\]

\[
C_S(t)=\frac{Y_S(t)}{Y_{total}(t)}
\]

Y para el residual:

\[
Y_R(t)=Y_{total}(t)-Y_S(t)
\]

### 32.2 Dependencia de la estabilidad respecto a los dominantes

Se comparará la estabilidad total y residual mediante:

- MAD.
- IQR.
- P95.
- Coeficiente de variación.
- Autocorrelación.
- Pendiente robusta.
- Número de change points.
- Número, duración y área de episodios elevados.
- Diferencia entre ramales.

Indicadores:

\[
Dependencia_{MAD}=
\frac{MAD_{total}-MAD_{residual}}{MAD_{total}}
\]

\[
Dependencia_{IQR}=
\frac{IQR_{total}-IQR_{residual}}{IQR_{total}}
\]

Una reducción fuerte indicará que los dominantes explican una parte importante
de la inestabilidad absoluta.

También se calculará la estabilidad de las contribuciones. Un defecto puede ser
alto y estable en conteo, pero variar en proporción, o viceversa.

### 32.3 Descomposición de varianza

Se estudiará cuánto de la variabilidad de \(Y_{total}\) está asociado con:

- Cada dominante.
- Covariación entre dominantes.
- Residual.

Como:

\[
Y_{total}=Y_S+Y_R
\]

\[
Var(Y_{total})=
Var(Y_S)+Var(Y_R)+2Cov(Y_S,Y_R)
\]

Esta identidad permitirá distinguir:

- Dominantes variables por sí mismos.
- Residual variable.
- Dominantes y residual que empeoran juntos.
- Compensación entre ambos.

La contribución no se expresará como porcentaje simple cuando la covarianza sea
negativa o impida una partición aditiva intuitiva.

### 32.4 Aparición de dominantes y deterioro posterior

Se crearán variables temporales:

- Nivel actual.
- Media y mediana móvil.
- Pendiente en 30, 60 y 120 minutos.
- Cambio respecto a referencia reciente.
- Racha elevada.
- Área acumulada.
- Contribución al total.

Para un horizonte \(h\), se estudiará:

\[
P(E_{t+h}=1\mid A_d(t)=1)
\]

donde:

- \(A_d(t)\): episodio elevado del defecto dominante.
- \(E_{t+h}\): empeoramiento posterior total o residual.

Se comparará contra:

\[
P(E_{t+h}=1\mid A_d(t)=0)
\]

Métricas:

- Riesgo relativo.
- Diferencia absoluta de probabilidades.
- Odds ratio.
- Lift.
- Intervalos bootstrap por bloques.

### 32.5 Antes, durante y después

Para cada inicio de episodio dominante se construirán ventanas de evento:

```text
-120 min ... inicio ... +120 min
```

Se calculará la trayectoria media o mediana de:

- Total.
- Residual.
- Otros dominantes.
- Entropía.
- Dispersión entre ramales.

Esto permitirá distinguir:

- El dominante precede al deterioro residual.
- Aparece simultáneamente.
- Es una consecuencia tardía.
- Es alto sin afectar al resto.

### 32.6 Correlaciones retardadas

Se calcularán correlaciones cruzadas entre:

- Dominante y total futuro.
- Dominante y residual futuro.
- Dominante de un ramal y residual del otro.
- Pares de dominantes.

Las series se centrarán o diferenciarán cuando sea necesario para evitar que una
tendencia común produzca relaciones espurias.

No se usará correlación como prueba causal.

### 32.7 Modelos explicativos

Primera etapa:

- Regresión robusta.
- Regresión logística para empeoramiento.
- Modelos con retardos.

Variables:

- Dominantes actuales y retardados.
- Pendientes.
- Persistencia.
- Concentración.
- Ramal.
- Hora y día de producción.

Se compararán modelos:

1. Solo información residual.
2. Solo dominantes.
3. Dominantes + residual.

La mejora de rendimiento del tercer modelo cuantificará el valor predictivo
adicional de los dominantes.

La validación dejará producciones completas fuera para evitar fuga temporal.

### 32.8 Relación entre ramales

Para cada dominante:

- Correlación contemporánea entre ramales.
- Correlación con retardos.
- Coincidencia de episodios.
- Diferencia de nivel.
- Tiempo entre inicio en un ramal y aparición en otro.

Interpretación exploratoria:

- Aparición simultánea: compatible con factor común.
- Aparición localizada: compatible con condición específica de ramal.
- Propagación con retardo: hipótesis de secuencia que deberá contrastarse
  físicamente.

### 32.9 Change points

Se detectarán cambios de nivel en:

- Cada dominante.
- Dominantes combinados.
- Residual.
- Concentración dominante.

Se comparará la proximidad temporal entre cambios:

- Cambio dominante antes que residual.
- Cambio simultáneo.
- Cambio residual sin dominante.

Se aplicará tolerancia temporal, por ejemplo `±2` ventanas.

### 32.10 Clasificación de resultados

Cada dominante podrá quedar descrito provisionalmente como:

- `alto_estable`: mucho volumen, poca variabilidad.
- `impulsor_inestabilidad`: explica una reducción fuerte de MAD/IQR al retirarlo.
- `precursor`: sus episodios elevan el riesgo de empeoramiento futuro.
- `concurrente`: aumenta durante el deterioro, pero no antes.
- `localizado`: asociado principalmente a un ramal.
- `comun`: aparece coordinadamente en varios ramales.
- `sin_evidencia`: no muestra relación robusta.

Estas etiquetas serán resultados estadísticos auditables, no diagnósticos
causales.

## 33. Nuevas visualizaciones

### 33.1 PCA

- Selector `Completo / Residual`.
- Biplot con polos positivos y negativos.
- Tabla completa de cargas.
- Contribución de variables a componentes.
- Perfil mediano de regiones o clusters en variables originales.

### 33.2 Clusters

- Banda temporal de clusters.
- Matriz de transición.
- Duración de episodios.
- Perfil por cluster.
- Total, dominantes y residual por cluster.
- Probabilidad de empeoramiento posterior.

### 33.3 Dominantes

- Panel individual por código.
- Total/dominante/residual sincronizados.
- Event study antes-después.
- Correlación cruzada.
- Comparación entre ramales.
- Change points alineados.
- Tabla de dependencia de estabilidad.

## 34. Ampliación de fases

Las siguientes fases se incorporarán después de la implementación básica ya
existente.

### Fase 11. Interpretación PCA rigurosa

- Mostrar cargas completas.
- Calcular contribuciones a componentes.
- Caracterizar polos positivos y negativos.
- Caracterizar clusters usando variables originales.

**Aceptación:** ninguna conclusión se deriva de una sola carga aislada.

### Fase 12. PCA residual

- Añadir modo completo/residual.
- Renormalizar composición residual.
- Comparar resultados y estabilidad.

**Aceptación:** retirar dominantes ajusta un modelo nuevo y reproducible por
producción.

### Fase 13. Clustering temporal

- Línea temporal.
- Persistencia.
- Matriz de transiciones.
- Caracterización de clusters.
- Métricas de robustez.

**Aceptación:** no se muestran clusters como estados si carecen de estabilidad
geométrica y temporal.

### Fase 14. Dependencia respecto a dominantes

- Comparación total/residual.
- Descomposición de varianza.
- Change points.
- Relaciones entre ramales.

**Aceptación:** se cuantifica cuánto depende la variabilidad global de cada
dominante.

### Fase 15. Precursores

- Etiqueta de empeoramiento.
- Variables retardadas.
- Event studies.
- Riesgo relativo y lift.
- Validación dejando producciones fuera.

**Aceptación:** un defecto solo se etiqueta como precursor si aporta evidencia
temporal reproducible en producciones no usadas para formular la hipótesis.

## 35. Ampliación: interpretación operacional

Los resultados actuales sugieren que una asociación entre defectos dominantes y
residual puede ser intensa en la misma ventana y desaparecer en horizontes de
una o dos horas. Este patrón no se interpretará automáticamente como que los
dominantes provocan el resto de los defectos.

Se registrarán como hipótesis operacionales alternativas:

1. **Capacidad de inspección manual:** una concentración puntual de defectos
   dominantes puede aumentar la carga del IFA y facilitar que coincidan o pasen
   temporalmente otros defectos.
2. **Causa común de proceso:** una misma condición de máquina puede elevar a la
   vez los dominantes y el residual.
3. **Cambio de clasificación:** ante una ventana con muchos eventos puede
   cambiar el reparto de códigos sin cambiar proporcionalmente la calidad real.
4. **Desalineación temporal:** el instante registrado puede no coincidir
   exactamente con el instante físico de generación o inspección.
5. **Variación del volumen fabricado:** mientras no exista denominador real, una
   parte del aumento conjunto podría deberse a cambios de producción.

La aplicación mostrará estas explicaciones como hipótesis compatibles con los
datos, nunca como conclusiones automáticas.

### 35.1 Evidencia esperada para la hipótesis de carga del IFA

La hipótesis ganará plausibilidad si se observa:

- Asociación contemporánea positiva entre dominantes y residual.
- Elevación residual durante episodios dominantes, pero no varias ventanas
  después.
- Ausencia de un incremento progresivo previo del residual.
- Recuperación rápida al finalizar el episodio dominante.
- Mayor asociación en turnos, horas o ramales con mayor carga observada.
- En el futuro, incremento de avisos sin incremento proporcional de rechazos o
  aumento del tiempo entre aviso y rechazo.

La hipótesis perderá plausibilidad si:

- El residual aumenta antes que los dominantes.
- El deterioro permanece elevado durante horas tras desaparecer el dominante.
- La relación está localizada de forma estable en un ramal concreto.
- Los avisos y rechazos futuros indican una alteración física sostenida del
  proceso.

### 35.2 Resultado textual auditable

El backend generará hechos cuantitativos y códigos de interpretación. El
frontend compondrá mensajes controlados, por ejemplo:

> Existe asociación contemporánea, pero no evidencia de anticipación sostenida.
> El resultado es compatible con saturación puntual de inspección o con una
> condición común; los datos actuales no permiten distinguir ambas hipótesis.

No se generarán diagnósticos causales mediante texto libre.

## 36. Análisis de clusters por turno

El objetivo es determinar si los patrones composicionales identificados por el
clustering aparecen preferentemente en turnos concretos o si su distribución es
compatible con una alternancia temporal sin estructura de turno.

### 36.1 Configuración de turnos

Los horarios no se codificarán dentro de los cálculos. Se modelarán mediante una
configuración explícita:

```text
Turno 1: hora_inicio, hora_fin
Turno 2: hora_inicio, hora_fin
Turno 3: hora_inicio, hora_fin
Zona horaria: Europe/Madrid
```

Requisitos:

- Soportar turnos que atraviesen medianoche.
- Validar que no existan solapamientos ni huecos involuntarios.
- Permitir una configuración por defecto revisable por el usuario.
- Conservar en la respuesta el horario usado para garantizar trazabilidad.
- Asignar cada ventana por su instante inicial.
- Marcar las ventanas fuera de calendario como `sin_turno`.

La primera implementación podrá utilizar una configuración local común para
todas las producciones, pero el contrato permitirá calendarios futuros por
línea, fecha o centro.

### 36.2 Tabla de contingencia

Para cada producción se calculará:

\[
N_{s,c}=\#\{\text{ventanas del turno }s\text{ asignadas al cluster }c\}
\]

y:

\[
P(C=c\mid S=s)=\frac{N_{s,c}}{\sum_j N_{s,j}}
\]

Se mostrarán:

- Número de ventanas por turno y cluster.
- Porcentaje dentro de cada turno.
- Porcentaje del cluster que procede de cada turno.
- Diferencia en puntos porcentuales respecto al conjunto de la producción.

Los porcentajes siempre incluirán su denominador para evitar conclusiones a
partir de turnos con pocas ventanas.

### 36.3 Contraste de dependencia

Se aplicará:

- Chi-cuadrado de independencia cuando los recuentos esperados sean válidos.
- Test exacto o agrupación prudente cuando existan celdas escasas.
- V de Cramér como tamaño de efecto.
- Residuos estandarizados por celda para localizar combinaciones
  turno-cluster sobrerrepresentadas o infrarrepresentadas.

El valor `p` no será suficiente para declarar relevancia. La interfaz mostrará
conjuntamente:

- Tamaño muestral.
- V de Cramér.
- Intervalo bootstrap por días o bloques temporales.
- Magnitud de las diferencias porcentuales.

### 36.4 Robustez entre días

Para evitar que un único día explique la asociación:

- Se recalcularán los perfiles turno-cluster por día de producción.
- Se medirá la dispersión de \(P(C\mid S)\) entre días.
- Se aplicará bootstrap por día, no por ventana independiente.
- Se indicará en cuántos días se repite la dirección de la asociación.

Una asociación de turno se considerará consistente solo si aparece en varios
días y no exclusivamente en una franja aislada.

### 36.5 Persistencia dentro del turno

Por turno y cluster se calcularán:

- Número de episodios.
- Duración mediana, P90 y máxima.
- Probabilidad de permanencia.
- Proporción de ventanas aisladas.
- Tasa de entradas y salidas por hora.

Esto permitirá distinguir:

- Un cluster frecuente por muchos episodios breves.
- Un estado persistente durante una parte importante del turno.
- Un patrón concentrado alrededor de un cambio de turno.

### 36.6 Transiciones alrededor del relevo

Para cada cambio de turno se analizará una ventana configurable, inicialmente:

```text
-60 min ... relevo ... +60 min
```

Se calcularán:

- Distribución de clusters antes y después.
- Número de transiciones.
- Cambio de total, dominantes y residual.
- Cambio de los defectos que distinguen cada cluster.
- Frecuencia con que aparece una transición concreta tras el relevo.

Este análisis será descriptivo. Un cambio coincidente con el relevo puede estar
relacionado con personal, ajustes, limpieza, materia prima u otros factores no
registrados.

### 36.7 Visualizaciones de turno

Cada producción incluirá:

- Heatmap `turno × cluster` con porcentajes y recuentos.
- Barras apiladas normalizadas por turno.
- Banda temporal de clusters con fondo sombreado por turno.
- Tabla de tamaños de efecto y residuos estandarizados.
- Comparación de persistencia por turno.
- Panel específico de transiciones alrededor del relevo.

Los números de cluster seguirán siendo internos a cada producción y no se
compararán directamente entre producciones.

## 37. Explorador de episodios largos de cluster

Se añadirá una herramienta para localizar físicamente los periodos que sustentan
un patrón estadístico.

### 37.1 Definición de episodio

Un episodio será una secuencia de ventanas consecutivas de la misma producción y
cluster. La continuidad se romperá cuando:

- Cambie el cluster.
- Exista un hueco temporal mayor que la ventana esperada más una tolerancia.
- Cambie la producción.

La primera versión permitirá filtrar por:

- Cluster.
- Duración mínima.
- Día.
- Turno.
- Ramal.
- Nivel total o residual mínimo.

### 37.2 Atributos del episodio

Cada episodio devolverá:

- Identificador estable dentro de la consulta.
- Producción y línea.
- Cluster.
- Inicio, fin y duración.
- Número de ventanas.
- Turno o turnos atravesados.
- Ramales presentes.
- Total, dominantes y residual: suma, mediana, máximo y área sobre referencia.
- Valores absolutos de los defectos que distinguen el cluster.
- Contribuciones de esos defectos.
- Distancia mediana al centroide.
- Outliers eliminados durante el preprocesado.

Cuando el episodio atraviese turnos se conservará como un único episodio, pero
se expondrá su reparto temporal por turno.

### 37.3 Atribución por ramal

El clustering puede ajustarse con un perfil agregado, pero el diagnóstico debe
volver a las series originales por ramal. Para cada episodio se calculará:

- Recuento y contribución de cada ramal.
- Diferencia absoluta y relativa entre ramales.
- Ramal con mayor desviación respecto a su referencia.
- Coincidencia temporal del patrón en varios ramales.

No se atribuirá un episodio a un ramal usando únicamente el total agregado.

### 37.4 Comparación con referencia

Para defectos como `4` y `38`, o cualquier código que caracterice el cluster, se
mostrarán:

- Nivel absoluto durante el episodio.
- Diferencia respecto a las dos horas anteriores.
- Diferencia respecto a la mediana del mismo turno y ramal.
- Diferencia respecto al resto de ventanas de la producción.
- Intervalo bootstrap por bloques.

Se distinguirá claramente entre:

- Diferencia de composición, expresada en puntos porcentuales.
- Diferencia de recuento absoluto.
- Diferencia de tasa proxy.

Así se evitará interpretar un defecto como elevado solo porque ocupa más peso
relativo cuando el total ha disminuido.

### 37.5 Vista de detalle

Al seleccionar un episodio, el frontend mostrará:

- Serie temporal ampliada con margen anterior y posterior.
- Total, dominantes y residual.
- Defectos característicos seleccionables.
- Separación por ramal.
- Fondo de turno.
- Inicio y fin del episodio.
- Referencia utilizada.

La tabla de episodios será ordenable por duración, intensidad, residual, ramal y
distancia al centroide.

## 38. Análisis relacional de defectos dominantes

Los hasta tres defectos seleccionados se estudiarán individualmente, por pares y
como conjunto. El objetivo será responder si aparecen juntos, si evolucionan de
forma similar, si son persistentes y si su comportamiento depende del turno o
del ramal.

### 38.1 Coaparición en la misma ventana

Para cada par \(A,B\):

- Número de ventanas con A.
- Número de ventanas con B.
- Número de ventanas con ambos.
- Índice de Jaccard.
- \(P(B>0\mid A>0)\).
- \(P(A>0\mid B>0)\).
- Lift de coaparición.
- Odds ratio con intervalo de confianza.

También se repetirá el cálculo usando episodio elevado, no solo presencia
mayor que cero.

Los intervalos se estimarán mediante bootstrap por bloques para respetar la
autocorrelación.

### 38.2 Correlación de niveles y cambios

Se calcularán por cada par:

- Spearman sobre recuentos.
- Spearman sobre contribuciones.
- Correlación sobre primeras diferencias.
- Correlación sobre desviaciones respecto a mediana móvil.
- Correlación parcial controlando por total proxy, turno y ramal cuando sea
  estadísticamente viable.

La correlación de niveles responderá si suelen ser altos a la vez. La
correlación de cambios responderá si crecen o decrecen conjuntamente.

No se usarán proporciones composicionales sin advertir que el cierre al 100 %
puede inducir correlaciones negativas.

### 38.3 Relación temporal

Para retardos configurables entre `-120` y `+120` minutos:

- Correlación cruzada.
- Lift de episodio futuro.
- Diferencia de probabilidad de empeoramiento.
- Tiempo mediano entre inicios de episodios.

Convención:

- Retardo positivo: A antecede a B.
- Retardo cero: relación contemporánea.
- Retardo negativo: B antecede a A.

Solo se hablará de precedencia temporal cuando el resultado:

- Sea consistente en varios retardos próximos.
- Tenga suficientes episodios.
- Se mantenga al separar días o producciones.
- No dependa de una única anomalía.

### 38.4 Tendencia y estabilidad

Para cada dominante:

- Medianas móviles de 30, 60 y 120 minutos.
- Pendiente robusta Theil-Sen por tramo.
- EWMA.
- Change points.
- Autocorrelación.
- Duración y área de episodios.
- Tiempo de recuperación a referencia.
- Tendencia por día, turno y ramal.

Se diferenciarán:

- `alto_estable`: nivel alto con variabilidad limitada.
- `creciente`: tendencia sostenida positiva.
- `intermitente`: picos breves y frecuentes.
- `persistente`: episodios largos.
- `sincronizado`: evolución conjunta con otro dominante.
- `localizado`: concentrado en un turno o ramal.

### 38.5 Dependencia del residual

Para cada dominante y para el conjunto se calculará:

- Asociación contemporánea con residual.
- Asociación con residual futuro.
- Reducción de MAD, IQR y P95 al retirarlo.
- Cambio de episodios residuales.
- Cambio de change points.
- Event study antes, durante y después.

El resultado separará explícitamente:

- **Concurrente:** dominante y residual se elevan en la misma ventana.
- **Precursor inmediato:** el residual se eleva en las ventanas siguientes.
- **Precursor sostenido:** el efecto permanece y se reproduce.
- **Independiente:** no existe relación robusta.
- **Inverso o compensatorio:** aumenta uno mientras disminuye el otro.

### 38.6 Interacciones con turno y ramal

Los análisis anteriores se estratificarán por:

- Producción.
- Turno.
- Ramal.
- Día de producción.

Cuando haya muestra suficiente se ajustará un modelo con interacciones:

\[
Residual_{t+h}\sim
Dominante_t + Turno + Ramal +
Dominante_t:Turno + Dominante_t:Ramal
\]

La interacción permitirá detectar si la asociación dominante-residual cambia
según el contexto. No se ajustarán modelos con más parámetros que información
disponible.

### 38.7 Panel de dominantes

La interfaz incluirá:

- Matriz de coaparición.
- Matriz de correlación con selector de definición.
- Curvas de correlación cruzada.
- Series sincronizadas.
- Episodios alineados.
- Comparación por turno y ramal.
- Tabla de clasificación provisional y evidencia que la sustenta.

Cada cifra tendrá tooltip con definición, unidad, denominador y cautela
interpretativa.

## 39. Integración futura de avisos y rechazos

Cuando esté disponible la nueva tabla, se conservará la distinción entre:

- **Aviso:** defecto detectado o señalado.
- **Rechazo:** botella efectivamente retirada o contabilizada como rechazada.

No se sumarán ambos eventos como si representaran la misma magnitud.

### 39.1 Contrato mínimo esperado

Campos deseables:

```text
timestamp
linea
ramal
cavidad
codigo_defecto
tipo_evento          # aviso | rechazo
cantidad
identificador_origen
produccion_id        # directo o resoluble temporalmente
```

Campos recomendados:

```text
turno
operador_o_equipo
motivo
estado
timestamp_resolucion
identificador_botella_o_lote
```

La ingestión validará zona horaria, duplicados, códigos desconocidos y
correspondencia con producción.

### 39.2 Métricas aviso-rechazo

Por defecto, ramal, turno y producción:

- Número de avisos.
- Número de rechazos.
- Proporción de avisos que terminan en rechazo cuando exista trazabilidad.
- Rechazos por aviso como proxy agregado cuando no exista enlace individual.
- Latencia aviso-rechazo.
- Avisos repetidos antes del rechazo.
- Avisos sin rechazo posterior.
- Rechazos sin aviso previo.

### 39.3 Análisis de saturación

Para estudiar la hipótesis del IFA se analizará si, al aumentar la carga de
avisos:

- Cambia la proporción de rechazos por código.
- Aumenta la latencia hasta el rechazo.
- Aumentan los defectos residuales.
- Se concentra la actividad en ciertos turnos.
- Aparecen secuencias de avisos sin resolución.

Se considerarán modelos:

- Regresión logística para probabilidad de rechazo.
- Modelos de conteo Poisson o binomial negativa.
- Análisis de supervivencia para tiempo aviso-rechazo.
- Modelos jerárquicos con efectos por producción, turno y ramal.

La selección dependerá de la granularidad y trazabilidad reales de la tabla.

### 39.4 Vinculación temporal

Si no existe identificador común, los avisos y rechazos solo podrán relacionarse
por ventanas temporales. En ese caso:

- Se definirá una tolerancia explícita.
- Se evitará afirmar correspondencia uno a uno.
- Se calcularán asociaciones agregadas.
- Se realizará análisis de sensibilidad con varias tolerancias.

## 40. Contratos y arquitectura de la ampliación

La implementación seguirá la arquitectura por capas existente y no incorporará
lógica estadística en componentes React ni en rutas FastAPI.

### 40.1 Backend

Responsabilidades:

- **Dominio:** definiciones de turno, episodio, asociación y evidencia.
- **Aplicación:** orquestación por producción y composición de resultados.
- **Infraestructura:** lectura Parquet y futura lectura de avisos/rechazos.
- **Estadística:** funciones puras para contrastes, bootstrap y modelos.
- **API:** validación Pydantic v2 y traducción al formato REST.

Se usarán:

- `from __future__ import annotations`.
- Type hints compatibles con `mypy --strict`.
- `Path` para rutas.
- `Protocol` para repositorios y motores estadísticos.
- `TypedDict` solo en estructuras internas que no sean contratos de API.
- Modelos Pydantic para entrada y salida.
- `asyncio.to_thread` para cálculos CPU o lectura bloqueante cuando proceda.
- Errores uniformes, `request_id` y logging estructurado con `structlog`.

### 40.2 Contratos REST propuestos

Se ampliará el recurso actual sin crear endpoints excesivamente específicos:

```text
POST /api/defect-analysis/shift-analysis
POST /api/defect-analysis/cluster-episodes
POST /api/defect-analysis/dominant-relations
POST /api/defect-analysis/warning-rejection-analysis   # futuro
```

Todos recibirán:

- Producciones seleccionadas.
- Ventana temporal.
- Defectos dominantes.
- Configuración de limpieza.
- Configuración de turnos cuando corresponda.

Todos devolverán resultados separados por producción. Nunca se ajustará un PCA,
clustering o modelo de episodios mezclando producciones sin que el endpoint y el
método lo indiquen expresamente.

### 40.3 Metadatos obligatorios de respuesta

- Versión de definición.
- Parámetros efectivos.
- Producción.
- Periodo analizado.
- Número de ventanas antes y después de limpieza.
- Outliers descartados.
- Advertencias por muestra insuficiente.
- Semilla aleatoria cuando exista.
- Tiempo de cálculo.

### 40.4 Frontend

Se ampliará el panel estadístico actual de forma progresiva:

- Navegación interna por `Resumen`, `Clusters`, `Dominantes` y `Turnos`.
- Estado de consulta común y tipado.
- Carga independiente por bloque para no recalcular todo al cambiar una vista.
- Mensajes vacíos y de muestra insuficiente.
- Tooltips interpretativos.
- Tablas exportables.
- Colores de cluster consistentes dentro de una producción.
- Comparación visual paralela, sin asumir equivalencia de etiquetas entre
  producciones.

## 41. Calidad, pruebas y rendimiento

### 41.1 Pruebas unitarias

Casos mínimos:

- Asignación de turnos, incluido cruce de medianoche.
- Detección de huecos y solapamientos.
- Segmentación de episodios con huecos temporales.
- Cálculo de duración y persistencia.
- Atribución por ramal.
- Jaccard, lift, odds ratio y correlaciones.
- Convención de retardos.
- Bootstrap reproducible.
- Separación entre recuento y composición.
- Manejo de series constantes y muestra insuficiente.
- Ausencia de mezcla entre producciones.

### 41.2 Pruebas de integración

- Contratos REST válidos e inválidos.
- Respuestas separadas por producción.
- Propagación de `request_id`.
- Formato uniforme de errores.
- Cancelación o timeout de cálculos largos.
- Compatibilidad con datos sin todos los ramales.

### 41.3 Pruebas frontend

- Selección y validación de turnos.
- Filtrado y apertura de episodios.
- Cambio de producción sin reutilizar resultados anteriores.
- Tooltips y unidades.
- Estados de carga, error y muestra insuficiente.
- Renderizado con uno, dos y tres dominantes.

### 41.4 Rendimiento

- Polars lazy para lecturas y agregaciones.
- Una sola lectura por producción y consulta cuando sea posible.
- Reutilización de la matriz temporal preprocesada.
- Caché acotada por parámetros efectivos.
- Límites de bootstrap y modelos configurables.
- Medición de duración mediante logs estructurados.

No se precalcularán combinaciones de turnos, dominantes y retardos. Los cálculos
serán on-premise y bajo demanda, reutilizando resultados intermedios.

## 42. Secuencia incremental de implementación

La ampliación no se realizará como un único cambio grande. Cada fase deberá
quedar probada y revisable antes de iniciar la siguiente.

### Fase 16. Fundamentos de turno

- Modelo y validación de calendario.
- Asignación de ventanas.
- Tabla turno-cluster.
- Pruebas unitarias.

**Aceptación:** todas las ventanas quedan asignadas de forma trazable y los
turnos que cruzan medianoche funcionan correctamente.

### Fase 17. Análisis y visualización por turno

- Chi-cuadrado, V de Cramér y residuos.
- Robustez por día.
- Persistencia y transiciones en relevos.
- Heatmap y banda temporal.

**Aceptación:** la interfaz diferencia frecuencia, tamaño de efecto y
consistencia entre días.

### Fase 18. Explorador de episodios

- Segmentación y ranking.
- Atributos absolutos y composicionales.
- Recuperación de series por ramal.
- Vista de detalle.

**Aceptación:** un episodio largo puede localizarse por fecha, hora, turno y
ramal, y contrastarse con los defectos que caracterizan el cluster.

### Fase 19. Relaciones entre dominantes

- Coaparición.
- Correlaciones de nivel y cambio.
- Retardos.
- Tendencia, persistencia y recuperación.
- Panel comparativo.

**Aceptación:** el usuario puede distinguir aparición conjunta, evolución
conjunta, precedencia temporal y dependencia contextual.

### Fase 20. Dominantes frente a residual

- Event studies.
- Clasificación concurrente/precursor/independiente.
- Interacciones con turno y ramal.
- Evidencia textual auditable.

**Aceptación:** ninguna etiqueta se presenta sin métricas, muestra, intervalo y
validación temporal asociada.

### Fase 21. Preparación para avisos y rechazos

- Definir contrato de ingestión.
- Implementar adaptador detrás de un `Protocol`.
- Mantener la funcionalidad desactivada mientras no haya datos.

**Aceptación:** incorporar la tabla futura no obliga a modificar los cálculos
actuales ni los contratos existentes.

### Fase 22. Modelos aviso-rechazo

- Conversión y latencia.
- Saturación.
- Modelos de conteo, clasificación o supervivencia según los datos.
- Validación por producción y bloques temporales.

**Aceptación:** avisos y rechazos conservan significado separado y cualquier
relación uno a uno está respaldada por trazabilidad real.

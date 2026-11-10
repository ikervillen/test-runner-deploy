# Scorecard comparativo de calidad

## Alcance

El scorecard compara las producciones seleccionadas mediante tasas proxy construidas con
la exposición nominal configurada. No certifica conformidad industrial y no sustituye
límites de calidad, severidades de defecto ni el número real de botellas fabricadas.

Con una sola producción se calculan todos los indicadores, pero no se asignan scores.

## Indicadores

### Nivel

- **Nivel central:** mediana de eventos por 1.000 botellas nominales.
- **Cola P95:** nivel que no supera el 95 % de las ventanas.

En ambos casos un valor menor se considera mejor.

### Estabilidad

- **MAD relativo:** `MAD / mediana`.
- **IQR relativo:** `(P75 - P25) / mediana`.

La normalización evita penalizar automáticamente una producción solo por tener una escala
mayor. Un valor menor representa menor dispersión relativa.

### Persistencia

- **Tiempo anómalo:** proporción de ventanas señaladas por z-score robusto, EWMA o CUSUM.
- **Episodio máximo:** ventanas del episodio anómalo más largo divididas por el total.
- **Área anómala normalizada:** exceso acumulado sobre la referencia dividido por
  `mediana × número de ventanas`.

Estas medidas separan los picos aislados de los deterioros prolongados.

### Composición

- **Deriva composicional:** distancia Aitchison mediana entre cada ventana y el perfil
  global de defectos. Se aplica transformación CLR y un pseudoconteo de `0,5` para tratar
  los ceros.
- **Concentración top 2:** contribución de los dos defectos dominantes.
- **Defectos efectivos:** exponencial de la entropía de la distribución de defectos.

Solo la deriva composicional puntúa. La concentración y la diversidad se muestran como
información causal: sin severidades industriales no puede afirmarse que una distribución
más concentrada o más diversa sea mejor.

### Homogeneidad

- **Separación relativa:** diferencia entre la mayor y menor tasa de ramal dividida por la
  mediana de la línea.
- **CV entre ramales:** desviación estándar de las tasas de ramal dividida por su media.

Un valor menor indica un comportamiento más homogéneo.

### Tendencia

- **Pendiente robusta:** pendiente Theil-Sen convertida a porcentaje del nivel por hora.
- **Cambio final:** cambio relativo entre la mediana del primer y último cuarto.
- **Ventanas en empeoramiento:** proporción de ventanas al menos un 30 % por encima de la
  mediana de su hora previa.

Valores menores representan mejor evolución; una pendiente negativa expresa mejora.

## Conversión a scores

Cada indicador puntuable se ordena dentro de la selección:

- Mejor valor: `100`.
- Peor valor: `0`.
- Valores intermedios: posición lineal entre ambos extremos.
- Empates: rango medio.

El sub-score de cada dimensión es la media de sus indicadores puntuables. El score global
es la media equiponderada de los seis sub-scores:

```text
Nivel + Estabilidad + Persistencia + Composición + Homogeneidad + Tendencia
---------------------------------------------------------------------------- = Score
                                      6
```

Los pesos son deliberadamente iguales mientras no existan criterios industriales
validados. Una futura ponderación deberá basarse en severidad, coste o especificaciones
de calidad, no en conveniencia estadística.

## Interpretación

El score responde a:

> ¿Qué producción presenta el comportamiento más favorable dentro de esta selección y en
> qué dimensiones?

No responde todavía a:

> ¿Es esta producción absolutamente buena o conforme?

Para responder a la segunda pregunta se necesitan denominadores medidos, límites
industriales y severidades por tipo de defecto.

El score global tampoco sustituye a la lectura temporal. Una producción puede presentar
un nivel relativamente estable y pocas anomalías puntuales mientras mantiene una
pendiente sostenida de empeoramiento. Por este motivo, la interfaz muestra de forma
separada el estado de trayectoria:

- **Mejora sostenida:** pendiente robusta y cambio final negativos.
- **Deterioro sostenido:** pendiente robusta y cambio final positivos.
- **Evolución temporal mixta:** ambos indicadores tienen signos distintos.

Esta etiqueta no modifica el score global mientras no existan pesos o límites
industriales validados; actúa como salvaguarda interpretativa para impedir que una
trayectoria desfavorable quede oculta por otras dimensiones favorables.

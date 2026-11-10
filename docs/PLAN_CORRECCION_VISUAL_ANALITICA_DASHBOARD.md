# Plan de correccion visual y explicabilidad analitica del dashboard

## Objetivo

Corregir visualizaciones que actualmente dificultan la interpretacion operativa del
dashboard. El criterio principal es que cada grafico tenga una lectura inmediata:
ejes claros, unidades claras, colores consistentes, leyendas fuera del area de datos
y ninguna metrica duplicada en eje, color o tamano.

## Principios de implementacion

- Cambios pequenos y verificables, por entregables.
- No redisenar modulos completos si el problema es local.
- Mantener separacion entre backend, calculo estadistico y representacion frontend.
- Evitar metricas compuestas sin explicacion visible.
- Evitar repetir una misma variable en eje, color y tamano.
- Usar escalas cromaticas consistentes cuando representen la misma magnitud.
- Si una visualizacion no aporta lectura operativa clara, se elimina o se degrada a tabla.

## Entregable 1. Legibilidad inmediata

### Historico

- Paretos:
  - Mantener codigos de defecto en los ejes.
  - Sacar la leyenda codigo-defecto fuera del area de barras.
  - Evitar que la leyenda pise titulo, barras o curva acumulada.
  - Usar nombres en espanol cuando existan en el catalogo.

- Matriz de diferencias:
  - Revisar contraste, tamano de celdas y formato numerico.
  - Asegurar que los numeros se leen.
  - Si sigue sin aportar interpretacion robusta, moverla a tabla o eliminarla.

### Riesgo de incidencias

- Mapas desagregados:
  - No colorear una burbuja con la misma variable que ya esta en X o Y.
  - Si no existe una tercera variable claramente util, usar color categorico por produccion.
  - Mantener tamano como metrica temporal solo si esta declarada en el grafico.

### Rendimiento ZC

- Mapas ZC frente a gravedad:
  - Si el color representa `horas activas`, todos los graficos deben usar exactamente la
    misma escala y la misma paleta.
  - La leyenda debe tener el mismo minimo, maximo, titulo y direccion semantica.

## Entregable 2. Semaforo de limites por criticidad

Convertir los chips actuales en una tabla interpretable por calidad:

| Produccion | Criticidad | Valor observado | Limite | Estado | Motivo |
| --- | ---: | ---: | ---: | --- | --- |

Reglas:

- `Grave`, `Mayor`, `Menor`, `Leve`:
  - comparar el porcentaje observado de rechazos de esa criticidad contra el limite global.
  - mostrar valor observado y limite aplicado.

- `Critico`:
  - no usar limite porcentual fijo.
  - marcar como `Repetitivo` si el mismo defecto critico aparece repetido en la produccion.
  - cuando exista dato por cavidad, marcar tambien repeticion dentro de cavidad.

Lectura esperada:

- `OK`: dentro de limite.
- `Cerca`: por encima del 80 % del limite.
- `Supera`: supera el limite.
- `Repetitivo`: defecto critico repetido.

## Entregable 3. Contratos visuales de graficos

Cada grafico de burbujas debe mostrar de forma homogenea:

- `X`: variable, unidad y direccion.
- `Y`: variable, unidad y direccion.
- `Tamano`: variable, unidad y direccion.
- `Color`: variable, unidad y direccion.

Si el color no aporta una variable distinta, se elimina como escala numerica y se usa
color fijo por produccion.

## Entregable 4. Limpieza de metricas confusas

- Revisar todos los textos tipo `eventos por 1.000 botellas estimadas`.
- Cambiar a `rechazos registrados por ventana seleccionada` cuando la grafica represente
  sumas de `LISTA_DEFECTOS_RECH`.
- Explicar que un boxplot por dia solo es robusto con suficientes ventanas dentro del dia.
  Para ventana `turno`, debe priorizarse barra por turno o matriz dia-turno.

## Entregable 5. Validacion

Para cada entregable:

- `npm run build`.
- `uv run ruff check backend`.
- tests backend afectados con `--no-cov` cuando se ejecuten subconjuntos.
- revision visual local en `http://127.0.0.1:5174`.


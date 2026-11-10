# Anexo: metricas adicionales centradas en maquinas de inspeccion ZF

## 1. Proposito del anexo

Este documento es un anexo independiente al plan principal de analisis de
estabilidad de maquinas de inspeccion de zona fria (ZF).

El objetivo no es repetir las metricas ya definidas en el plan base, sino proponer
lineas adicionales centradas especificamente en el comportamiento de las maquinas
de inspeccion como sistemas de deteccion.

Por tanto, este anexo se centra en preguntas como:

- si una maquina esta detectando un defecto mas de lo habitual para el mismo
  modelo de botella;
- si una maquina se esta separando del comportamiento del resto de maquinas;
- si una subida de rechazo parece general de proceso o localizada en una maquina;
- si una maquina muestra deriva temporal durante la produccion;
- si el arranque, la estabilizacion o la parte final de una produccion tienen
  comportamientos distintos;
- si un defecto aparece de forma sincronizada entre maquinas o solo en una;
- si las detecciones se concentran en rafagas o en ventanas concretas;
- si la intensidad de los episodios anomalos queda mejor representada mediante
  area bajo la curva o exceso acumulado.

La idea principal es pasar de una lectura del tipo:

```text
La maquina tiene mucho rechazo.
```

a una lectura mas analitica:

```text
La maquina ZF-2 esta detectando el defecto X por encima de su historico
para este mismo modelo, se esta separando progresivamente del resto de maquinas
y ese patron no aparece con la misma intensidad en los avisos/rechazos registrados.
```

Este tipo de lectura no afirma una causa de forma automatica, pero ayuda a
formular hipotesis industriales mas defendibles.

---

## 2. Principio general: referencias en capas

Una duda clave es si la referencia debe ser global o especifica por maquina y
defecto.

La recomendacion es no elegir una unica referencia, sino crear una estructura de
referencias en capas. Cada capa responde a una pregunta distinta.

### 2.1 Referencia global del modelo de botella

Esta referencia compara la produccion actual contra el comportamiento historico
general del mismo modelo de botella.

```text
referencia_global_modelo =
  historico del mismo modelo de botella
  considerando todas las maquinas
  considerando todos los defectos
```

Responde a la pregunta:

```text
¿Esta produccion se comporta peor que lo habitual para este modelo?
```

Ejemplo de interpretacion:

```text
La produccion actual tiene una tasa de rechazo 2,1 veces superior a la mediana
historica de este modelo de botella.
```

Esta referencia es util como lectura general, pero no permite saber todavia si el
problema esta en una maquina concreta, en un defecto concreto o en una condicion
comun de produccion.

### 2.2 Referencia por modelo y defecto

Esta referencia compara la aparicion de un defecto concreto contra su historico
en el mismo modelo de botella.

```text
referencia_modelo_defecto =
  historico del mismo modelo de botella
  filtrado por defecto
```

Responde a la pregunta:

```text
¿Este defecto esta apareciendo mas de lo habitual para este modelo?
```

Ejemplo:

```text
El defecto AI_ALGO_PLI aparece 3,4 veces mas de lo habitual para este modelo.
```

Esta capa permite detectar si el problema esta asociado a un tipo de defecto,
pero aun no indica si todas las maquinas lo ven por igual.

### 2.3 Referencia por modelo y maquina

Esta referencia compara el comportamiento de una maquina concreta contra su
historico para el mismo modelo de botella.

```text
referencia_modelo_maquina =
  historico del mismo modelo de botella
  filtrado por maquina de inspeccion
```

Responde a la pregunta:

```text
¿Esta maquina esta rechazando mas de lo habitual cuando inspecciona este modelo?
```

Ejemplo:

```text
La maquina ZF-2 esta rechazando un 80% mas de lo habitual para este modelo,
mientras que el resto de maquinas estan dentro de rango.
```

Esta referencia ya empieza a aportar informacion especifica sobre la maquina.

### 2.4 Referencia por modelo, maquina y defecto

Esta es la referencia mas potente para analizar comportamiento especifico de
maquinas de inspeccion.

```text
referencia_modelo_maquina_defecto =
  historico del mismo modelo de botella
  filtrado por maquina de inspeccion
  filtrado por defecto
```

Responde a la pregunta:

```text
¿La maquina X esta detectando el defecto Y mas de lo habitual para este modelo?
```

Ejemplo:

```text
La maquina ZF-2 esta detectando AI_ALGO_PLI 3,5 veces por encima de su
comportamiento historico para este mismo modelo.
```

Esta capa permite buscar inconsistencias muy interesantes:

```text
El defecto sube en una maquina, pero no sube en el resto.
```

Lectura posible:

```text
Patron compatible con sensibilidad especifica, desajuste local de inspeccion,
ramal concreto o condicion particular de esa maquina.
```

En cambio:

```text
El defecto sube simultaneamente en todas las maquinas.
```

Lectura posible:

```text
Patron mas compatible con una causa comun de proceso, lote, formato o condicion
aguas arriba.
```

---

## 3. Indicadores de sobredeteccion maquina-defecto

Una linea nueva de analisis seria crear indicadores especificos de
sobredeteccion por maquina y defecto.

### 3.1 Ratio de sobredeteccion maquina-defecto

```text
ratio_sobredeteccion_maquina_defecto =
  tasa_actual_maquina_defecto
  /
  mediana_historica_maquina_defecto_mismo_modelo
```

Lectura:

```text
La maquina esta detectando ese defecto X veces mas de lo habitual para este
modelo.
```

Ejemplo:

```text
ratio_sobredeteccion_maquina_defecto = 3,5
```

Interpretacion:

```text
La maquina esta detectando ese defecto 3,5 veces por encima de su referencia
historica comparable.
```

### 3.2 Z robusto de sobredeteccion

El ratio puede ser engañoso cuando la tasa historica es muy baja. Por eso
conviene complementarlo con una desviacion robusta.

```text
z_robusto_maquina_defecto =
  (tasa_actual_maquina_defecto - mediana_historica_maquina_defecto)
  /
  MAD_historico_maquina_defecto
```

Cuando el MAD historico sea cero o demasiado bajo, debe aplicarse una regla de
proteccion, por ejemplo:

```text
MAD_utilizado = max(MAD_historico, epsilon)
```

Este indicador responde a:

```text
¿La maquina esta fuera de su variabilidad normal para ese defecto?
```

### 3.3 Exceso absoluto por 1.000 botellas

Para evitar alarmas por ratios altos sobre bases muy pequeñas, conviene medir el
exceso absoluto.

```text
exceso_absoluto_por_1000_maquina_defecto =
  tasa_actual_por_1000_maquina_defecto
  -
  mediana_historica_por_1000_maquina_defecto
```

Ejemplo:

```text
Historico: 0,1 rechazos / 1000
Actual:    0,4 rechazos / 1000
Ratio:     x4
Exceso:    +0,3 rechazos / 1000
```

Aunque el ratio sea alto, el impacto absoluto puede ser bajo.

Por eso se recomienda analizar conjuntamente:

```text
ratio_sobredeteccion_maquina_defecto
z_robusto_maquina_defecto
exceso_absoluto_por_1000_maquina_defecto
```

---

## 4. Comparacion simultanea entre maquinas para el mismo defecto

Ademas de comparar cada maquina contra su historico, interesa comparar las
maquinas entre si dentro de la misma produccion y ventana temporal.

Para cada defecto:

```text
tasa_m1_defecto_x
tasa_m2_defecto_x
tasa_m3_defecto_x
tasa_m4_defecto_x
```

### 4.1 Spread entre maquinas por defecto

```text
spread_maquinas_defecto =
  max(tasa_maquina_defecto)
  -
  min(tasa_maquina_defecto)
```

Lectura:

```text
Cuanta diferencia existe entre la maquina que mas detecta y la que menos detecta
ese defecto.
```

### 4.2 Ratio peor/mejor maquina por defecto

```text
ratio_peor_mejor_maquina_defecto =
  (max(tasa_maquina_defecto) + epsilon)
  /
  (min(tasa_maquina_defecto) + epsilon)
```

Este ratio debe tratarse con cuidado si la tasa minima es cero o muy baja.

### 4.3 Concentracion del defecto por maquina

Puede utilizarse un HHI especifico de maquinas para cada defecto.

```text
HHI_maquinas_defecto = sum(cuota_maquina_i ^ 2)
```

donde:

```text
cuota_maquina_i =
  rechazos_maquina_i_defecto
  /
  rechazos_totales_defecto
```

Lectura:

- HHI bajo: el defecto esta repartido entre maquinas.
- HHI alto: una maquina concentra gran parte del defecto.
- HHI alto con una sola maquina dominante: posible comportamiento local o
  sensibilidad particular de esa maquina.
- HHI bajo con subida generalizada: patron mas compatible con causa comun.

---

## 5. Pendientes especificas de maquina

La pendiente temporal es una metrica especialmente util para distinguir entre:

- maquina estable;
- maquina que mejora;
- maquina que empeora;
- maquina que se separa progresivamente del resto.

### 5.1 Pendiente global de maquina

```text
pendiente_maquina =
  slope(tasa_rechazo_maquina ~ tiempo)
```

Responde a:

```text
¿La maquina rechaza cada vez mas o cada vez menos a lo largo de la produccion?
```

Lectura:

- pendiente positiva: posible deterioro progresivo;
- pendiente negativa: posible estabilizacion o correccion;
- pendiente cercana a cero: comportamiento estable;
- pendiente cambiante: comportamiento irregular.

### 5.2 Pendiente maquina-defecto

```text
pendiente_maquina_defecto =
  slope(tasa_defecto_x_maquina_y ~ tiempo)
```

Esta metrica es mas fina que la pendiente total de la maquina, porque permite ver
si una maquina esta aumentando especificamente la deteccion de un defecto.

Ejemplo:

```text
El rechazo total de la maquina se mantiene estable, pero AI_ALGO_PLI sube y
AI_ALGO_MINCE baja.
```

En este caso, la tasa total puede ocultar un cambio importante en el patron de
deteccion.

### 5.3 Pendiente relativa frente al resto de maquinas

Esta metrica compara una maquina contra el comportamiento agregado del resto.

```text
pendiente_relativa_maquina =
  slope(
    tasa_maquina_x - mediana_tasa_resto_maquinas
    ~ tiempo
  )
```

Lectura:

```text
La maquina X se esta separando progresivamente del comportamiento del resto.
```

Esta metrica es especialmente util para detectar comportamientos desacoplados.

Si todas las maquinas suben a la vez, el problema puede ser comun. Si una sola
maquina se separa, la hipotesis de comportamiento local gana fuerza.

---

## 6. Indicadores de arranque, estabilizacion y degradacion final por maquina

No todos los rechazos tienen la misma lectura operativa. Un arranque malo que se
corrige no debe interpretarse igual que una deriva progresiva durante toda la
produccion.

### 6.1 Indice de arranque de maquina

```text
indice_arranque_maquina =
  tasa_rechazo_maquina_primer_tramo
  /
  tasa_rechazo_maquina_tramo_estable
```

Ejemplo de tramos:

```text
primer_tramo = primeros 30 o 60 minutos
tramo_estable = tramo central de la produccion
```

Lectura:

- indice alto: arranque inestable;
- indice cercano a 1: arranque similar al resto de la produccion;
- indice bajo: arranque bueno con posible empeoramiento posterior.

### 6.2 Indice de arranque relativo

Para diferenciar si el arranque malo es general o especifico de una maquina:

```text
indice_arranque_relativo_maquina =
  indice_arranque_maquina
  /
  mediana_indice_arranque_resto_maquinas
```

Lectura:

- todas las maquinas tienen arranque malo: posible ajuste general de produccion;
- solo una maquina tiene arranque malo: posible ajuste local de inspeccion;
- una maquina arranca mal y luego corrige: posible estabilizacion de maquina,
  receta, iluminacion o sensibilidad;
- una maquina arranca bien y luego empeora: posible deriva.

### 6.3 Indice de degradacion final

```text
indice_degradacion_final_maquina =
  tasa_rechazo_maquina_ultimo_tramo
  /
  tasa_rechazo_maquina_tramo_central
```

Lectura:

- alto: la maquina empeora hacia el final;
- normal: no hay deterioro final relevante;
- bajo: la maquina termina mejor que en el tramo central.

Puede calcularse tambien por maquina-defecto:

```text
indice_degradacion_final_maquina_defecto =
  tasa_defecto_maquina_ultimo_tramo
  /
  tasa_defecto_maquina_tramo_central
```

---

## 7. Autocorrelacion y correlacion cruzada entre maquinas

Conviene separar dos conceptos distintos: autocorrelacion y correlacion cruzada.

### 7.1 Autocorrelacion de una maquina

La autocorrelacion mide si una maquina tiene memoria temporal respecto a su propio
comportamiento.

```text
autocorrelacion_maquina_lag_1 =
  corr(tasa_maquina_t, tasa_maquina_t-1)
```

Lectura:

- autocorrelacion alta: cuando la maquina entra en estado alto, tiende a seguir
  alta;
- autocorrelacion baja: picos aislados;
- autocorrelacion alta en una sola maquina: posible comportamiento local
  persistente;
- autocorrelacion alta en todas las maquinas: posible causa comun sostenida.

### 7.2 Correlacion cruzada entre maquinas

La correlacion cruzada mide si dos maquinas suben y bajan juntas.

```text
correlacion_maquina_i_j =
  corr(tasa_maquina_i_t, tasa_maquina_j_t)
```

Puede calcularse tambien por defecto:

```text
correlacion_maquina_i_j_defecto_x =
  corr(tasa_maquina_i_defecto_x_t, tasa_maquina_j_defecto_x_t)
```

Lectura:

- correlacion alta: las maquinas se mueven juntas;
- correlacion baja: comportamiento desacoplado;
- una maquina con baja correlacion frente a todas: candidata a revision;
- correlacion alta para varios defectos: posible causa comun;
- correlacion baja solo para un defecto concreto: posible sensibilidad especifica
  de una maquina frente a ese defecto.

### 7.3 Correlacion cruzada con desfase

Puede haber desfases temporales por flujo, buffers, diferencias de ramal o
sincronizacion de datos.

```text
correlacion_lag_k_maquina_i_j =
  corr(tasa_maquina_i_t, tasa_maquina_j_t-k)
```

Indicadores derivados:

```text
lag_maxima_correlacion_entre_maquinas
correlacion_maxima_entre_maquinas
```

Lectura:

```text
La maquina 2 reproduce el patron de la maquina 1 con un desfase de 20 minutos.
```

Esta informacion puede ayudar a revisar sincronizacion, trazabilidad temporal o
flujo real de botellas entre zonas.

---

## 8. Area bajo la curva y exceso acumulado por maquina

Las duraciones de episodios altos indican cuanto tiempo dura un problema, pero no
si el problema esta ligeramente por encima del umbral o muy por encima.

Por eso puede ser interesante medir la intensidad acumulada.

### 8.1 Area de exceso sobre umbral

```text
AUC_exceso_tasa_maquina =
  suma(
    max(0, tasa_maquina_t - umbral_maquina)
    * duracion_ventana
  )
```

Lectura:

```text
Cuanta intensidad temporal de rechazo anomalo acumula la maquina.
```

### 8.2 Exceso acumulado de rechazos sobre lo esperado

Industrialmente puede ser mas defendible convertir el exceso en botellas.

```text
exceso_rechazos_ventana =
  max(0, tasa_maquina_t - tasa_esperada_maquina_t)
  * botellas_inspeccionadas_maquina_t
```

```text
exceso_rechazos_total_maquina =
  suma(exceso_rechazos_ventana)
```

Lectura:

```text
La maquina ha generado aproximadamente 240 rechazos por encima de su
comportamiento esperado.
```

Tambien puede calcularse por maquina-defecto:

```text
exceso_rechazos_total_maquina_defecto =
  suma(
    max(0, tasa_maquina_defecto_t - tasa_esperada_maquina_defecto_t)
    * botellas_inspeccionadas_maquina_t
  )
```

Esta metrica ayuda a priorizar porque combina anomalia e impacto real.

---

## 9. Concentracion temporal de rechazos por maquina

La idea de burstiness puede simplificarse en metricas mas faciles de interpretar
en planta.

### 9.1 Rechazo concentrado en las peores ventanas

```text
concentracion_temporal_top10_maquina =
  rechazos_maquina_en_top_10pct_ventanas
  /
  rechazos_totales_maquina
```

Lectura:

```text
El 62% del rechazo de esta maquina se concentra en el 10% de sus peores ventanas.
```

Esta metrica permite diferenciar:

- rechazo repartido de forma continua;
- rechazo concentrado en rafagas;
- ventanas concretas que explican gran parte del problema.

### 9.2 Indice simple de rafaga

```text
indice_rafaga_maquina =
  P95_tasa_rechazo_maquina
  /
  mediana_tasa_rechazo_maquina
```

Lectura:

- alto: la maquina tiene picos muy superiores a su comportamiento habitual;
- cercano a 1: comportamiento mas uniforme;
- alto solo en una maquina: posible inestabilidad local;
- alto en todas: posible condicion comun de proceso o producto.

Esta familia de metricas debe verse como diagnostico secundario, no como indicador
principal de calidad.

---

## 10. Indicador de comportamiento desacoplado de maquina

Una de las salidas mas interesantes seria detectar maquinas que se comportan de
forma desacoplada respecto al resto.

No se recomienda empezar con un score cerrado, sino con una etiqueta o semaforo
basado en varias evidencias.

### 10.1 Evidencias de desacoplamiento

Una maquina podria considerarse desacoplada si aparecen varias de estas señales:

```text
sobredeteccion_maquina_defecto alta
z_robusto_maquina_defecto alto
pendiente_relativa_vs_resto positiva
baja correlacion con el resto de maquinas
HHI_maquinas_defecto alto
exceso acumulado relevante
patron no replicado en otras maquinas
patron no explicado por avisos/rechazos equivalentes
```

### 10.2 Etiquetas operativas sugeridas

En lugar de un unico score numerico, al principio puede ser mas interpretable
mostrar etiquetas:

```text
Maquina coherente con el resto
Maquina con sobredeteccion puntual
Maquina con arranque inestable corregido
Maquina con deriva progresiva
Maquina desacoplada del resto
Maquina dominante en defecto concreto
```

Ejemplo de texto automatico:

```text
La maquina ZF-2 concentra el 68% de las detecciones del defecto AI_ALGO_PLI,
presenta una sobredeteccion de 3,5x frente a su historico comparable y muestra
baja correlacion temporal con el resto de maquinas. Patron compatible con
comportamiento desacoplado de inspeccion o condicion local del ramal.
```

---

## 11. Cruce entre maquinas de inspeccion y avisos/rechazos registrados

El usuario ya dispone de metricas relacionadas con avisos y rechazos. Este anexo
no pretende redefinirlas, sino proponer cruces especificos desde la perspectiva de
maquina de inspeccion.

### 11.1 Pendiente de maquina frente a pendiente de avisos/rechazos

```text
pendiente_maquina_defecto
vs
pendiente_avisos_rechazos_defecto
```

Lecturas posibles:

```text
Ambas pendientes suben
```

Interpretacion:

```text
La deteccion de maquina y los avisos/rechazos evolucionan de forma coherente.
```

```text
La maquina sube, pero los avisos/rechazos no suben
```

Interpretacion:

```text
Posible sobredeteccion, sensibilidad local o defecto detectado por maquina que no
se refleja en el sistema de avisos/rechazos.
```

```text
Los avisos/rechazos suben, pero la maquina no sube
```

Interpretacion:

```text
Posible problema no capturado por esa maquina, diferencia de criterio o falta de
alineacion entre fuentes.
```

### 11.2 Coherencia temporal entre maquinas e incidencias

```text
correlacion_maquina_avisos_defecto =
  corr(tasa_maquina_defecto_t, tasa_avisos_rechazos_defecto_t)
```

Tambien puede analizarse con desfase temporal:

```text
lag_maxima_correlacion_maquina_avisos
```

Lectura:

```text
Los avisos/rechazos aparecen 20 minutos despues de la subida de deteccion de la
maquina.
```

Esto puede ayudar a entender si la maquina anticipa, acompaña o no refleja los
problemas registrados.

---

## 12. Matriz de diagnostico centrada en maquinas

Las metricas anteriores permiten construir una matriz de lectura industrial sin
afirmar causalidad directa.

| Patron observado | Lectura posible |
|---|---|
| Una maquina sube, el resto no | Posible comportamiento local, sensibilidad o ramal especifico |
| Todas las maquinas suben a la vez | Posible causa comun de proceso, lote o formato |
| Una maquina sube solo en un defecto | Posible sensibilidad especifica a ese defecto |
| Una maquina sube en muchos defectos | Posible configuracion general, iluminacion, camara o estado de maquina |
| Pendiente positiva solo en una maquina | Posible deriva progresiva local |
| Arranque alto y estabilizacion posterior | Posible ajuste inicial o estabilizacion de inspeccion |
| Alta concentracion temporal en pocas ventanas | Rechazo en rafagas, evento puntual o inestabilidad intermitente |
| Alta sobredeteccion pero bajo exceso absoluto | Alarma relativa, impacto industrial bajo |
| Alta sobredeteccion y alto exceso absoluto | Prioridad alta de revision |
| Baja correlacion con avisos/rechazos | Posible desacople entre deteccion automatica y registros operativos |

---

## 13. Datos adicionales recomendables para reforzar este anexo

Para que estas metricas sean realmente utiles, conviene intentar capturar o
preparar los siguientes campos si existen:

```text
maquina_inspeccion
ramal
modelo_bottle/articulo
produccion_id
defecto
familia_defecto
timestamp_inicio
timestamp_fin
botellas_inspeccionadas
botellas_rechazadas
receta_inspeccion
version_receta
modo_auto_manual
estado_maquina
cambio_configuracion
cambio_formato
turno
operador o equipo, si aplica
score_defecto, si existe
umbral_defecto, si existe
vista/camara, si existe
coordenadas_bbox, si existen
```

Especialmente valiosos para diferenciar problema real frente a comportamiento de
inspeccion:

```text
receta_inspeccion
version_receta
score_defecto
umbral_defecto
vista/camara
estado_maquina
cambios de configuracion
```

---

## 14. Visualizaciones especificas para este anexo

Estas visualizaciones no sustituyen a las del plan base. Son complementarias y
centradas en maquinas.

### 14.1 Matriz maquina x defecto

Una tabla o heatmap con:

```text
filas = maquinas
columnas = defectos
color = z_robusto_maquina_defecto o ratio_sobredeteccion
```

Permite ver rapidamente:

- que maquina se sale;
- que defecto domina;
- si el patron es localizado o general.

### 14.2 Evolucion temporal relativa maquina vs resto

Serie temporal:

```text
linea 1 = tasa maquina seleccionada
linea 2 = mediana resto de maquinas
linea 3 = diferencia maquina - resto
```

Esta grafica es muy util para detectar separacion progresiva.

### 14.3 Ranking de maquinas desacopladas

Tabla con:

```text
maquina
defecto
ratio_sobredeteccion
z_robusto
exceso_absoluto_por_1000
pendiente_relativa
correlacion_con_resto
exceso_rechazos_total
etiqueta_diagnostico
```

### 14.4 Curva de exceso acumulado

Grafica acumulativa:

```text
X = tiempo
Y = exceso acumulado de rechazos sobre esperado
```

Permite ver cuando se genera el impacto principal.

### 14.5 Comparativa de arranque por maquina

Grafica de barras:

```text
indice_arranque_maquina
indice_arranque_relativo_maquina
```

Permite distinguir arranque general malo de arranque local de una maquina.

---

## 15. Priorizacion recomendada

Para no sobrecargar la primera version de este anexo, se recomienda priorizar en
este orden.

### Fase A: referencias y sobredeteccion

```text
ratio_sobredeteccion_maquina_defecto
z_robusto_maquina_defecto
exceso_absoluto_por_1000_maquina_defecto
```

Objetivo:

```text
Saber si una maquina detecta un defecto por encima de su historico comparable.
```

### Fase B: comparacion entre maquinas

```text
spread_maquinas_defecto
ratio_peor_mejor_maquina_defecto
HHI_maquinas_defecto
```

Objetivo:

```text
Saber si el defecto esta repartido entre maquinas o concentrado en una.
```

### Fase C: dinamica temporal de maquina

```text
pendiente_maquina_defecto
pendiente_relativa_vs_resto_maquinas
indice_arranque_maquina
indice_degradacion_final_maquina
```

Objetivo:

```text
Saber si la maquina arranca mal, deriva, mejora o se separa del resto.
```

### Fase D: intensidad acumulada

```text
AUC_exceso_tasa_maquina
exceso_rechazos_total_maquina
exceso_rechazos_total_maquina_defecto
```

Objetivo:

```text
Medir impacto real de los episodios anomalos, no solo su duracion.
```

### Fase E: coherencia con otras fuentes

```text
correlacion_maquina_vs_avisos_rechazos
lag_maxima_correlacion_maquina_avisos
comparacion_pendientes_maquina_vs_avisos_rechazos
```

Objetivo:

```text
Saber si lo que detecta la maquina coincide con lo que registran los avisos y
rechazos.
```

---

## 16. Precauciones especificas

Estas metricas pueden aportar mucho valor, pero hay que tratarlas con algunas
precauciones.

### 16.1 Historico insuficiente

Para referencias muy especificas, como modelo + maquina + defecto, puede haber
poco historico.

En ese caso conviene mostrar cobertura:

```text
n_producciones_historicas_comparables
n_ventanas_historicas_comparables
botellas_historicas_inspeccionadas
```

Si la cobertura es baja, la interpretacion debe marcarse como provisional.

### 16.2 Defectos muy raros

Los defectos muy raros pueden generar ratios muy altos con impactos muy bajos.

Por eso no debe usarse solo el ratio. Conviene exigir tambien:

```text
exceso_absoluto_minimo
volumen_minimo_inspeccionado
numero_minimo_rechazos
```

### 16.3 Cambios de receta o configuracion

Un cambio de receta puede alterar completamente la referencia historica.

Por tanto, cuando sea posible, la referencia deberia considerar:

```text
modelo + maquina + defecto + receta/version_configuracion
```

Si no se dispone de receta, al menos debe marcarse el posible cambio como evento
contextual.

### 16.4 Sincronizacion temporal

La comparacion entre maquinas, avisos, rechazos y otras fuentes depende de una
buena alineacion temporal.

Conviene revisar:

```text
zona horaria
retardos de escritura
buffers
ventanas incompletas
desfase entre ZC y ZF
orden real de paso de botellas
```

---

## 17. Ejemplos de textos automaticos

Estos textos estan pensados para explicar comportamientos especificos de maquina.

```text
La maquina ZF-2 detecta el defecto AI_ALGO_PLI 3,5 veces por encima de su
historico comparable para este modelo de botella.
```

```text
El defecto AI_ALGO_PLI se concentra principalmente en la maquina ZF-2, que
acumula el 68% de las detecciones del defecto durante la produccion.
```

```text
La maquina ZF-3 presenta una pendiente positiva de rechazo, mientras que la
mediana del resto de maquinas permanece estable. Patron compatible con deriva
local de inspeccion o condicion especifica del ramal.
```

```text
El arranque de la maquina ZF-1 fue inestable, pero el rechazo se estabilizo tras
los primeros 45 minutos. La lectura operativa es distinta a una deriva sostenida.
```

```text
La maquina ZF-4 genero aproximadamente 180 rechazos por encima de su referencia
esperada, concentrados principalmente en dos episodios de alta intensidad.
```

```text
Las maquinas muestran una subida sincronizada del defecto AI_ALGO_MINCE. El
patron es mas compatible con causa comun de proceso que con desajuste individual
de una maquina.
```

```text
La deteccion de la maquina ZF-2 aumenta, pero los avisos/rechazos registrados no
muestran una tendencia equivalente. Conviene revisar posible desacople entre
inspeccion automatica y registros operativos.
```

---

## 18. Resumen ejecutivo del anexo

Las metricas adicionales propuestas se centran en analizar si una maquina de
inspeccion se comporta de forma coherente respecto a:

```text
su propio historico
el historico del mismo modelo de botella
el historico del mismo defecto
el resto de maquinas de la misma produccion
la evolucion temporal de la produccion
los avisos y rechazos registrados
```

La recomendacion principal es trabajar con referencias jerarquicas:

```text
modelo
modelo + defecto
modelo + maquina
modelo + maquina + defecto
```

Y no basar el diagnostico en una unica metrica, sino en la combinacion de:

```text
sobredeteccion relativa
z robusto
exceso absoluto
pendiente temporal
pendiente relativa frente al resto
concentracion entre maquinas
correlacion con otras maquinas
area o exceso acumulado
coherencia con avisos/rechazos
```

El objetivo final no es decir automaticamente cual es la causa, sino clasificar el
patron observado como mas compatible con:

```text
comportamiento normal
sobredeteccion puntual
arranque inestable corregido
deriva progresiva de maquina
maquina desacoplada
causa comun entre maquinas
posible inconsistencia entre inspeccion y registros operativos
```

Esta capa aportaria una lectura mas profunda del sistema de inspeccion, ayudando
a diferenciar entre problemas reales de produccion, problemas localizados en una
maquina o ramal, y posibles comportamientos inestables del propio sistema de
deteccion.

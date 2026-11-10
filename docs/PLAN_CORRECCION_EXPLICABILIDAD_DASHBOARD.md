# Plan de correccion de explicabilidad y visualizacion del dashboard

## 1. Objetivo

El objetivo de este plan es corregir problemas de interpretacion, exceso de metricas agregadas y falta de claridad visual en las tres pestañas principales del dashboard:

- Historico de producciones.
- Riesgo de incidencias.
- Rendimiento ZC y gravedad de defectos.

El criterio principal pasa a ser:

> Cada grafico y cada indicador debe tener una lectura industrial directa. Si una metrica no se puede explicar con claridad a calidad o produccion, debe eliminarse, simplificarse o desagregarse.

Este plan no busca añadir mas complejidad, sino reducir ruido, aclarar formulas y dejar solo informacion accionable.

## 2. Principios de correccion

### 2.1. Explicabilidad antes que sofisticacion

No se mantendran graficos estadisticos o modelos que no aporten una lectura clara de proceso.

Se eliminaran:

- PCA de defectos.
- Clustering asociado al PCA.
- Modelo explicativo binomial por turnos.
- Cambios sostenidos de nivel.
- Episodios fuera de referencia.
- Diferencias de tasa proxy entre parejas de producciones.

Se conservaran solo los analisis que permitan responder preguntas industriales:

- Que produccion tuvo mas defectos.
- Que produccion fue mas estable.
- Que defectos dominaron.
- Que defectos son relevantes para calidad.
- Que turno o dia concentraron problemas.
- Que produccion tuvo peor rendimiento ZC.
- Que produccion acumulo mas defectos criticos/graves.

### 2.2. Codigos de defecto siempre traducidos

Los codigos de maquina de inspeccion, como `15`, `37`, `413`, etc., no deben aparecer solos en ningun punto visible de la aplicacion.

Formato obligatorio:

```text
413 - Claro
37 - Nombre del defecto
15 - Nombre del defecto
```

Si existe codigo antiguo, codigo nuevo y descripcion:

```text
413 - Claro
Codigo historico: 413
Codigo normalizado: <codigo nuevo si aplica>
```

La aplicacion debe mantener el codigo historico como clave tecnica porque las producciones son historicas, pero la interfaz debe mostrar siempre el nombre legible del defecto.

### 2.3. Separar valor medido, valor normalizado y score

Actualmente algunos indicadores mezclan lectura relativa, proxy de exposicion y score comparativo. Esto genera confusion.

Cada metrica debera etiquetarse como una de estas tres categorias:

- Valor observado: medido directamente en los datos.
- Valor normalizado: transformado para comparar producciones, por ejemplo por ventana temporal o por exposicion nominal.
- Score relativo: posicion relativa frente a las producciones seleccionadas o frente al historico.

La UI debe mostrar esta distincion de forma explicita.

Ejemplo:

```text
Rechazos registrados por ventana
Valor observado agregado en la ventana seleccionada. No representa botellas totales rechazadas, sino suma de eventos de rechazo registrados en las filas de defectos.
```

```text
Score de estabilidad
Score relativo 0-100 dentro de la seleccion actual. No es un limite absoluto de aceptacion.
```

## 3. Pestaña 1: Historico

### 3.1. Aclarar que significa el eje Y de las series temporales

Problema actual:

El eje Y muestra valores muy altos, por ejemplo 400, 500 o mas por hora, y se etiqueta como `rechazos registrados`. Esto puede interpretarse erroneamente como 500 botellas rechazadas fisicamente en una hora.

Correccion necesaria:

Revisar la definicion exacta usada actualmente en backend para construir la serie. La documentacion visible debe indicar si el valor es:

- suma de filas/eventos registrados;
- suma de conteos de defectos;
- suma por ramal;
- suma por defectos;
- agregacion por ventana de 10 min, 1 h, 4 h u 8 h;
- tasa proxy por exposicion nominal.

La etiqueta debe cambiarse a una formulacion no ambigua.

Opciones recomendadas:

```text
Eventos de defecto agregados por ventana
```

o, si realmente son conteos de rechazos:

```text
Rechazos registrados agregados por ventana
```

Debe añadirse una ayuda contextual visible:

```text
Cada punto agrega los registros de defectos de la ventana seleccionada. Si hay varios ramales y defectos, el valor es la suma de todos ellos. No debe leerse como numero de botellas fabricadas ni como tasa real si no existe denominador de botellas inspeccionadas.
```

### 3.2. Revisar y explicar boxplot y violin plot

Problema actual:

La etiqueta `Eventos por 1.000 botellas estimadas` no se entiende y mezcla el supuesto nominal de 100 botellas/min con el agregado por ventana.

Decision propuesta:

La lectura principal debe depender de la ventana seleccionada. No debe mostrarse por defecto una metrica proxy por 1.000 botellas si el usuario ha seleccionado 1 h, 4 h, 8 h o dia, porque induce a pensar que se esta viendo una tasa real comparable a una muestra de 1.000 botellas.

Etiquetas recomendadas segun ventana:

```text
10 min -> Eventos registrados por ventana de 10 min
1 h    -> Eventos registrados por hora
4 h    -> Eventos registrados por bloque de 4 h
8 h    -> Eventos registrados por turno de 8 h
Dia    -> Eventos registrados por dia
```

La metrica proxy nominal debe pasar a ser una opcion secundaria, no la etiqueta principal del eje Y.

Separar dos modos de visualizacion:

1. Modo observado:

```text
Eventos agregados por ventana
```

2. Modo proxy nominal:

```text
Eventos por 1.000 botellas nominales
```

Formula del modo proxy:

```text
eventos_por_1000 = eventos_ventana / botellas_nominales_ventana * 1000
```

Con:

```text
botellas_nominales_ventana = 100 botellas/min * minutos_ventana
```

Ejemplos:

- 10 min: 1.000 botellas nominales.
- 1 h: 6.000 botellas nominales.
- 4 h: 24.000 botellas nominales.
- 8 h: 48.000 botellas nominales.

La UI debe mostrar esta formula junto al selector o en una nota compacta bajo el grafico.

Si se mantiene el modo proxy como default, debe llamarse claramente `proxy nominal`, no tasa real.

Decision final recomendada:

- Default: eventos observados agregados segun la ventana seleccionada.
- Opcional: proxy nominal por 1.000 botellas, claramente etiquetado y con formula visible.
- El eje Y debe cambiar automaticamente con la ventana para evitar lecturas ambiguas.

### 3.3. Rehacer los seis indicadores de score global

Problema actual:

Los seis indicadores son demasiados, mezclan conceptos y no queda claro si son:

- valores absolutos;
- valores relativos a la seleccion;
- valores normalizados por exposicion nominal;
- scores comparativos.

Correccion:

Dividir la lectura en dos niveles.

#### Nivel 1: tarjetas observadas

Mostrar valores medidos directamente:

- Mediana de eventos por ventana.
- P95 de eventos por ventana.
- MAD o IQR como estabilidad.
- Tendencia inicial-final o pendiente robusta.
- Diferencia entre ramales.
- Porcentaje de eventos en defectos dominantes seleccionados.

Cada tarjeta debe incluir:

- nombre claro;
- valor;
- unidad;
- breve explicacion;
- si menor es mejor o mayor es mejor.

#### Nivel 2: score relativo opcional

El score global debe quedar como lectura secundaria.

Debe indicarse:

```text
Score relativo calculado solo frente a las producciones seleccionadas. No es un criterio absoluto de aceptacion.
```

Si se mantiene, debe poder desplegarse el desglose de formulas, pero no ocupar visualmente el panel principal.

### 3.4. Eliminar analisis no accionables

Eliminar de la pestaña historico:

- Modelo explicativo binomial por turnos.
- Cambios sostenidos de nivel.
- Episodios fuera de referencia.
- Diferencias de tasa proxy e intervalos entre parejas de producciones.

Motivo:

No aportan una lectura industrial suficientemente clara en esta fase y generan ruido interpretativo.

### 3.5. Pareto de defectos por produccion

Correcciones obligatorias:

- Mostrar siempre nombres de defecto, no solo codigos.
- Mantener codigo historico como prefijo.
- Ordenar por contribucion descendente.
- Separar paretos por produccion.
- Evitar paretos agregados salvo accion explicita del usuario.

Formato recomendado:

```text
413 - Claro
37 - <descripcion>
15 - <descripcion>
```

Tooltip:

```text
Codigo historico: 413
Descripcion: Claro
Contribucion: 28,4 %
Eventos: 12.430
```

### 3.6. Subpestaña de defectos dominantes y perfiles

Problema actual:

Hay demasiada informacion. PCA y clusters no son suficientemente explicables para proceso.

Correccion:

Eliminar:

- PCA.
- Cargas del PCA.
- Clusters.
- Secuencia temporal de clusters.
- Interpretacion de clusters.

Conservar:

- selector libre de defectos dominantes, mostrando codigo + nombre;
- grafico por produccion de:
  - eventos totales;
  - eventos de defectos seleccionados como dominantes;
  - eventos residuales.

Objetivo:

Responder de forma sencilla:

```text
Si separo estos defectos dominantes, como queda el resto de la produccion?
```

### 3.7. Segmento de botella y defectos caracteristicos

El modelo actual pertenece al segmento:

```text
Vino Ligero
```

Debe incorporarse una lectura especifica de los defectos caracteristicos del segmento.

Acciones:

- Cargar la tabla o configuracion de segmentos.
- Identificar los tres defectos caracteristicos de `Vino Ligero`.
- Mapearlos contra codigos historicos de la tabla de rechazos.
- Mostrar en historico una tarjeta o bloque:

```text
Defectos caracteristicos del segmento Vino Ligero
```

Para cada produccion:

- eventos totales de esos defectos;
- porcentaje sobre el total de defectos;
- ranking frente a otras producciones;
- evolucion temporal;
- contribucion por ramal si aplica.

Esto es prioritario porque conecta el analisis con criterios definidos por calidad.

## 4. Pestaña 2: Riesgo de incidencias

### 4.1. Revisar color de burbujas por diversidad

Problema actual:

La diversidad de defectos en escala 0-100 colorea muchas producciones con tonos similares. Visualmente no discrimina.

Decision:

No usar diversidad como color si no separa claramente las producciones.

Alternativas mejores:

#### Opcion A: color por concentracion interpretable

Usar una metrica directa:

```text
% contribucion Top 5 defectos
```

Lectura:

- alto: pocos defectos explican casi todo;
- bajo: el problema esta repartido entre muchos defectos.

Color podria ser:

- verde/azul: concentrado y atacable;
- naranja/rojo: disperso y menos claro.

#### Opcion B: color por severidad real

Usar:

```text
% de exposicion asociada a defectos Criticos + Graves
```

Esta opcion es mas tangible para calidad.

Lectura:

- rojo: mucha exposicion en defectos criticos/graves;
- verde: poca exposicion en defectos criticos/graves.

Esta opcion es preferible si la tabla de criticidades esta completa y el mapeo de defectos es fiable.

#### Opcion C: color por etiqueta cliente/reclamaciones

Cuando se incorpore la validacion contra reclamaciones:

- verde: sin reclamaciones relevantes;
- amarillo: observada;
- naranja: mala;
- rojo: critica.

Esta opcion es potente para validar si las metricas predicen bien la percepcion real de cliente.

### 4.2. Revisar tamaño de burbujas

Problema actual:

`Eventos mas persistentes` no explica si se usa media, mediana, maximo, P90 u otra metrica.

Decision:

El tamaño debe representar una metrica robusta y visible.

Opcion recomendada:

```text
P90 de duracion de eventos
```

Motivo:

- conecta directamente con los graficos de distribucion de duraciones;
- evita que un unico outlier determine el tamaño;
- representa persistencia operativa;
- es facil de explicar.

Texto obligatorio:

```text
Tamaño: P90 de duracion de incidencias. Una burbuja grande indica que el 10 % de eventos mas largos de esa produccion dura mucho mas que en otras producciones.
```

Para el mapa cliente:

```text
Tamaño: P90 de duracion de avisos. Una burbuja grande indica avisos que permanecen abiertos durante mas tiempo antes de cerrarse o pasar a rechazo.
```

### 4.3. Desagregar metricas en graficos de un solo termino por eje

Mantener el enfoque ya iniciado:

Cada grafico debe comparar pares de metricas simples, no ejes compuestos por varias contribuciones.

Ejemplos utiles:

- Exposicion total vs dispersion entre cavidades.
- Exposicion total vs Top 5 cavidades.
- Exposicion total vs 1-HHI.
- P90 duracion vs dispersion entre cavidades.
- P90 duracion vs frecuencia de incidencias.
- Frecuencia vs recurrencia cavidad-defecto.
- Avisos normalizados vs P90 avisos.
- Avisos normalizados vs defectos criticos/graves.

### 4.4. Top 5 cavidades en lugar de Top 3

Cambiar todas las referencias de `Top 3 cavidades` a `Top 5 cavidades`.

Motivo:

Top 5 deberia capturar mejor diferencias entre producciones cuando existen varias cavidades con contribuciones relevantes.

Nueva formula:

```text
Concentracion Top 5 = exposicion de las 5 cavidades mas afectadas / exposicion total
```

Lectura:

- Top 5 alto: problema concentrado en pocas cavidades.
- Top 5 bajo: problema repartido entre muchas cavidades.

### 4.5. HHI y dispersion

Mantener HHI como metrica estadistica complementaria, pero explicarla mejor.

Formula:

```text
HHI = sum(cuota_i^2)
```

Donde:

```text
cuota_i = exposicion de la cavidad i / exposicion total
```

Interpretacion:

- HHI alto: pocas cavidades concentran la exposicion.
- HHI bajo: la exposicion esta repartida entre muchas cavidades.

Para representar dispersion:

```text
dispersion = 1 - HHI_normalizado
```

Lectura:

- dispersion alta: problema repartido.
- dispersion baja: problema localizado.

### 4.6. Relacion con boxplots de historico

El tamaño de burbuja en riesgo debe conectar con la pestaña historico.

Recomendacion:

- Historico muestra distribucion de eventos por ventana.
- Riesgo muestra distribucion de duracion de incidencias.

Ambas son complementarias:

```text
Historico responde: cuanta intensidad de defectos aparece por ventana?
Riesgo responde: cuanto duran los eventos/incidencias que generan esa afectacion?
```

La UI debe incluir una explicacion compacta:

```text
Una produccion puede tener pocos eventos por ventana pero incidencias largas, o muchas ventanas con defectos moderados pero incidencias cortas. Por eso historico y riesgo no siempre ordenan igual las producciones.
```

## 5. Pestaña 3: Rendimiento ZC y gravedad

### 5.1. Persistencia de resultados al cambiar de pestaña

Problema actual:

Al cambiar de pestaña se pierden resultados calculados.

Requisito:

Cada pestaña debe conservar su ultima consulta y resultado hasta que:

- el usuario pulse un boton `Reset`;
- se recargue manualmente la aplicacion;
- el usuario lance una nueva consulta.

Implementacion prevista:

- Mantener estado por pestaña en el nivel superior de la app.
- Evitar desmontar completamente paneles con estado local critico.
- Añadir boton visible `Resetear resultados` por pestaña.

### 5.2. Leyenda del rendimiento ZC por turno

Problema actual:

La leyenda pisa el eje X.

Correccion:

- Mover la leyenda fuera del area de datos.
- Ubicarla debajo del grafico con margen suficiente o a la derecha si hay espacio.
- Si hay muchas producciones, usar scroll horizontal o leyenda compacta multi-columna.
- Mantener colores consistentes por produccion en toda la aplicacion.

### 5.3. Mapa de colores consistente en mapas desagregados

Problema actual:

Los mapas desagregados de rendimiento ZC frente a gravedad usan escalas visuales que pueden parecer distintas entre graficos.

Requisito:

Todos los mapas deben compartir:

- misma paleta;
- mismo minimo;
- mismo maximo;
- misma interpretacion de color;
- misma leyenda textual.

Si color representa horas activas de defectos:

```text
Color: horas activas de defectos de esa gravedad. Misma escala para todos los graficos.
```

Si el rango real es muy estrecho, valorar:

- usar cuantiles globales;
- escala logaritmica;
- bins discretos;
- o cambiar la variable de color.

### 5.4. Replantear color y tamaño en mapas ZC vs gravedad

Problema:

Si color y tamaño representan casi lo mismo, o si no diferencian visualmente, pierden utilidad.

Propuesta:

- Eje Y: rendimiento ZC medio.
- Eje X: porcentaje de rechazos de una gravedad concreta.
- Tamaño: P90 de duracion de eventos de esa gravedad.
- Color: exposicion total o horas activas de esa gravedad, con escala global compartida.

Para los defectos criticos/graves:

- priorizar mapas especificos:
  - Rendimiento ZC vs % Critico.
  - Rendimiento ZC vs % Grave.
  - Rendimiento ZC vs % Critico + Grave.
  - Rendimiento ZC vs defectos caracteristicos del segmento.

### 5.5. Defectos caracteristicos de Vino Ligero

Este es el punto mas relevante de la pestaña 3.

Debe añadirse un bloque especifico:

```text
Defectos caracteristicos del segmento Vino Ligero
```

Para cada produccion:

- porcentaje de rechazos asociados a los 3 defectos caracteristicos;
- numero absoluto de eventos;
- duracion total o exposicion si aplica;
- P90 de duracion si existe en avisos/rechazos;
- evolucion por turno o dia;
- relacion visual con rendimiento ZC.

Graficos propuestos:

1. Rendimiento ZC medio vs % defectos caracteristicos.
2. Rendimiento ZC por turno con marcas cuando aparecen defectos caracteristicos.
3. Barras apiladas por produccion:
   - defectos caracteristicos;
   - resto de criticos/graves;
   - resto de defectos.
4. Tabla compacta:

```text
Produccion | Rendimiento ZC medio | % caracteristicos | Defecto caracteristico dominante | Criticidad | Turno peor
```

### 5.6. Limites de aparicion de defectos definidos por calidad

La tabla de limites aportada por calidad debe incorporarse como criterio explicativo en la pestaña de rendimiento ZC y gravedad.

La tabla distingue dos niveles de lectura:

- limite global: aplicado al conjunto de la produccion;
- limite individual: aplicado a una cavidad concreta.

Los limites globales sirven para responder:

```text
La produccion completa supera el porcentaje maximo aceptable de aparicion de esta criticidad?
```

Los limites individuales sirven para responder:

```text
Existe alguna cavidad concreta que supera el porcentaje maximo aceptable de aparicion de esta criticidad?
```

#### 5.6.1. Semaforo junto al grafico de distribucion por gravedad

Debajo o junto al grafico `Distribucion por gravedad`, debe añadirse un bloque de semaforo por produccion.

Formato recomendado:

```text
Produccion | Criticos | Graves | Mayores | Menores | Leves | Lectura
```

Estados:

- verde: dentro de limite;
- amarillo: cerca del limite o requiere vigilancia;
- rojo: limite superado;
- gris: no evaluable por falta de mapeo, falta de denominador o criticidad sin limite numerico.

Para criticidades con limite numerico, la regla sera:

```text
porcentaje_observado_global > limite_global -> rojo
porcentaje_observado_individual_cavidad > limite_individual -> rojo
```

La UI debe separar claramente si el incumplimiento viene de:

- exceso global de la produccion;
- exceso localizado en una cavidad;
- ambos.

Ejemplo de lectura:

```text
AV32_PROD_004 - Graves: rojo por cavidad 5F. La produccion global esta dentro de limite, pero una cavidad individual supera el umbral.
```

#### 5.6.2. Criterio especial para defectos criticos

Para defectos criticos, el criterio no debe ser solo numerico.

La tabla indica que el analisis debe centrarse en la repeticion:

- defecto critico repetitivo en la produccion;
- defecto critico repetitivo dentro de una misma cavidad.

Reglas iniciales propuestas:

```text
Critico observado una sola vez y sin repeticion por cavidad -> amarillo
Critico repetido en la produccion -> rojo
Critico repetido en la misma cavidad -> rojo prioritario
```

La lectura debe mostrar:

- codigo y nombre del defecto critico;
- numero de apariciones;
- cavidades afectadas;
- si hay repeticion en la misma cavidad;
- turnos o dias donde aparece.

Ejemplo:

```text
Critico repetitivo: 413 - Claro. 8 apariciones, 5 en cavidad 11D. Requiere revision localizada.
```

#### 5.6.3. Relacion con rendimiento ZC

El semaforo no debe sustituir al grafico de rendimiento ZC. Debe complementarlo.

Lectura conjunta esperada:

- rendimiento ZC bajo + semaforo rojo: posible degradacion real de proceso;
- rendimiento ZC alto + semaforo rojo: produccion eficiente, pero con riesgo de calidad;
- rendimiento ZC bajo + semaforo verde: perdida de rendimiento posiblemente no explicada por defectos criticos/graves;
- rendimiento ZC alto + semaforo verde: escenario favorable.

#### 5.6.4. Integracion con graficos existentes

El bloque de limites debe integrarse en este orden:

1. Grafico de distribucion por gravedad.
2. Semaforo de limites globales e individuales.
3. Mapas ZC vs gravedad.
4. Periodos principales de criticos/graves.

El objetivo es que calidad pueda pasar de una lectura general a una causa concreta:

```text
Distribucion por gravedad -> limite superado -> cavidad/defecto responsable -> periodo temporal.
```

### 5.7. Eliminar tablas ilegibles

Eliminar o rediseñar tablas de mucho detalle que no aporten lectura rapida, por ejemplo:

- Top 5 por produccion y gravedad si se vuelve ilegible.

Reemplazo recomendado:

- tarjetas compactas por produccion;
- tabla resumen con maximo 1-3 filas por produccion;
- filtros bajo demanda para detalle.

## 6. Persistencia de estado entre pestañas

Este requisito aplica a toda la aplicacion.

Cada pestaña debe conservar:

- filtros seleccionados;
- producciones seleccionadas;
- resultado de ultima consulta;
- estado de subpestañas internas;
- configuracion de visualizacion.

No debe conservarse:

- datos obsoletos si el usuario lanza una nueva consulta;
- resultados incompatibles si cambia la version de parametros de forma critica.

Diseño recomendado:

- Estado global ligero en React para cada panel.
- Cache local por clave de consulta.
- Boton `Reset` por pestaña.
- Indicador de `Ultima consulta` con timestamp y numero de producciones.

## 7. Traduccion global de defectos

### 7.1. Fuente de verdad

Debe existir un unico servicio/mapeador de defectos.

La fuente principal para traducir los codigos historicos de maquina de inspeccion sera:

```text
C:\Users\iker.villen\Desktop\AI_tool\Datos\COD_DEF.xlsx
```

Este archivo contiene la equivalencia entre codigos como `15`, `37`, `413`, etc. y el nombre real del defecto. Debe integrarse explicitamente con:

- los defectos de `AV-2024-2026.xlsx`;
- los Parquet generados desde `AV-2024-2026.xlsx`;
- los informes `Informe Avisos Rechazos...`;
- los Parquet de `avisos_rechazos_parquet`;
- cualquier tabla posterior de criticidades o segmentos.

Regla de integracion:

```text
codigo_defecto_historico -> COD_DEF.xlsx -> descripcion_defecto
```

El codigo historico sigue siendo la clave tecnica para unir datos antiguos, pero la descripcion procedente de `COD_DEF.xlsx` debe ser la etiqueta visible en toda la aplicacion.

Debe resolver:

- codigo historico;
- descripcion historica;
- codigo nuevo si existe;
- criticidad;
- categoria o familia;
- indicador de si pertenece a defectos caracteristicos del segmento.

### 7.2. Uso obligatorio

El mapeador debe usarse en:

- selectores de defectos dominantes;
- paretos;
- tooltips;
- tablas;
- mapas de gravedad;
- periodos principales;
- periodos principales de defectos criticos/graves en rendimiento ZC;
- validacion contra reclamaciones;
- cualquier etiqueta visible.

Caso obligatorio:

En la parte inferior de la pestaña de rendimiento ZC, en `periodos principales de defectos criticos/graves`, no deben aparecer codigos numericos aislados. Cada fila debe mostrar el nombre resuelto desde `COD_DEF.xlsx`.

Formato:

```text
413 - Claro
```

Si un codigo procedente de avisos/rechazos o de `AV-2024-2026` no aparece resuelto, no se debe aceptar como comportamiento final. Hay que extender la equivalencia entre:

- codigos de maquina de deteccion;
- codigos historicos de defectos en `AV-2024-2026`;
- codigos presentes en `Informe Avisos Rechazos...`;
- tabla maestra `COD_DEF.xlsx`;
- tabla de criticidades.

El objetivo es que el usuario de calidad no tenga que interpretar codigos tecnicos.

### 7.3. Fallback profesional

Si no hay descripcion:

```text
413 - Sin descripcion en tabla maestra
```

Nunca mostrar solo:

```text
Defecto 413
```

## 8. Entregables propuestos

Para evitar un cambio enorme y dificil de revisar, se propone implementar en pequeños entregables.

### Entregable 1: Limpieza de historico

- Corregir etiquetas de ejes.
- Añadir formulas y ayudas visibles.
- Eliminar modelo binomial, cambios sostenidos, episodios fuera de referencia y diferencias proxy.
- Mantener solo graficos utiles.

Validacion:

- `npm run build`
- tests frontend existentes
- verificacion visual con AV23 y AV32.

### Entregable 2: Mapeo global de defectos

- Centralizar el mapeador codigo-nombre.
- Aplicarlo a paretos, selectores y tablas.
- Revisar todos los puntos donde aparece `Defecto X`.

Validacion:

- test unitario del mapeador;
- comprobacion visual en historico, riesgo y ZC.

### Entregable 3: Simplificacion de defectos dominantes

- Eliminar PCA y clusters.
- Mantener solo total/dominantes/residual.
- Selector libre de defectos con nombres.

Validacion:

- seleccionar varios defectos;
- comprobar que residual se recalcula correctamente.

### Entregable 4: Riesgo de incidencias mas interpretable

- Replantear color y tamaño de burbujas.
- Usar P90 de duracion como tamaño.
- Cambiar Top 3 a Top 5.
- Mejorar explicacion de HHI y dispersion.
- Revisar mapas desagregados para que cada eje sea un unico termino.

Validacion:

- comparar 4, 8 y todas las producciones;
- comprobar que los graficos no pisan etiquetas;
- comprobar que se entienden los ejes sin leer codigo.

### Entregable 5: Persistencia de resultados entre pestañas

- Mantener resultados al cambiar de pestaña.
- Añadir boton reset.
- Mantener filtros seleccionados.

Validacion:

- calcular en Historico;
- cambiar a Riesgo;
- volver a Historico;
- comprobar que el resultado sigue visible.

### Entregable 6: Rendimiento ZC y defectos caracteristicos de segmento

- Arreglar leyenda del rendimiento ZC.
- Unificar mapa de colores.
- Añadir bloque de defectos caracteristicos de Vino Ligero.
- Relacionar rendimiento ZC con % de defectos caracteristicos.
- Añadir semaforo de limites globales e individuales por criticidad.
- Aplicar criterio especial de repeticion para defectos criticos.
- Traducir todos los defectos de periodos principales usando `COD_DEF.xlsx`.

Validacion:

- seleccionar AV32 completa;
- comprobar que todos los mapas comparten escala;
- comprobar que se visualizan los 3 defectos caracteristicos.
- comprobar que cada produccion muestra estado verde/amarillo/rojo/gris por criticidad.
- comprobar que un exceso global y un exceso individual por cavidad se distinguen visualmente.
- comprobar que no aparecen codigos numericos aislados en periodos criticos/graves.

## 9. Criterios de aceptacion

La correccion se considerara aceptable si:

- Ningun grafico visible muestra codigos de defecto sin descripcion.
- Cada eje tiene unidad, formula o explicacion compacta.
- Los scores relativos se identifican explicitamente como relativos.
- Las metricas observadas no se mezclan con scores sin indicarlo.
- Los resultados no se pierden al cambiar de pestaña.
- Los graficos mantienen colores consistentes por produccion.
- Las leyendas no pisan datos ni ejes.
- Los graficos son legibles al seleccionar muchas producciones.
- Los defectos caracteristicos de Vino Ligero aparecen de forma explicita.
- La distribucion por gravedad se acompaña de un semaforo contra limites de calidad.
- Los limites globales y los limites individuales por cavidad se evalúan por separado.
- Los defectos criticos se evalúan por repeticion global y repeticion en cavidad, no solo por porcentaje.
- Se eliminan los analisis no accionables definidos en este plan.

## 10. Riesgos y decisiones pendientes

### 10.1. Definicion exacta de rechazos registrados

Antes de renombrar ejes definitivamente, hay que verificar en backend si los valores son:

- conteos de defectos;
- conteos de filas;
- suma por ramal;
- suma por cavidad;
- suma por defecto;
- proxy normalizado.

Esta verificacion condiciona la etiqueta final.

### 10.2. Completeness del mapeo de defectos

Hay que medir:

- porcentaje de codigos historicos con descripcion;
- porcentaje con criticidad;
- porcentaje con codigo nuevo;
- porcentaje que encaja en defectos caracteristicos de segmento.

Si hay huecos, deben mostrarse como datos sin clasificar, no ocultarse.

### 10.3. Segmento y defectos caracteristicos

Hay que confirmar formalmente:

- que el modelo pertenece a `Vino Ligero`;
- cuales son los tres defectos caracteristicos;
- que codigos historicos corresponden a cada defecto caracteristico.

### 10.4. Scores globales

El score global debe tratarse como experimental hasta validarlo contra reclamaciones reales.

No debe presentarse como criterio definitivo de calidad.

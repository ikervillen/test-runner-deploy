# Plan de Integracion de Salud Serie, Segmentos y Defectos Especificos

## 1. Objetivo

El objetivo de este plan es definir como incorporar al visualizador la informacion
del documento de calidad `Salud Serie limites.pptx`, especialmente la parte de
segmentos de botella, limites provisionales y defectos especificos por segmento.

El caso inicial de uso sera el modelo de botella actualmente analizado en la
aplicacion, que pertenece al segmento **Vino Ligero**.

La finalidad no es crear un score opaco adicional, sino disponer de una lectura
industrial clara:

- que defectos especificos del segmento aparecen en cada produccion;
- con que peso aparecen frente al total de rechazos;
- en que cavidades, turnos o dias se concentran;
- si se acercan o superan limites provisionales definidos por calidad;
- que reclamaciones de cliente se han recibido para cada produccion;
- que metricas internas explican mejor las producciones reclamadas;
- que producciones deberian revisarse prioritariamente desde el punto de vista
  de salud de serie.

## 2. Fuentes de Datos Revisadas

### 2.1 PowerPoint de calidad

Archivo:

```text
C:\Users\iker.villen\Desktop\AI_tool\Datos\Salud Serie limites.pptx
```

El archivo contiene una diapositiva densa con informacion de salud de serie.
En ella se identifican segmentos de botella, limites globales e individuales,
defectos especificos por segmento y referencias a rechazos de proceso.

### 2.2 Catalogo de defectos

Archivo:

```text
C:\Users\iker.villen\Desktop\AI_tool\Datos\COD_DEF.xlsx
```

Contiene la relacion base:

- `COD_DEF`
- `DESCRIPTION`

Ejemplos relevantes encontrados:

| COD_DEF | DESCRIPTION |
|---:|---|
| 12 | Ovalizzazione |
| 14 | Line Over |
| 40 | Rechupe |
| 50 | Verticalita |
| 104 | Verticalidad |
| 114 | Ovalizacion |
| 117 | Verticalidad/Cuello Torcido |
| 532 | Ovality |
| 1095 | Line over finish |
| 1107 | Ovality body |
| 1108 | Ovality neck |

### 2.3 Tabla de criticidades

Archivo:

```text
C:\Users\iker.villen\Desktop\AI_tool\Datos\Tabla defectos con criticidades.xlsx
```

Contiene, entre otras columnas:

- codigo antiguo;
- descripcion antigua;
- codigo nuevo;
- descripcion nueva;
- gravedad generica.

La aplicacion actual usa esta tabla para mapear defectos a criticidad. Para los
datos de `AV-2024-2026`, la logica correcta es priorizar el **codigo nuevo**,
dejando el codigo antiguo como fallback para fuentes futuras que todavia puedan
trabajar con identificadores antiguos.

Ejemplos relevantes encontrados:

| Codigo antiguo | Descripcion antigua | Codigo nuevo | Descripcion nueva | Gravedad |
|---:|---|---:|---|---|
| 110 | Line-over en superficie cierre | 27 | Line-over en superficie cierre | Grave |
| 212 | Verticalidad | 414 | Verticalidad | Mayor |
| 302 | Perfil interno boca / Rechupe | 115 | Rechupe | Menor |
| 307 | Boca ovalada | 53 | Boca ovalada | Menor |
| 315 | Botella ovalada | 221 | Botella ovalada | Menor |

### 2.4 Reclamaciones por produccion

La informacion de reclamaciones aporta una capa externa de validacion. No mide
directamente el proceso, sino la consecuencia observada o comunicada desde
cliente/calidad una vez la produccion ya ha salido del entorno industrial.

Esta fuente debe utilizarse para etiquetar producciones y contrastar si los
indicadores internos de la aplicacion explican bien las producciones buenas,
observadas, malas o criticas.

Reglas iniciales:

- una reclamacion por `Etiqueta equivocada` debe excluirse del diagnostico de
  calidad de fabricacion del envase, porque pertenece a embalaje,
  identificacion o gestion logistica;
- una reclamacion `Cumple especificacion / indeterminado` no debe excluirse:
  debe entrar como **Observada**, porque no confirma fabricacion incorrecta
  frente al acuerdo actual, pero si puede indicar una oportunidad de revision
  de especificaciones, criterio de cliente o sensibilidad futura;
- reclamaciones por fisura, rotura, impacto, infundido, espesor fuera de
  norma, acuerdo-talon u otros defectos de producto deben tratarse como
  reclamaciones relevantes de fabricacion.

## 3. Segmentos Identificados

El PowerPoint propone siete segmentos:

1. Aceite.
2. Cerveza.
3. Alimentacion y conservas.
4. Licores.
5. Vino Ligero.
6. Vino premium.
7. Espumosos y cavas.

El segmento de interes inicial para el modelo actual es **Vino Ligero**.

## 4. Segmento Vino Ligero

Para `Vino Ligero`, el PowerPoint recoge estas caracteristicas especificas:

| Caracteristica | Origen dato | Limite proceso | Limite laboratorio | Limite linea |
|---|---|---:|---:|---|
| Ovalizacion | AV&RECH | 8% | 25% maximo | Cpk > 0,8 |
| Verticalidad | AV&RECH | 8% | 15% maximo | Cpk > 0,9 |
| Corcho: Rechupe int. 45 mm | AV&RECH | 8% | 25% maximo | Cp > 0,9 y Cpk > 0,7 |
| Rosca: Line over | AV&RECH | 8% | pendiente / no indicado | pendiente / no indicado |

Aunque se habla de tres caracteristicas principales por segmento, para Vino
Ligero parece necesario tratar la tercera caracteristica como dependiente del
tipo de cierre:

- botella corcho: `Rechupe`;
- botella rosca: `Line over`.

Este punto debe validarse con calidad antes de fijar reglas definitivas.

## 5. Problema Principal: Equivalencia Semantica de Defectos

El punto critico no es tecnico, sino semantico.

El PowerPoint habla de conceptos de calidad:

- Ovalizacion.
- Verticalidad.
- Rechupe.
- Line over.

Las tablas disponibles contienen codigos y descripciones que no siempre son
literalmente iguales. Por ejemplo, `Ovalizacion` puede aparecer como:

- `Boca ovalada`;
- `Botella ovalada`;
- `Ovality`;
- `Ovality body`;
- `Ovality neck`;
- `Ovalizzazione`.

Por tanto, no conviene automatizar el cruce solo por coincidencia textual.
La aplicacion puede sugerir candidatos, pero la equivalencia final debe quedar
recogida en una tabla explicita y validada.

## 6. Diccionario de Segmento Propuesto

Se propone crear un catalogo mantenible de defectos especificos por segmento.

Para `Vino Ligero`, una primera version podria ser:

| Segmento | Caracteristica | Familia | Codigos candidatos |
|---|---|---|---|
| Vino Ligero | Ovalizacion | Forma / cuerpo / boca | 53, 221, 114, 532, 1107, 1108 |
| Vino Ligero | Verticalidad | Geometria | 414, 104, 117 |
| Vino Ligero | Rechupe | Boca / corcho | 115, 40 |
| Vino Ligero | Line over | Boca / rosca | 27, 14, 1095 |

Esta tabla no debe considerarse definitiva. Debe revisarse con calidad para
decidir:

- que codigos forman realmente parte de cada caracteristica;
- que codigos deben excluirse aunque el texto sea parecido;
- si se debe distinguir entre codigo antiguo, codigo nuevo y `COD_DEF`;
- si una caracteristica puede estar compuesta por varios defectos equivalentes.

## 7. Enfoque de Analisis Propuesto

### 7.1 Analisis por produccion

Para cada produccion seleccionada:

- total de rechazos registrados;
- total de rechazos asociados a defectos especificos del segmento;
- porcentaje de defectos especificos sobre el total de la produccion;
- desglose por caracteristica:
  - Ovalizacion;
  - Verticalidad;
  - Rechupe;
  - Line over;
- peor cavidad por caracteristica;
- contribucion del Top 5 de cavidades para cada caracteristica;
- evolucion temporal por ventana.

### 7.2 Analisis temporal

Los defectos especificos deben poder analizarse por:

- 10 minutos;
- 1 hora;
- turno de 8 horas;
- dia;
- produccion completa.

La ventana de turno es especialmente importante para responder preguntas de
planta:

- en que turno empeora una caracteristica especifica;
- si el defecto aparece de forma sostenida o en picos;
- si una intervencion parece corregir el problema;
- si el problema se desplaza entre cavidades o permanece localizado.

### 7.3 Analisis por cavidad

Para cada caracteristica especifica:

- contribucion por cavidad;
- Top 5 cavidades;
- concentracion del problema;
- comparacion con limite individual, si aplica;
- persistencia temporal de las cavidades dominantes.

Esto permite diferenciar:

- problema localizado de molde/cavidad;
- problema general de maquina;
- problema intermitente o de ajuste;
- posible ruido de deteccion.

### 7.4 Analisis por gravedad

La criticidad debe mantenerse visible, pero sin sustituir al analisis por
segmento.

Ejemplo:

- `Line-over en superficie cierre` aparece como `Grave`;
- `Verticalidad` aparece como `Mayor`;
- `Rechupe`, `Boca ovalada` y `Botella ovalada` aparecen como `Menor`.

Esto implica que dos producciones con el mismo numero de defectos especificos
pueden no tener el mismo riesgo industrial si la composicion de gravedad cambia.

## 8. Limites Globales e Individuales

El PowerPoint incluye limites de aparicion de defectos a nivel:

- global de produccion;
- individual de cavidad.

Tambien distingue severidades:

- criticos;
- graves;
- mayores;
- menores;
- leves.

Estos limites deben tratarse como **provisionales** hasta que calidad confirme:

- definicion exacta del denominador;
- si el porcentaje se calcula sobre botellas, rechazos, eventos o cavidades;
- si aplica por produccion completa, turno, dia o lote;
- si aplica por defecto individual o por familia de defectos;
- si los limites son iguales para todos los segmentos.

La aplicacion deberia mostrar estos limites como referencia, pero indicando
claramente que son umbrales pendientes de validacion.

## 9. Indicadores Recomendados

### 9.1 Indicadores por caracteristica especifica

Para cada caracteristica del segmento:

```text
Peso caracteristica =
rechazos de la caracteristica / rechazos totales de la produccion
```

```text
Peso relativo dentro del segmento =
rechazos de la caracteristica / rechazos especificos del segmento
```

```text
Concentracion Top 5 cavidades =
rechazos de las 5 cavidades mas afectadas / rechazos de la caracteristica
```

### 9.2 Indicadores frente a limite

Si se confirma que el limite de proceso usa porcentaje sobre rechazos:

```text
Ratio frente a limite =
porcentaje observado / limite de proceso
```

Lectura:

- menor que 1: dentro del limite;
- igual a 1: en limite;
- mayor que 1: supera limite.

Si se confirma que el limite usa botellas fabricadas, sera necesario incorporar
un denominador de produccion real o estimado.

### 9.3 Indicadores por cavidad

```text
Peso cavidad =
rechazos de la cavidad para esa caracteristica / rechazos de la caracteristica
```

```text
Peor cavidad frente a limite individual =
porcentaje observado en peor cavidad / limite individual
```

Esto permitiria detectar si una produccion no es mala globalmente, pero si
tiene una cavidad concreta con riesgo alto.

## 10. Calidad Cliente y Reclamaciones

### 10.1 Principio de uso

Las reclamaciones no deben mezclarse de forma prematura dentro de los scores
internos de proceso. Deben actuar como una **variable objetivo externa** contra
la que validar las metricas internas.

Separacion conceptual:

| Capa | Que mide | Uso principal |
|---|---|---|
| Metricas internas | Como se comporto la produccion | Diagnostico tecnico |
| Salud Serie | Si aparecieron defectos relevantes para el segmento | Diagnostico de producto/segmento |
| Reclamaciones | Que llego o fue percibido por cliente/calidad | Validacion externa |

Esta separacion evita circularidad: no se debe decir que una produccion es mala
porque el score contiene la reclamacion y despues usar esa misma reclamacion
para validar el score.

### 10.2 Etiqueta de calidad cliente

Cada produccion debe recibir una etiqueta clara, calculada a partir de
reclamaciones relevantes.

Propuesta inicial:

| Etiqueta | Regla | Lectura |
|---|---|---|
| Buena | 0 reclamaciones relevantes de producto | Sin evidencia externa negativa |
| Observada | 1 reclamacion menor o `Cumple especificacion / indeterminado` | Revisar, pero no implica mala fabricacion confirmada |
| Mala | 1 reclamacion grave/mayor confirmada o 2 reclamaciones relevantes | Evidencia externa de problema de producto |
| Critica | 3 o mas reclamaciones relevantes, repeticion de defecto grave, o varios defectos de producto en la misma produccion | Produccion prioritaria para analisis |
| Excluida de fabricacion | Reclamacion de etiqueta, embalaje, identificacion o causa no atribuible al envase | No penaliza calidad de fabricacion |

La etiqueta debe conservar tambien el detalle que la justifica:

- numero de reclamaciones relevantes;
- numero de reclamaciones observadas;
- numero de reclamaciones excluidas de fabricacion;
- peor severidad reclamada;
- motivo principal;
- lista de identificadores de reclamacion;
- cliente o grupo cliente, si esta disponible.

### 10.3 Reglas de clasificacion por tipo de reclamacion

Las reclamaciones deben clasificarse antes de agregarse.

| Tipo | Ejemplos | Tratamiento |
|---|---|---|
| Producto confirmado | fisura, rotura, impacto, infundido, espesor fuera de norma, acuerdo-talon | Cuenta como reclamacion relevante |
| Producto observado | cumple especificacion / indeterminado | Cuenta como Observada |
| Embalaje / identificacion | etiqueta equivocada | Se excluye del diagnostico de fabricacion |
| No confirmada tecnica | reclamacion sin defecto verificable ni criterio claro | Mantener como observacion separada hasta validacion |

Este paso debe ser auditable. La aplicacion debe permitir ver por que una
reclamacion ha sido incluida, excluida o marcada como observada.

### 10.4 Coloreado transversal por calidad cliente

Todas las visualizaciones comparativas de producciones deberian poder colorear
las producciones por etiqueta de calidad cliente.

Propuesta de colores:

| Etiqueta | Color | Uso visual |
|---|---|---|
| Buena | Verde | Produccion sin reclamaciones relevantes |
| Observada | Amarillo | Produccion bajo revision |
| Mala | Naranja | Produccion con reclamacion relevante |
| Critica | Rojo | Produccion claramente problematica |
| Excluida / no imputable | Gris | Incidencia no atribuible a fabricacion |

Este coloreado debe estar disponible en:

- mapas de burbujas;
- rankings;
- matrices comparativas;
- graficos de rendimiento ZC;
- analisis de criticidad;
- analisis de Salud Serie;
- tablas resumen.

El objetivo es comprobar visualmente si los puntos naranjas y rojos caen en las
zonas esperadas: peor rendimiento, mas defectos graves, mayor duracion de
incidencias, mas defectos especificos de segmento o mayor dispersion.

### 10.5 Pestana de Validacion Contra Reclamaciones

Se propone crear una pestana especifica: **Validacion contra reclamaciones**.

Esta pestana debe responder a una pregunta concreta:

```text
Que metricas internas separan mejor las producciones con reclamaciones
relevantes de las producciones sin reclamaciones relevantes?
```

Contenido recomendado:

1. Tabla maestra de producciones.
2. Etiqueta de calidad cliente.
3. Motivos de reclamacion incluidos/excluidos.
4. KPIs internos principales por produccion.
5. Graficos metrica interna vs etiqueta cliente.
6. Deteccion de falsos positivos y falsos negativos.
7. Ranking de metricas con mayor capacidad explicativa.

La tabla maestra deberia incluir, como minimo:

| Campo | Descripcion |
|---|---|
| Produccion | Identificador de la app |
| Linea | AV23 o AV32 |
| Fechas | Rango de produccion |
| Reclamaciones relevantes | Numero de reclamaciones de producto |
| Reclamaciones observadas | Reclamaciones tipo especificacion/indeterminado |
| Reclamaciones excluidas | Etiqueta, embalaje u otros no imputables a fabricacion |
| Peor motivo | Motivo de mayor severidad |
| Calidad cliente | Buena, Observada, Mala o Critica |
| Metricas internas | Rendimiento, criticidad, riesgo, Salud Serie, etc. |

### 10.6 Falsos positivos y falsos negativos

La pestana debe identificar casos discordantes entre metricas internas y
resultado externo.

| Caso | Definicion | Interpretacion |
|---|---|---|
| Alineado bueno | Metricas internas favorables y sin reclamaciones relevantes | Produccion consistente |
| Alineado malo | Metricas internas desfavorables y reclamacion relevante | Indicadores utiles |
| Falso positivo | Metricas internas desfavorables, pero sin reclamacion relevante | Posible buena contencion, inspeccion eficaz o metrica demasiado sensible |
| Falso negativo | Metricas internas favorables, pero reclamacion relevante | Riesgo serio: el indicador no capturo el problema |
| Observada | Reclamacion `Cumple especificacion / indeterminado` | Revisar criterio, especificacion o sensibilidad cliente |

Este analisis es clave para depurar los KPIs. Una metrica no es buena por
parecer sofisticada, sino porque ayuda a explicar o anticipar diferencias reales
en calidad cliente.

### 10.7 Metricas candidatas para validar contra reclamaciones

Las primeras metricas a contrastar deberian ser desglosadas, no mezcladas en
scores compuestos:

- rendimiento ZC medio;
- rendimiento ZC minimo por turno;
- porcentaje de defectos criticos;
- porcentaje de defectos graves;
- porcentaje de defectos mayores;
- peso de defectos especificos de Vino Ligero;
- peso de Ovalizacion;
- peso de Verticalidad;
- peso de Rechupe o Line over;
- exposicion total de incidencias;
- P90 de duracion de incidencias;
- duracion maxima de incidencia;
- dispersion entre cavidades;
- concentracion Top 5 cavidades;
- diversidad de defectos;
- frecuencia de incidencias;
- recurrencia cavidad-defecto.

Cada grafico debe usar ejes con una sola metrica por eje cuando el objetivo sea
validacion analitica. Los scores compuestos pueden mantenerse como resumen, pero
no deben ocultar que variable concreta explica la reclamacion.

### 10.8 Estado inicial esperado para las 17 producciones AV

Con la informacion visible actualmente, la clasificacion inicial seria:

| Produccion | Tratamiento recomendado |
|---|---|
| AV32_PROD_005 | Mala/Critica por varias reclamaciones relevantes de producto |
| AV32_PROD_008 | Observada grave o Mala leve por rotura/impacto ZCI |
| AV32_PROD_006 | Observada por `Cumple especificacion / indeterminado` |
| AV23_PROD_002 | Buena para fabricacion; reclamacion de etiqueta excluida |
| Resto sin reclamaciones | Buena, salvo evidencia interna posterior |

Esta tabla debe revisarse cuando se disponga de la fuente estructurada completa
de reclamaciones, no solo de la captura.

## 11. Visualizacion Propuesta

### 11.1 Panel principal: Salud Serie - Vino Ligero

Un bloque especifico dentro del dashboard podria mostrar:

- segmento activo;
- caracteristicas especificas aplicables;
- tarjetas por caracteristica;
- estado frente a limite;
- peor produccion;
- peor cavidad;
- gravedad asociada;
- etiqueta de calidad cliente de la produccion, si existe.

Ejemplo de tarjetas:

| Caracteristica | Valor observado | Limite | Estado | Peor cavidad | Gravedad | Calidad cliente |
|---|---:|---:|---|---|---|---|
| Ovalizacion | 5,2% | 8% | Dentro | 5D | Menor | Buena |
| Verticalidad | 9,1% | 8% | Supera | 11D | Mayor | Observada |
| Rechupe | 2,3% | 8% | Dentro | 4F | Menor | Buena |

### 11.2 Tabla comparativa por produccion

Filas:

- producciones seleccionadas.

Columnas:

- total rechazos;
- rechazos especificos segmento;
- Ovalizacion;
- Verticalidad;
- Rechupe;
- Line over;
- peor cavidad;
- estado frente a limite;
- reclamaciones relevantes;
- calidad cliente;
- comentario operativo.

### 11.3 Graficos recomendados

1. Barras por produccion y caracteristica.
2. Heatmap produccion x caracteristica.
3. Heatmap cavidad x caracteristica.
4. Serie temporal de las caracteristicas especificas.
5. Pareto especifico de Vino Ligero.
6. Tabla de incumplimientos o casi incumplimientos.
7. Mapa de metricas internas coloreado por calidad cliente.
8. Tabla de falsos positivos y falsos negativos.

## 12. Relacion con Modulos Existentes

### 12.1 Historico

El modulo historico permite ver la evolucion general de rechazos. La capa de
segmento permitiria filtrar esa evolucion solo a defectos especificos de
`Vino Ligero`.

La etiqueta de calidad cliente deberia estar disponible como color o filtro en
las comparativas historicas para comprobar si las producciones reclamadas
presentan niveles, colas, tendencias o turnos peores.

### 12.2 Rendimiento ZC y gravedad

La pestana de rendimiento y gravedad ya separa defectos por criticidad. La capa
de segmento no sustituye ese analisis, sino que lo complementa:

- gravedad responde a "cuan severo es el defecto";
- segmento responde a "cuan relevante es este defecto para esta familia de
  botella".

La validacion contra reclamaciones debe permitir comprobar si las producciones
con reclamacion relevante tenian peor rendimiento ZC o mayor peso de defectos
criticos, graves o mayores.

### 12.3 Riesgo de incidencias

El modulo de riesgo trabaja con avisos y rechazos como incidencias temporales.
Cuando la equivalencia de codigos este validada, se podra marcar si una
incidencia pertenece a una caracteristica especifica de segmento.

Esto permitiria responder:

- si los avisos largos corresponden a defectos relevantes para Vino Ligero;
- si los eventos de cliente potencial estan dominados por defectos especificos;
- si una produccion tiene riesgo alto por volumen general o por defectos clave.

Ademas, las metricas de duracion, frecuencia, dispersion y recurrencia deben
compararse contra la etiqueta de calidad cliente para detectar que variables
explican mejor las reclamaciones.

### 12.4 Reclamaciones

El modulo de reclamaciones debe actuar como capa transversal. No sustituye a
Historico, Riesgo, Rendimiento ZC ni Salud Serie. Les aporta una etiqueta
externa para validar si las metricas tecnicas se alinean con la realidad
observada por cliente/calidad.

### 12.5 Inspeccion ZF

Los rechazos de proceso del PowerPoint parecen asociados a maquinas de
inspeccion. Por ahora no deben mezclarse con el analisis actual porque faltan
datos directos de esas maquinas.

Cuando existan datos de inspeccion ZF, se podra cruzar:

- defectos especificos de segmento;
- tasa de rechazo por maquina de inspeccion;
- estabilidad temporal de deteccion;
- consistencia entre ramales.

## 13. Riesgos y Decisiones Pendientes

### 13.1 Validacion de equivalencias

No se debe asumir que todos los codigos parecidos pertenecen a la misma
caracteristica. La tabla de equivalencias debe ser revisada por calidad.

### 13.2 Denominador de los limites

Debe confirmarse si los porcentajes del PowerPoint se calculan sobre:

- total de botellas fabricadas;
- total de rechazos;
- total de defectos detectados;
- total por cavidad;
- total por turno/lote/produccion.

Esta decision cambia completamente la interpretacion.

### 13.3 Segmento y tipo de cierre

Para Vino Ligero hay una diferencia importante entre:

- corcho;
- rosca.

La aplicacion debe saber que caracteristica aplicar en cada caso, o permitir
seleccionarla manualmente si el dato no esta disponible.

### 13.4 Limites provisionales

Los limites del PowerPoint deben mostrarse como referencias no definitivas hasta
que calidad confirme su validez.

### 13.5 Clasificacion de reclamaciones

La clasificacion de reclamaciones debe estar gobernada por reglas explicitas.
No debe depender de texto libre sin revisar.

Decisiones pendientes:

- lista cerrada de motivos excluidos de fabricacion;
- lista cerrada de motivos observados;
- severidad de cada motivo confirmado;
- tratamiento de reclamaciones repetidas del mismo defecto;
- tratamiento de reclamaciones asociadas a varios clientes en la misma
  produccion.

## 14. Propuesta de Implementacion Futura

Cuando se apruebe este enfoque, se recomienda implementarlo en fases pequeñas,
revisables y testeables. No debe abordarse como un unico cambio grande, porque
mezcla catalogos, reglas de negocio, calculo analitico y visualizacion.

### Fase 1. Catalogo de segmento

- Crear una estructura de datos para segmentos.
- Registrar las caracteristicas de Vino Ligero.
- Incluir codigos candidatos.
- Permitir revisar y cerrar equivalencias.

### Fase 2. Catalogo de reclamaciones

- Crear una estructura de datos para reclamaciones por produccion.
- Clasificar cada reclamacion como relevante, observada o excluida de
  fabricacion.
- Asignar etiqueta `Buena`, `Observada`, `Mala` o `Critica`.
- Mantener trazabilidad del motivo que justifica cada etiqueta.

### Fase 3. Backend analitico

- Calcular indicadores por produccion.
- Calcular indicadores por cavidad.
- Calcular evolucion temporal.
- Comparar contra limites provisionales.
- Exponer etiquetas de calidad cliente y reclamaciones junto a las metricas
  internas, sin mezclarlas en los scores de proceso.

### Fase 4. Frontend

- Crear bloque o pestana de `Salud Serie`.
- Crear pestana de `Validacion contra reclamaciones`.
- Mostrar tarjetas, tablas y graficos.
- Permitir filtrar por caracteristica.
- Permitir comparar producciones seleccionadas.
- Permitir colorear producciones por calidad cliente en visualizaciones
  comparativas.
- Mostrar falsos positivos, falsos negativos y casos observados.

### Fase 5. Validacion con calidad

- Revisar equivalencias.
- Revisar denominadores.
- Revisar limites.
- Revisar clasificacion de reclamaciones.
- Revisar si las metricas internas explican los casos reclamados.
- Ajustar visualizaciones segun utilidad real.

## 15. Lectura Operativa Esperada

La salida deberia permitir explicar una produccion con frases como:

```text
La produccion AV23_PROD_00X no es especialmente mala en rechazos totales,
pero concentra un peso elevado de Verticalidad, que es una caracteristica
especifica del segmento Vino Ligero. El problema esta localizado
principalmente en las cavidades 5D y 7D y supera el limite provisional de
proceso.
```

O bien:

```text
La produccion AV23_PROD_00Y tiene mas rechazos globales, pero los defectos
especificos de Vino Ligero estan por debajo de los limites provisionales. El
problema parece mas general de proceso que especifico del segmento.
```

Tambien deberia permitir lecturas de validacion contra reclamaciones:

```text
La produccion AV32_PROD_005 aparece como critica por reclamaciones de producto.
Las metricas internas que mas la separan del resto son el peso de defectos
graves/mayores, la presencia de defectos especificos del segmento y la
persistencia por cavidad. Es un caso alineado malo: las metricas internas
explican la reclamacion.
```

```text
La produccion AV23_PROD_002 tiene una reclamacion de etiqueta equivocada. Se
excluye del diagnostico de fabricacion, por lo que se mantiene como buena para
calidad del envase. La incidencia debe tratarse en una capa de embalaje o
identificacion.
```

```text
La produccion AV32_PROD_006 tiene una reclamacion de tipo cumple especificacion
/ indeterminado. Se marca como observada: no confirma fabricacion fuera de
criterio, pero conviene revisarla contra metricas internas y posibles criterios
de cliente.
```

Esta distincion es importante porque permite separar:

- mala produccion global;
- mala produccion para el segmento;
- produccion reclamada por cliente;
- produccion observada pero no confirmada como mala;
- reclamacion no imputable a fabricacion;
- problema localizado de cavidad;
- problema de criticidad alta;
- problema de ruido o defectos no caracteristicos.

## 16. Criterios de Implementacion

### 16.1 Entregables pequenos

La implementacion debe dividirse en entregables pequenos, cada uno con una
funcionalidad verificable:

1. Catalogo y modelo de datos de reclamaciones.
2. Clasificacion de reclamaciones por regla auditable.
3. Exposicion de etiquetas de calidad cliente en backend.
4. Coloreado transversal de producciones por calidad cliente.
5. Pestana de validacion contra reclamaciones.
6. Cruce con metricas internas existentes.
7. Deteccion de falsos positivos y falsos negativos.
8. Integracion posterior con Salud Serie y defectos especificos de segmento.

Cada entregable debe poder probarse y revisarse sin depender de que toda la
funcionalidad final este terminada.

### 16.2 Separacion de responsabilidades

La solucion debe mantener fronteras claras:

- ingestion: lectura y normalizacion de reclamaciones;
- dominio: reglas de clasificacion y etiquetas de calidad cliente;
- aplicacion: casos de uso y contratos REST;
- infraestructura: acceso a ficheros/parquet/excel;
- frontend: visualizacion, filtros y lectura operativa;
- tests: validacion de reglas, calculos y contratos.

Las reglas de negocio no deben quedar incrustadas en componentes visuales. El
frontend debe recibir datos ya clasificados y trazables desde el backend.

### 16.3 Reglas auditables

Toda etiqueta debe poder explicarse.

Ejemplo:

```text
AV32_PROD_006 -> Observada
Motivo: 1 reclamacion `Cumple especificacion / indeterminado`
Tratamiento: no confirma fabricacion mala, pero requiere seguimiento.
```

La respuesta de backend deberia incluir:

- etiqueta final;
- reclamaciones incluidas;
- reclamaciones observadas;
- reclamaciones excluidas;
- motivo de exclusion o inclusion;
- peor motivo considerado;
- contador por tipo.

### 16.4 Criterios de codigo

La implementacion debe seguir los criterios ya usados en el proyecto:

- Python moderno con type hints;
- FastAPI y diseno REST;
- Pydantic para contratos de entrada/salida;
- arquitectura por capas;
- modulos pequenos y cohesionados;
- `pathlib.Path` para rutas;
- logging estructurado, sin mezclar `print()` con logger;
- errores normalizados;
- ausencia de duplicidades;
- nombres de dominio claros;
- funciones pequenas, testeables y sin efectos laterales innecesarios;
- frontend con componentes reutilizables y visualmente consistentes;
- nada de hardcodear reglas en graficos si pertenecen al dominio.

### 16.5 Tests minimos por entregable

Cada entregable debe incluir pruebas proporcionales al riesgo:

- tests unitarios de clasificacion de reclamaciones;
- tests de equivalencia produccion-reclamacion;
- tests de exclusion de etiqueta equivocada;
- tests de inclusion como Observada de `Cumple especificacion / indeterminado`;
- tests de serializacion del contrato REST;
- tests de frontend para renderizar etiquetas y colores;
- tests de regresion para no romper historico, riesgo ni rendimiento ZC.

Casos obligatorios:

| Caso | Resultado esperado |
|---|---|
| 0 reclamaciones relevantes | `Buena` |
| 1 `Cumple especificacion / indeterminado` | `Observada` |
| 1 `Etiqueta equivocada` | `Buena` para fabricacion + reclamacion excluida |
| 1 reclamacion grave confirmada | `Mala` u `Observada grave`, segun regla final |
| 3 o mas reclamaciones relevantes | `Critica` |

### 16.6 Validacion funcional

Antes de considerar terminado un entregable se debe comprobar:

- que la informacion se entiende sin leer el codigo;
- que cada etiqueta tiene explicacion visible;
- que los colores no sustituyen al dato numerico;
- que los graficos permiten comparar producciones sin ambiguedad;
- que las reclamaciones no contaminan los scores internos;
- que las metricas internas pueden analizarse contra la etiqueta externa.

### 16.7 Criterio de aceptacion industrial

La funcionalidad sera util si permite responder con claridad:

- que producciones tuvieron reclamaciones relevantes;
- que producciones tuvieron reclamaciones observadas;
- que producciones tuvieron reclamaciones no imputables a fabricacion;
- que metricas internas explican mejor las producciones malas o criticas;
- que metricas producen falsos positivos;
- que metricas no detectan falsos negativos;
- si los defectos especificos de Vino Ligero aparecen en producciones
  reclamadas.

# Plan de migracion a `calidad_serie`

## Objetivo

Crear una aplicacion autonoma para Calidad que conserve exclusivamente el
analisis de **Calidad de produccion**: seleccion de producciones, configuracion
de parametros, ranking, mapa de penalizacion, detalle por produccion y detalle
por turno. La migracion debe conservar los resultados analiticos actuales y
dejar el proyecto preparado para su despliegue interno.

La aplicacion actual `Calidad_produccion` se mantiene intacta como referencia
funcional hasta que finalice la validacion y el despliegue de `calidad_serie`.

## Principios de ejecucion

- Una fase se implementa mediante una o varias PR pequenas y revisables.
- No se cambia ninguna formula de calidad durante la extraccion funcional.
- Cada cambio se valida contra resultados de referencia de la aplicacion actual.
- Los datos operativos, ficheros Excel y Parquet no se versionan en Git.
- La configuracion se declara mediante variables de entorno y ficheros de
  configuracion versionados, nunca mediante rutas personales o valores ocultos.
- El entorno productivo solo puede leer los datos; su actualizacion se resuelve
  mediante un proceso de ingesta externo y trazable.

## Invariantes del score

El motor de calidad conserva siempre los cuatro bloques actuales:

1. **MIA**: tasa real de cada defecto de maquina frente a su historico.
2. **A/R**: exposicion por minutos-cavidad de avisos y rechazos, agrupada por
   criticidad y comparada con los AQL globales.
3. **DAR**: subconjunto de defectos A/R especificos del segmento de botella.
4. **ZC**: rendimiento de zona caliente frente a su objetivo.

Los AQL, pesos, umbrales DAR, objetivo ZC y limites de estado siguen siendo
globales y configurables. Un segmento no crea ni elimina bloques del score ni
introduce fuentes nuevas en esta migracion. Su unica responsabilidad es
resolver que defectos normalizados de avisos/rechazos pertenecen a DAR.

La presentacion `Salud Serie limites` se utilizara como fuente de gobierno para
esa lista de DAR por segmento, pero sus limites de SIPQ, SIP, proceso y
laboratorio quedan fuera del score actual hasta que Calidad defina una futura
ampliacion expresa de sus fuentes y formulas.

## Alcance funcional conservado

### Interfaz

- Seleccion de lineas, producciones y ramales.
- Seleccion contextual de todas las producciones visibles y limpieza de la
  seleccion.
- Parametros editables de AQL, DAR, pesos, objetivo ZC y limites de estado.
- Ranking de penalizacion y mapa de penalizacion.
- Color de calidad cliente en el mapa.
- Modal de detalle de una produccion.
- Modal de detalle de cada turno, seleccion de dias, peores turnos y grafico
  temporal con la franja historica.
- Cache local de las ultimas consultas de calidad.
- No se muestra un resumen duplicado de la configuracion efectiva: los valores
  se editan y consultan en el desplegable de parametros del score.

### Backend y datos

- Catalogo de producciones y metadatos de Parquet.
- Calculo de score de calidad por produccion y turno.
- Calidad cliente para el color del mapa.
- Normalizacion MDL de los defectos de avisos/rechazos.
- Etiquetas MIA en su diccionario separado.

## Dependencias de datos que se conservaran

| Origen | Uso en calidad de produccion |
| --- | --- |
| `AV-2024-2026_parquet` | Catalogo, periodos, ramales y tasas MIA historicas. |
| `avisos_rechazos_parquet` | Exposicion por minutos-cavidad, AQL, DAR y repeticiones. |
| `inspeccionadas_totales_parquet` | Botellas inspeccionadas reales para MIA. |
| `Rendimiento_ZC.xlsx` | Rendimiento de zona caliente. |
| `COD_DEF.xlsx` | Etiquetas propias de defectos MIA. |
| `Tabla defectos con criticidades.xlsx` | Criticidades y metadatos de defectos. |
| `Programacion defectos MDL_enviado V01.xlsx` | Conversion Vidrala historica a la convencion MDL normalizada. |
| `config/customer_score.json` | Valores por defecto del score. |
| Catalogo de segmento DAR | Defectos normalizados que forman DAR para cada segmento. |

## Fases y PRs

### Fase 0. Base reproducible y contrato de regresion

**PR 0.1 - Inicializacion y saneamiento del repositorio**

- Registrar el estado inicial de `calidad_serie` y excluir artefactos locales,
  logs, caches, datos operativos y secretos.
- Revisar que `.gitignore` cubra los directorios de datos externos y los
  ficheros de entorno.
- Establecer el contrato operativo Windows: ubicacion separada de codigo,
  configuracion, datos, staging y logs; requisitos del servidor y reglas de
  actualizacion reversibles.
- Normalizar los finales de linea para que el mismo repositorio se ejecute de
  forma reproducible en Windows y en contenedores Linux.
- Sustituir en la documentacion las referencias a la aplicacion historica que
  ya no aplican.

**PR 0.2 - Casos de referencia de calidad**

- Definir un conjunto estable de producciones y turnos de AV23 y AV32.
- Guardar en pruebas las salidas esperadas: penalizacion total, contribuciones,
  categorias AQL, DAR, MIA, ZC, estado y orden del ranking.
- Separar datos sinteticos de pruebas de los datos industriales reales.

**PR 0.3 - Catalogo de botellas, segmentos y DAR**

- Definir un identificador estable para cada modelo de botella y su segmento;
  la linea no determina el segmento.
- Extender el manifiesto de producciones con planta, linea, modelo de botella,
  segmento y version de perfil DAR.
- Crear un catalogo versionado de DAR por segmento, basado en identificadores
  normalizados MDL, no en descripciones libres ni en codigos antiguos o nuevos
  aislados.
- Mantener vigencia, autor, fuente de aprobacion y motivo de cada cambio de
  perfil. Las producciones historicas conservan la version que se uso en su
  calculo.
- Asignar explicitamente el perfil correcto a la botella del piloto; no se
  infiere a partir de los defectos observados.

**Criterio de cierre:** el repositorio se puede clonar sin datos privados y la
aplicacion actual cuenta con una referencia objetiva para detectar cambios
analiticos no deseados; cada produccion conocida tiene un segmento y un perfil
DAR auditables.

### Fase 1. Frontend reducido sin cambiar la logica

**PR 1.1 - Pantalla unica de calidad**

- Reemplazar la navegacion por una pantalla unica con el encabezado, los
  filtros de catalogo y `ProductionQualityScorePanel`.
- Conservar la seleccion de lineas, producciones y ramales, asi como los
  botones Todas y Limpiar contextualizados por linea.
- Eliminar del bundle las vistas, estados y llamadas de historico, estadistica,
  incidencia, analisis complementario y calidad cliente como pestana propia.

**PR 1.2 - Consolidacion de tipos y cliente API**

- Mantener solo los contratos TypeScript y llamadas HTTP necesarios para
  catalogo, score de produccion y calidad cliente.
- Extraer el estado de calidad de `App.tsx` a componentes o hooks con limites
  claros: seleccion, parametros, ejecucion y cache.
- Mantener sin cambio los modales de produccion y turno.

**Criterio de cierre:** el frontend no muestra pestañas ajenas, compila y para
los casos de referencia produce la misma consulta API y la misma presentacion
de resultados.

### Fase 2. Backend minimo de calidad

**PR 2.1 - Factoría especializada**

- Crear una composicion exclusiva para calidad con los casos de uso `catalog`,
  `customer-score`, `customer-quality` y `health`.
- Conservar middleware, gestion de errores, logging y validacion de limites de
  consulta.
- No borrar todavia el backend amplio de referencia; la eliminacion se hace
  cuando los contratos esten verificados.

**PR 2.2 - Retirada controlada de modulos no utilizados**

- Eliminar rutas, casos de uso, repositorios y dependencias que solo sirvan a
  las pestañas descartadas.
- Mantener separadas las fuentes y diccionarios MIA y avisos/rechazos.
- Reducir dependencias Python que solo soporten analisis retirados, si las hay.

**Criterio de cierre:** la API publica solo expone los endpoints de calidad y
los tests de regresion dan resultados equivalentes a la aplicacion de origen.

### Fase 3. Configuracion externa y contrato de datos

**PR 3.1 - Configuracion portable**

- Eliminar valores por defecto con rutas de usuario Windows.
- Declarar todas las rutas necesarias con variables `APP_*` documentadas.
- Validar al arrancar que existen los Parquet, Excel y configuracion requeridos;
  informar con mensajes concretos del recurso ausente.

**PR 3.2 - Configuracion versionada de negocio**

- Mantener AQL, pesos, DAR, objetivo ZC y umbrales de estado en un esquema
  JSON validado.
- Separar el catalogo DAR por segmento de los pesos y AQL globales. Cada regla
  DAR referencia el identificador normalizado del defecto y es compatible con
  las convenciones antigua y nueva mediante la normalizacion MDL.
- Separar configuracion por defecto de las modificaciones temporales enviadas
  desde la interfaz.
- Registrar en cada respuesta la version/configuracion efectiva para que el
  resultado sea auditable.

**PR 3.3 - Salud y version de datos**

- Exponer una comprobacion de salud que distinga servicio disponible de datos
  disponibles.
- Incorporar versiones o fechas de actualizacion para manifest, Parquet,
  diccionarios y configuracion MDL.

**PR 3.4 - Contrato y validacion de entrada de producciones**

- Definir contratos de entrada por fuente: datos MIA, avisos/rechazos, botellas
  inspeccionadas, rendimiento ZC y metadatos de produccion.
- Registrar cada entrada en un area de `staging` con identificador de lote,
  checksum, fecha de recepcion, origen y version de esquema. Los originales no
  se modifican.
- Validar antes de transformar: columnas obligatorias, tipos, zonas horarias,
  intervalos temporales validos, inicio no posterior a fin, linea, produccion,
  cavidades, duplicados y coherencia con el rango de produccion declarado.
- Aplicar la normalizacion MDL de avisos/rechazos y emitir un informe de
  codigos no mapeados, ambiguos o fuera del catalogo. El lote se bloquea si
  supera el umbral de calidad de datos acordado.
- Validar que el segmento y perfil DAR declarados existen y son compatibles con
  la planta y periodo de la produccion.

**PR 3.5 - Pipeline de publicacion de datos**

- Implementar una tarea de ingesta independiente de la interfaz, inicialmente
  como comando de administrador o tarea programada. La aplicacion de consulta
  no aceptara archivos arbitrarios desde el navegador.
- Transformar cada fuente a Parquet particionado por planta, linea, modelo y
  produccion; deduplicar con las claves de negocio propias de cada origen.
- Generar manifest y agregados por produccion y turno en una ubicacion temporal.
  Solo tras superar todas las validaciones se publican mediante promocion
  atomica; una consulta nunca ve un lote a medio procesar.
- Versionar el conjunto publicado y conservar el informe de lote: filas leidas,
  aceptadas, rechazadas, duplicadas, defectos normalizados y artefactos
  generados.
- Invalidar cache por version de datos y conservar la posibilidad de reprocesar
  un lote de forma idempotente, o revertir a la ultima version publicada.
- Añadir pruebas de integracion del pipeline con datos sinteticos en
  convenciones antigua, nueva y mixta.

**Criterio de cierre:** cualquier entorno se configura sin editar codigo y una
consulta informa con claridad que datos y reglas utilizo; una nueva produccion
puede entrar mediante un lote validado, trazable, recuperable e invisible hasta
su publicacion completa.

### Fase 4. Rendimiento operativo

**PR 4.1 - Medicion y perfilado**

- Medir tiempos por etapa para AV23 y AV32, con una produccion y con la
  seleccion completa de cada linea.
- Registrar volumen de lectura, cache hit/miss y tiempo de calculo de turnos.
- Establecer presupuestos de rendimiento antes de optimizar: latencia p50/p95,
  tiempo maximo de consulta, concurrencia objetivo y consumo maximo de memoria.
- Ejecutar pruebas de carga con consultas reales representativas y usuarios
  concurrentes. La cache local del navegador no se considera una garantia de
  rendimiento de servidor.

**PR 4.2 - Tablas agregadas de calidad**

- Disenar una ingesta que genere agregados por produccion y turno con las
  entradas necesarias para score, AQL, DAR, MIA y ZC.
- Mantener la trazabilidad hacia los eventos y Parquet originales.
- Usar recalculo bajo demanda solo cuando cambien parametros que no puedan
  resolverse con el agregado.
- Mantener la trazabilidad desde cada agregado hasta el lote de entrada y las
  particiones Parquet que lo originaron.

**PR 4.3 - Cache de servidor**

- Sustituir o complementar la cache en memoria por una cache con clave basada
  en seleccion, configuracion y version de datos.
- Definir capacidad, expiracion e invalidacion al regenerar agregados.
- Para multiples replicas, usar una cache compartida solo si el perfilado
  justifica su complejidad.
- Limitar simultaneamente las consultas pesadas para proteger el servidor y
  devolver una respuesta controlada cuando se alcance la capacidad acordada.

**Criterio de cierre:** el tiempo de respuesta para todas las producciones de
una linea cumple el presupuesto acordado bajo la concurrencia objetivo y los
resultados conservan la misma trazabilidad.

### Fase 5. Empaquetado de produccion

**PR 5.1 - Imagenes de produccion**

- Cambiar el contenedor frontend de Vite en desarrollo a build estatico servido
  por Nginx o Caddy.
- Servir `/api` mediante proxy inverso al backend, en el mismo origen.
- Copiar `config/customer_score.json` y cualquier recurso versionado requerido
  por el backend.
- Ejecutar el backend con una configuracion de procesos y limites ajustada a
  calculos Polars, no como un unico proceso de desarrollo.

**PR 5.2 - Compose y volumenes de datos**

- Montar el directorio raiz de datos corporativos en solo lectura, no solo el
  subdirectorio de `AV-2024-2026_parquet`.
- Configurar de forma explicita cada ruta del backend en el contenedor.
- Añadir healthchecks de vida y preparacion, reinicio controlado, limites de
  CPU/memoria y cierre ordenado de consultas en curso.
- Separar configuracion, imagenes y volumenes entre desarrollo, pruebas y
  produccion.

**PR 5.3 - Despliegue Windows reproducible**

- Implementar un `deploy.bat` idempotente para clon inicial y actualizacion de
  instalaciones existentes. Recibe repositorio, release, directorios y archivo
  de configuracion sin incrustar credenciales.
- Requerir una etiqueta o commit de release verificable; no actualizar a una
  rama mutable ni borrar cambios locales de forma automatica.
- Verificar prerrequisitos, ejecutar el despliegue, esperar readiness y dejar
  registrada la version anterior para rollback.
- Añadir un modo `-WhatIf` o equivalente de validacion sin cambios y pruebas
  automatizadas de los flujos de instalacion, actualizacion y recuperacion.

**Criterio de cierre:** un despliegue limpio en un servidor de prueba arranca
con una unica configuracion de entorno y permite una consulta completa.

### Fase 6. Fiabilidad, seguridad y operacion corporativa

**PR 6.1 - Resiliencia y continuidad de servicio**

- Definir comportamiento ante caida de backend, almacenamiento de datos no
  disponible, lote en publicacion, cache inaccesible y consulta agotada por
  tiempo. Cada caso debe producir un error controlado, identificable y sin
  resultados parciales.
- Usar checks de `liveness` y `readiness`: una replica solo recibe trafico si
  puede leer la configuracion y la version de datos ya publicada.
- Si el nivel de disponibilidad requerido lo justifica, desplegar al menos dos
  replicas de backend tras un proxy balanceador. La cache y los metadatos de
  lote deben ser externos o reconstruibles; ninguna replica puede ser el unico
  punto de verdad.
- Definir copias de seguridad de configuracion, manifiestos, lotes publicados y
  agregados; probar restauracion y reversibilidad de una publicacion.

**PR 6.2 - Acceso y red**

- Integrar autenticacion corporativa o un proxy de identidad ya aprobado por
  la organizacion.
- Definir roles minimos: consulta, gestion de parametros y administracion.
- Exponer el servicio solo detras de HTTPS y un proxy corporativo; no publicar
  directamente puertos de desarrollo.

**PR 6.3 - Observabilidad y auditoria**

- Centralizar logs estructurados y errores.
- Registrar usuario, seleccion, configuracion efectiva, version de datos y
  duracion de cada calculo.
- Exponer metricas de latencia, errores, saturacion, cache, disponibilidad de
  datos y resultado de los lotes. Configurar alertas operativas y un runbook de
  actuacion.
- Definir responsables, ventanas de actualizacion de datos y procedimiento de
  recuperacion documentado.

**Criterio de cierre:** hay control de acceso, trazabilidad suficiente para
Calidad, recuperacion probada y una operativa documentada para soporte.

### Fase 7. GitHub, CI y despliegue

**PR 7.1 - Calidad automatizada**

- Ejecutar lint, pruebas backend, pruebas frontend, build y escaneo de secretos
  en cada pull request.
- Ejecutar pruebas de contrato, regresion de score, integracion de ingesta y
  carga basica antes de promover una version.
- Analizar dependencias e imagenes de contenedor para detectar vulnerabilidades.
- Publicar imagenes versionadas tras integrar cambios en la rama principal.

**PR 7.2 - Documentacion de entrega**

- Reescribir README para el producto reducido.
- Añadir guia de configuracion, guia de despliegue, arquitectura, modelo de
  datos y runbook de incidencias.
- Incluir ejemplos anonimizados de `.env`, nunca datos reales.

**PR 7.3 - Puesta en produccion**

- Desplegar primero en entorno de prueba con datos de solo lectura.
- Validar resultados contra la aplicacion actual y realizar aceptacion de
  usuarios de Calidad.
- Pasar a produccion con despliegue controlado, checks posteriores y plan de
  reversibilidad probado. Mantener la app actual como respaldo hasta el cierre
  formal.

**Criterio de cierre:** repositorio Git privado, pipeline verde, despliegue
reproducible y aceptacion funcional documentada.

## Orden de implementacion recomendado

1. PR 0.1, PR 0.2 y PR 0.3.
2. PR 1.1 y PR 1.2.
3. PR 2.1 y PR 2.2.
4. PR 3.1, PR 3.2 y PR 3.3.
5. PR 4.1 antes de decidir PR 4.2 y PR 4.3.
6. PR 5.1 y PR 5.2.
7. PR 6.1, PR 6.2, PR 6.3, PR 7.1, PR 7.2 y PR 7.3.

La optimizacion con agregados no se mezclara con la extraccion de la aplicacion:
primero se preserva la equivalencia; despues se mide y se acelera con datos.

## Registro de ejecucion

Implementado en el repositorio local:

- PR 0.1, PR 0.2 y PR 0.3: saneamiento, regresion determinista y perfiles DAR
  versionados por segmento.
- PR 1.1 y PR 1.2: interfaz y contratos reducidos a Calidad de produccion.
- PR 2.1 y PR 2.2: API y backend limitados a catalogo, score, calidad cliente y
  salud.
- PR 3.1 y PR 3.3: configuracion portable, liveness y readiness con version de
  manifest.
- PR 3.2, PR 3.4 y PR 3.5: validacion de configuracion de negocio, contrato de
  lote, comandos de validacion/publicacion atomica e informes de lote.
- PR 4.1 y PR 4.2: harness de medida, paquetes A/R y tasas MIA por produccion
  y turno, trazables y con paridad frente al origen. Las tasas MIA usan las
  botellas inspeccionadas reales deduplicadas por fecha/hora-ramal; las
  selecciones parciales mantienen su fallback exacto al origen. La release
  tambien materializa las observaciones ZC desde Excel a Parquet. Los paquetes
  se invalidan de forma segura cuando cambian sus fuentes; ZC conserva cache
  en memoria por version y fallback al Excel cuando no hay agregado vigente.
- PR 4.3: cache LRU de servidor para las tres ultimas consultas, invalidada por
  configuracion y version de datos, con coordinacion single-flight para que
  consultas identicas simultaneas no multipliquen el calculo pesado.
- PR 5.1, PR 5.2 y PR 5.3: imagenes productivas, Compose de solo lectura y
  despliegue Windows con release explicita, rollback, limites de recursos y
  ejecucion no privilegiada del backend.
- PR 6.1, PR 6.2 y PR 6.3: capacidad y timeout de consultas, metricas,
  readiness, roles delegados a proxy corporativo y runbook de operacion.
- PR 7.1 y PR 7.2: CI, Dependabot y documentacion operativa.

Pendiente de implementar antes de declarar puesta en produccion:

- Cierre de rendimiento: ejecutar las cuatro mediciones y la prueba de carga
  en el servidor destino, acordar presupuesto con Calidad e Infraestructura y,
  solo si no se cumple, perfilar una siguiente optimizacion sin sacrificar la
  exactitud de eventos ni los filtros parciales.
- PR 6.1 multi-replica: decidirla tras la prueba de carga. Requiere proxy
  balanceador y metricas/cache compartidas o reconstruibles.
- Integracion real del proxy, HTTPS e identidad corporativa en el servidor de
  validacion.
- PR 7.3: repositorio remoto privado, despliegue de prueba, pruebas de caida y
  aceptacion funcional documentada por Calidad.

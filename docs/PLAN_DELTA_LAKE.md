> Copia publicada del plan de trabajo. La fuente de edición conjunta permanece en `calidad_serie/fuentes-referencia/PLAN_DELTA_LAKE.md` del workspace local. Se actualiza explícitamente con `scripts/export_plan.py`; esta copia permite revisar los contratos desde el repositorio independiente.

# Plan de ejecución del Delta Lake de calidad de serie

Estado: borrador de trabajo para revisión conjunta.  
Fecha inicial: 14 de octubre de 2026.  
Revisión: 35 — Piloto develop activo con datos reales; carga histórica recuperable, comprobaciones MIA por contexto y turnos por fuente/origen. Ampliación por entregas a procesos independientes con presupuesto Docker/WSL2 y publicación de asociaciones sin copias completas de serialización. La conciliación global y la aceptación productiva siguen pendientes; ZF_OK conserva su revisión independiente.
Última revisión: 20 de octubre de 2026.
Proyecto: `calidad_serie`.

## 1. Objetivo y alcance

Construir en el servidor remoto un histórico creciente de datos de producción, inspección, avisos/rechazos y rendimiento ZC, a partir de los volcados Fenix seleccionados y de las referencias que ya utiliza la aplicación.

La ingesta será independiente de la aplicación. Leerá, normalizará, validará y publicará tablas Delta de forma incremental, incorporando tanto registros nuevos como modificaciones de registros existentes.

Secuencia operativa acordada el 19/10/2026: **validación local → GitHub/CI → piloto de ingesta en el servidor → validación continua e integración con la app**. GitHub precede al piloto; no se espera a terminar la integración de la app para versionar/probar la ingesta o contrastar nuevas llegadas reales. La copia local es una instantánea, no acredita operación sostenida ni el estado actual de planta. El piloto utiliza destinos separados y no requiere migrar previamente la aplicación.

Aclaración posterior del usuario: los puntos 2/3 —catálogos físicos y derivados— se implementan ahora sin esperar a ZF_OK. La validación local de ese código utiliza datos sintéticos; la reconstrucción y conciliación real v2 se harán en destinos separados **en el servidor**, después de configurar GitHub/CI. La integración de la app no es requisito de ese ensayo. Estado de ejecución y límites en 17.16.

La adaptación de la aplicación para consumir este histórico se abordará después. Este documento define decisiones y requisitos de implementación futura; su edición no ejecuta despliegues ni modifica código, fórmulas o datos. El trabajo se implementará por fases pequeñas y verificables, sin una reescritura o un PR único de gran tamaño.

Conservar información no implica conservar indefinidamente cada copia original de los CSV. La retención de archivos de entrada se decidirá separadamente de la retención de datos y versiones de las tablas.

### 1.1. Empaquetado de la ingesta en Docker y condiciones de despliegue

**Decisión de arquitectura propuesta:** distribuir la ingesta como una aplicación ejecutable dentro de una imagen Docker inmutable cuando TI confirme un runtime soportado. La ejecución nativa descrita en la sección 15 queda como alternativa mientras no exista ese runtime. Ambos modos mantienen los mismos contratos de datos, recuperación e idempotencia.

Este apartado contempla únicamente la ingesta y sus comandos operativos. El empaquetado de API y frontend se abordará en el plan de despliegue de la aplicación; no incluirlos en la imagen de ingesta ni desplegarlos como consecuencia de instalarla.

#### 1.1.1. Virtualización, contenedores y SSH son capacidades distintas

- Habilitar virtualización anidada permite ciertas opciones de ejecución de máquinas virtuales y contenedores, pero no instala Docker ni garantiza por sí solo un entorno soportado.
- SSH requiere su propio servidor, servicio, autenticación, permisos y conectividad/firewall. Puede existir con o sin Docker y no se habilita automáticamente al activar virtualización.
- Confirmar si el sistema es Windows Server o Windows de escritorio y qué runtime permite y mantiene TI. **Docker Desktop no está soportado en Windows Server**; no basar el diseño de producción en instalarlo allí.
- La imagen preferida será Linux, inicialmente para la arquitectura del servidor que se valide. Una imagen Linux no se ejecuta directamente en un motor configurado únicamente para contenedores Windows.
- Para Windows Server, valorar con TI una VM Linux con Docker Engine dentro del entorno autorizado. La virtualización anidada puede permitir esa VM si el Windows anfitrión ya es virtual; deben validarse hipervisor, recursos, operación y soporte. Otras alternativas requerirán aprobación técnica del runtime concreto.
- Si solo se autoriza un runtime de contenedores Windows, habrá que construir y probar una imagen Windows adecuada, con la compatibilidad host/base correspondiente. No dar por reutilizable la imagen Linux.

La selección del runtime y la topología de almacenamiento se resolverán antes de ejecutar el primer despliegue. No asumir que WSL, Docker Desktop, una VM Linux y contenedores Windows ofrecen las mismas garantías de operación en servidor.

#### 1.1.2. Qué contiene la imagen y qué permanece fuera

La imagen incluirá el código de ingesta, versión fijada de Python, dependencias bloqueadas, bibliotecas de sistema necesarias, contratos/esquemas y comandos de ejecución. La imagen de base se fijará por versión y digest, con una política explícita de reconstrucción y actualización.

No incluir en la imagen:

- CSV/Excel operativos, tablas Delta, `_delta_log`, almacén de control, checkpoints ni auditoría persistente.
- Credenciales, rutas específicas del cliente, certificados privados ni referencias de negocio cuya distribución no esté autorizada.
- Código del backend/frontend ni sus dependencias ajenas a la ingesta.

Los tres Excel de referencia y los JSON de negocio se suministrarán como referencias/configuración externas versionadas y de solo lectura durante cada ejecución. El lote registrará sus huellas. No incorporarlos accidentalmente al contexto de build desde `fuentes-referencia/`.

Usar contexto de build acotado y `.dockerignore`. El build será multietapa cuando permita separar compilación/herramientas de prueba del runtime. La imagen operativa no tendrá herramientas de desarrollo innecesarias. Si hay paquetes nativos, verificar sus wheels/bibliotecas y requisitos de CPU en la arquitectura real del cliente.

**Las versiones se eligen para la ingesta, independientemente del Python instalado por otros usuarios del servidor.** No instalar ni actualizar paquetes al arrancar el contenedor; las dependencias se resuelven durante el build controlado. El contenedor sigue dependiendo del kernel/runtime y recursos disponibles: empaquetar versiones no prueba la integridad del almacenamiento ni la semántica del cálculo.

#### 1.1.3. Una imagen y comandos separados por responsabilidad

Se propone una imagen operativa de ingesta con comandos explícitos, cuyos nombres finales se concretarán al implementar:

| Comando/responsabilidad | Ejecución | Permisos/destino |
| --- | --- | --- |
| Ingestar un lote/ciclo | Job acotado que termina con resultado y código de salida. | Entrada y referencias en lectura; Delta/control operativos en escritura. |
| Validar release | Misma imagen candidata ejecutando una validación funcional; suite CI en etapa de pruebas cuando corresponda. | Muestras y directorios de prueba exclusivos, sin mounts productivos de escritura. |
| Comprobar configuración/compatibilidad | Modo de comprobación sin operaciones de datos. | Solo lectura; no crea tablas ni adelanta consumo. |
| Reconciliar/reintentar | Recuperación explícita que conserva el descriptor del lote. | Control/Delta aprobados y bloqueo de escritor; no genera otra ingesta independiente. |
| Compactar o mantener | Comando separado de la ingesta ordinaria. | Ventana/bloqueo de mantenimiento y política aprobada; nunca desde el instalador. |

No crear un contenedor diferente por cada CSV ni incluir un programador complejo dentro de la imagen inicialmente. Un programador externo lanza jobs de la release activa. Compose podrá describir imagen, mounts, variables y recursos; no es por sí mismo el programador de cargas periódicas.

La programación podrá residir en Windows —invocando el runtime por el mecanismo autorizado— o en el host Linux del motor. TI debe definir cómo se controla ese host sin SSH si este no está disponible.

Cada job registra su identidad, release/digest y salida persistente. No aplicar reinicio ilimitado del contenedor para ocultar fallos; los reintentos se rigen por la sección 8. El stdout puede enviarse al sistema de logs, pero no sustituye al control y auditoría que deben sobrevivir al contenedor.

#### 1.1.4. Montajes y almacenamiento persistente

Rutas interiores ilustrativas, no directorios ya creados:

| Ruta en el contenedor | Contenido | Modo |
| --- | --- | --- |
| `/inputs` | Volcados disponibles de Fenix. | Solo lectura. |
| `/references` | Referencias Excel/catálogos externos. | Solo lectura. |
| `/config` | Configuración externa validada para la ejecución. | Solo lectura. |
| `/lake` | Tablas Delta productivas. | Escritura únicamente para jobs operativos autorizados. |
| `/control` | Control persistente, checkpoints y auditoría. | Escritura operativa. |
| `/work` | Entradas estables/preparadas necesarias para recuperación. | Persistente mientras existan lotes pendientes. |
| `/tmp` | Temporales regenerables. | Efímero, con límite de recursos. |

La validación utilizará mounts de prueba independientes; nunca montar `/lake` productivo en escritura para comprobar una imagen candidata. Solo cambiar una variable `ENV=test` no proporciona aislamiento.

Los datos persistirán en ubicaciones externas a la capa escribible del contenedor, con nombres/rutas estables, inventario y backup. La eliminación/sustitución de un contenedor o imagen no elimina esos datos. No usar volúmenes anónimos para el histórico ni acciones como `compose down -v`, borrado de volúmenes o limpieza general en el script de actualización.

Los bind mounts se resuelven **en el host del motor Docker**, no necesariamente en el Windows donde se ejecuta el `.bat`. Si el motor está en una VM Linux, una ruta `C:\...` del servidor Windows no aparece automáticamente dentro de ella. Definir cómo llegan los volcados a ese host: transferencia controlada o acceso de solo lectura validado.

Preferir disco local del host del motor para Delta y el control transaccional. Acceder a inputs de Windows mediante una ruta compartida no obliga a escribir Delta en ese mismo recurso. SMB/CIFS y mecanismos de compartición Windows–Linux requieren pruebas de atomicidad, locks, SQLite cuando proceda, persistencia y recuperación; Docker no corrige sus limitaciones.

La configuración de ejecución validará los mounts reales, raíces y permisos. Un montaje ausente debe producir un error, no crear inadvertidamente un histórico vacío dentro del contenedor. La inicialización de tablas corresponde al primer lote operativo aprobado, no al build, pull, load o arranque de validación.

#### 1.1.5. Identidad de ejecución y acceso al motor

Ejecutar como usuario no root con UID/GID/permisos resueltos para el almacenamiento, límites de CPU/memoria, filesystem raíz de solo lectura cuando sea viable y sin modo privilegiado. No montar el socket Docker en la aplicación de ingesta. La ingesta por archivos no necesita publicar un puerto HTTP por defecto.

El acceso administrativo al motor permite lanzar contenedores y montar recursos del host. Por ello, **pertenecer al grupo Docker no equivale a una cuenta de despliegue restringida a código**. Para sostener el aislamiento de la sección 3.2, TI deberá proporcionar ejecución delegada con configuraciones/mounts autorizados o un control equivalente; si concede administración completa del motor, documentar ese límite de privilegios y no afirmar que los permisos interiores lo eliminan.

Entrada, validación y mantenimiento tendrán configuraciones permitidas diferentes. Los secretos se entregarán por el mecanismo externo aprobado, no por capas de imagen, argumentos impresos o repositorios. La confianza en certificados corporativos se configurará de forma controlada si hace falta acceder a un registry.

#### 1.1.6. Build, pruebas y distribución

1. CI comprueba código, contratos y pruebas con la plataforma elegida.
2. Construye la imagen candidata a partir del commit, base y lockfile fijados, utilizando caché de capas sin modificar dependencias en ejecución.
3. Prueba la imagen final —no solo un Python local— en creación, merge, idempotencia, actualización, logs persistentes y recuperación. Verifica usuario, mounts y modo de validación.
4. Genera descriptor de release con commit, procedencia, CI, digest/ID de imagen, plataforma, base, dependencias y contratos compatibles. Conserva inventario de componentes y resultados de comprobaciones de vulnerabilidades conforme a la política acordada.
5. Distribuye mediante un registry autorizado, por ejemplo GHCR si el cliente lo permite, o mediante un paquete offline exportado con `docker image save` y cargado con `docker image load`.
6. Verifica identidad en el destino antes de probar/activar. Con registry, fijar el digest; en distribución offline, comprobar la huella del paquete y el ID de imagen esperado, sin asumir que se conserva la referencia `RepoDigest` del registry.

No ejecutar `latest` como referencia productiva ni reconstruir una imagen distinta en el servidor a partir de una etiqueta mutable. Las actualizaciones de base o dependencias generan otra imagen/release, probada antes de promoverla. La caché reduce trabajo de build; no convierte una imagen en un entorno que se modifica instalando paquetes después.

#### 1.1.7. Despliegue con y sin SSH

| Acceso disponible | Entrega y activación |
| --- | --- |
| Consola/RDP y acceso al registry | Acceder al servidor, ejecutar `.bat`/PowerShell para obtener la imagen exacta, validar en pruebas y promover la configuración operativa. |
| Consola/RDP sin acceso al registry | Transferir por un canal autorizado el paquete de imagen y descriptor, verificar sus huellas, cargar la imagen y seguir la misma validación/activación. |
| SSH habilitado al host correspondiente | Invocar remotamente el mismo procedimiento autorizado; cambia el transporte, no los controles ni el alcance de escritura. |

No tener SSH no impide instalar una imagen. Sí necesitamos un acceso de administración autorizado al motor/host donde se ejecutará, directamente o mediante TI. No habilitar una API Docker remota sin protección como sustituto de SSH.

La actualización prepara/pull/load la imagen candidata mientras la ingesta sigue operando; realiza pruebas aisladas, espera la frontera segura, reconcilia pendientes y cambia la referencia activa por digest/ID. No altera mounts persistentes ni archivos Delta. Solo los jobs operativos posteriores escriben datos con sus lotes y auditoría.

Rollback selecciona la imagen y configuración anteriores, tras comprobar compatibilidad y exclusión de escritor. No revierte ni elimina las tablas. Mantener las imágenes requeridas para recuperar lotes preparados con una release anterior hasta resolverlos.

El bloqueo debe funcionar en la topología elegida. Un mutex exclusivo de Windows no protege automáticamente procesos dentro de una VM/contenedor Linux. Validar un mecanismo compartido por todos los escritores y mantenimiento; al cambiar de ejecución nativa a Docker, desactivar el escritor anterior y comprobar que no queda ningún proceso antes de iniciar el nuevo.

#### 1.1.8. Criterios de aceptación específicos

- [ ] TI confirma runtime, plataforma, arquitectura y topología soportados; SSH se trata como requisito independiente.
- [ ] La imagen fija Python/base/dependencias y funciona sin instalaciones al arrancar.
- [ ] La CI prueba la imagen que se distribuye; el servidor ejecuta la identidad validada.
- [ ] El histórico, control y lotes preparados sobreviven a eliminar/recrear el contenedor.
- [ ] Pull/load/build/despliegue/rollback no generan commits Delta de producción.
- [ ] El modo de pruebas no dispone de mounts productivos de escritura.
- [ ] Validado el acceso desde Fenix/Windows al host del motor y las garantías del filesystem elegido.
- [ ] Validado el bloqueo entre jobs, recuperación y mantenimiento, incluidos cambios de modalidad.
- [ ] Ensayado cierre ordenado y caída abrupta con commit incierto; la recuperación sigue la sección 8.
- [ ] Ensayada entrega sin SSH y, si corresponde, sin acceso al registry.
- [ ] Datos y logs persistentes respaldados junto con el descriptor y las imágenes necesarias para recuperación.

Estas decisiones describen el empaquetado deseado. No afirman que Docker, SSH o el almacenamiento requerido estén ya configurados en el servidor.

## 2. Fuentes acordadas

### 2.1. Diccionarios Fenix

| Tabla | Información que conservamos |
| --- | --- |
| `SIP_DEFECTO.csv` | ID, código y descripciones del defecto o motivo de un aviso/rechazo; se excluyen `MEDIDA` y las entradas «NO USAR». Contrato acordado en 4.2. |
| `SIP_DEFECTO_STATUS.csv` | ID, código y descripciones de la procedencia del aviso/rechazo, conservando `ORIGEN` y `ZONA` como códigos originales; se excluye `ACTIVO`. Contrato acordado en 4.3. |
| `SIP_DEFECTO_AR.csv` | ID, código y descripción del tipo de aviso/rechazo y código de agrupación original. Contrato acordado en 4.4. |
| `SIP_MODELO.csv` | Correspondencia compartida en Aiala entre ID interno Fenix y código del modelo de botella; se excluye el estado. Contrato acordado en 4.1. |

Relaciones que deben preservarse para futuros filtros sobre `LISTA_AR`:

- `COD_AR` referencia `SIP_DEFECTO.CODIGO`: qué defecto o motivo es.
- `STATUS` referencia `SIP_DEFECTO_STATUS.CODIGO`: de dónde procede el registro.
- `TIPO` referencia `SIP_DEFECTO_AR.CODIGO`: qué clase de registro es.

No confundir `STATUS` con el estado abierto/cerrado del evento. Tampoco interpretar el campo `ACTIVO` de un catálogo como actividad temporal de un aviso.

La posible unificación de diccionarios queda para una fase posterior. Inicialmente se conservarán separados, con su contexto de origen.

### 2.2. Registro temporal MP9

| Tabla | Información que conservamos |
| --- | --- |
| `MP9_CAMBIO_MODELO.csv` | Entradas/salidas ZC/ZF, vínculo entre zonas, máquina física y confirmación Fenix. La revisión 31 recupera `ID_MAQUINA`; siguen excluidos `ID_PREPARACION` y `N_MOD`. Contrato v2 implementado en revisión 33, sin reconstruir todavía las tablas reales v1. |

La tabla MP9 conservada no es un simple diccionario. Sus registros pueden cambiar y recibir fechas de cierre.

La delimitación de producciones se acuerda en la sección 16 a partir de entradas ZC y salidas ZF relacionadas mediante `ID_ST1_REL`, con evidencia del cambio real de AV32 del 14/10/2026. La validación de otros orígenes, la precisión física de las fechas y las incidencias de datos siguen sujetas a comprobación; no confundir este acuerdo de negocio con una implementación ya validada.

### 2.3. Medidas y eventos

| Tabla | Información que conservamos |
| --- | --- |
| `LISTA_DEFECTOS.csv` | Defectuosidad MIA de zona fría; conservar sus 18 columnas originales. Clave, deduplicación, bases de inspección y compatibilidad con el histórico en 4.6. |
| `LISTA_RECH.csv` | Conservar sus nueve columnas de inspeccionadas, rechazadas y rechazos automáticos; fuente preferente del denominador MIA. Contrato en 4.7. |
| `LISTA_AR.csv` | Conservar sus 12 columnas y cada tramo de aviso/rechazo por separado, aunque comparta `ID_Agrupación`; contrato en 4.8. El cálculo de minutos-cavidad queda pendiente. |
| `CONT_ZC.csv` | Conservar los 17 campos originales de contadores, velocidades e indicador de salida. Rendimiento con rueda y teóricos; contrato en 4.9. |

Total vigente desde el 19/10/2026: **11 familias Fenix**, las nueve ya implementadas más los dos catálogos físicos de 2.6. No es la selección inicial de once: `MP9_MOD_LINEA_ZF` y `MP9_PARAM_MODELO` siguen excluidas. El prefijo de fecha no crea otra tabla. Los resultados históricos de las entregas 01–11 corresponden a las nueve fuentes y contratos v1; no acreditan la implementación de esta ampliación.

### 2.4. Referencias existentes

Las siguientes cinco fuentes están copiadas junto a este plan, en `fuentes-referencia/`:

| Archivo | Función | Tratamiento inicial |
| --- | --- | --- |
| `COD_DEF.xlsx` | Diccionario de códigos y descripciones MIA. | Tabla Delta de referencia. |
| `Programación defectos MDL_enviado V01.xlsx` | Equivalencias antiguas/nuevas, descripciones, criticidad y zona. | Tabla Delta de referencia que preserve las correspondencias múltiples. |
| `Tabla defectos con criticidades.xlsx` | Clasificación de defectos y correspondencias de códigos. | Tabla Delta de referencia; conservar hasta validar su solapamiento con MDL. |
| `customer_score.json` | Pesos, límites y reglas del score. | Configuración versionada; no convertir a Delta inicialmente. |
| `segment_dar_profiles.json` | Perfiles y agrupaciones de códigos DAR por segmento. | Configuración versionada; conservar las asignaciones específicas a producciones cuando existan. |

Estas copias no han sustituido las rutas activas del backend. Antes de automatizar actualizaciones, se elegirá una única fuente autorizada para cada referencia y configuración, evitando mantener copias divergentes.

El catálogo MIA existente debe validarse frente a los códigos de cada nueva instalación. No asumir equivalencia entre catálogos o plantas por coincidencia numérica.

Se conservará el histórico ya disponible para las referencias estadísticas del componente MIA. No hace falta duplicar el Excel histórico original si su información está correctamente conservada en Parquet. Si se carga a Delta, se hará como una migración inicial controlada, conservando procedencia y separando códigos originales de normalizados.

`CONT_ZC` será la fuente de las nuevas observaciones de rendimiento ZC. No se añade `Rendimiento_ZC.xlsx` como una fuente adicional obligatoria para nuevas producciones; habrá que validar que la transformación reproduce la información necesaria para el cálculo actual.

### 2.5. Fuentes excluidas del alcance inicial

Se mantienen excluidas por decisión del usuario:

- `MP9_MOD_LINEA_ZF.csv`: descartada por decisión del usuario. No crear su tabla Delta de origen, incluirla como fuente obligatoria ni utilizarla como dependencia de las nueve fuentes conservadas. La tabla derivada de asociación temporal producción–ramal acordada posteriormente en 17 no reincorpora esta fuente ni crea un catálogo maestro de ramales. Se reconoce que una línea puede alojar modelos simultáneos por diferentes ramales; si una asignación no puede resolverse con los datos conservados y las evidencias validadas, documentar la carencia concreta sin inventar relaciones ni reincorporar fuentes automáticamente. El orden de exportación y las repeticiones no se interpretan como nuevas producciones. Esta decisión de alcance no elimina los archivos originales de la copia Fenix.
- `MP9_PARAM_MODELO.csv`: descartada por decisión del usuario al no aportar información necesaria para el alcance actual. No generar su tabla Delta ni exigir sus parámetros como dependencia de las nueve fuentes seleccionadas. Esta exclusión no elimina los archivos originales de la copia Fenix ni modifica el contrato de `MP9_CAMBIO_MODELO`.
- `CNF_LIN_ZF.csv`.
- `CONT_GEN.csv` y `CONT_ZF.csv`.
- `MP9_LISTA_DEFECTOS_ZC.csv`, `MP9_LISTA_RECH_ZC.csv` y `MP9_MAQUINA_MIA_ZC.csv`.
- `MP9_MOD_SECCION.csv` y `SIP_MP9_SECCION.csv`.
- `MUESTR_ZF.csv`, `PESO_MUESTR_ZF.csv` y `PESOS_ZC.csv`.
- `SIP_COLOR.csv` y `SIP_PROCESO.csv`.
- `SIP_CONEXION_MAQUINAS.csv` y `SIP_CONEXION_PLANTAS.csv`.
- `SIP_LINEA_ZC.csv`. `SIP_LINEA_ZF.csv` se reincorpora expresamente en la revisión 31, con el alcance mínimo de 2.6 y 17.14.
- `SIP_NUMERO_GOTAS_SIMULTANEAS.csv`. `SIP_MAQUINA_MOLDEO.csv` se reincorpora expresamente en la revisión 31, con el alcance mínimo de 2.6 y 17.14.
- `SIP_MP9_NOMENCLATURA_CAVIDAD.csv`.
- `SIP_MP9_DEFECTO_MIA.csv`, `SIP_MP9_FAMILIA_MIA.csv`, `SIP_MP9_DEFMIA_GRUPO.csv` y `SIP_MP9_DEFMIA_SUBGRUPO.csv`.
- `VW_CAMBIO_MATERIAL.csv`.

No se reincorporarán automáticamente ni se exigirá archivarlas todas. Si una validación descubre información ausente, se documentará el caso concreto y se revisará conjuntamente la decisión.

### 2.6. Catálogos físicos incorporados por acuerdo del 19/10/2026

| Fuente | Información imprescindible | Destino acordado |
| --- | --- | --- |
| `SIP_MAQUINA_MOLDEO.csv` | ID de máquina Fenix, denominación —AV31A/AV31B, por ejemplo—, código de línea ZC y fechas de vigencia declaradas. | Catálogo Silver de máquinas físicas con procedencia y vigencia. |
| `SIP_LINEA_ZF.csv` | ID interno de línea ZF, código de ramal utilizado por MIA, ID de máquina asociado y fechas de vigencia declaradas. | Relación Silver temporal ramal–máquina, diferenciada de producción–ramal. |

La autorización responde a una carencia comprobada: modelo/origen/fecha no distinguen las dos fabricaciones simultáneas de 1358/242 en AV31. No se incorpora automáticamente el resto de columnas ni las otras fuentes excluidas. `SIP_CONEXION_MAQUINAS` y `MP9_MOD_LINEA_ZF` se han consultado como corroboración, pero no pasan a ser dependencias operativas obligatorias. Esquemas mínimos, claves, integridad, actualizaciones y transición desde v1 se fijan en 17.14.

## 3. Arquitectura inicial propuesta

Motor propuesto: **Python + Polars/Arrow + `deltalake` (delta-rs)**, sin Spark inicialmente.

La distribución Docker de la sección 1.1 será la opción preferida cuando exista un runtime soportado. Mientras no esté disponible, la ingesta podrá ejecutarse nativamente mediante el Programador de tareas de Windows. La compatibilidad del runtime y del almacenamiento debe validarse en ambos casos. La ingesta no depende del despliegue de la aplicación.

Flujo lógico acordado:

```text
Volcados Fenix seleccionados y referencias autorizadas
              ↓
Bronze: CSV originales seleccionados, copias estables y control de recepción
              ↓
Silver de origen: nueve tablas Delta tipadas, validadas y deduplicadas
                    + referencias Excel normalizadas
              ↓
Silver derivada: catálogo de producciones ZC–ZF y asociación temporal producción–ramal
              ↓
Gold / datos de consulta: asociaciones a producción y resultados para la app
              ↓
Publicación coherente y consumo por la aplicación —fase posterior—
```

Habrá una tabla Delta normalizada por cada una de las nueve familias seleccionadas y tres tablas de referencia procedentes de Excel. Se añaden dos entidades derivadas de negocio en Silver: catálogo de producciones (16) y asociación temporal producción–ramal (17). Las nueve fuentes no limitan a nueve el número total de estructuras derivadas; tampoco justifican copiar todas las fuentes en cada capa. Las capas expresan responsabilidades, no requieren servicios o contenedores separados.

Se propone almacenamiento local del servidor y **un único proceso escritor**, sin ejecuciones solapadas. Una ruta compartida SMB u otro almacenamiento requerirá validación específica de las garantías de escritura del motor. La carpeta de llegada puede estar separada del almacenamiento de las tablas Delta.

### 3.1. Separación obligatoria de responsabilidades

| Componente | Responsabilidad | Límite |
| --- | --- | --- |
| Aplicación | API, visualización y consulta de datos publicados. | Su instalación, actualización y pruebas no escriben ni mantienen las tablas Delta. |
| Ingesta | Leer las fuentes y ejecutar las operaciones de datos aprobadas. | Solo el proceso operativo autorizado puede escribir en Delta de producción. |
| Despliegue | Preparar código, entornos, validación y activación de una release. | No crea, cambia, compacta, restaura ni elimina tablas Delta de producción. |
| Almacenamiento | Datos Delta, originales retenidos, referencias y auditoría persistentes. | Independiente de los checkouts Git, releases y entornos virtuales. |
| Validación | Probar releases con entradas controladas y tablas de prueba. | Rutas y permisos separados; nunca usar producción como destino de escritura. |

**Invariante: actualizar, clonar, sustituir o revertir cualquiera de los repositorios —aplicación o ingesta— jamás ejecutará modificaciones en las tablas Delta de producción.** Cambiar paquetes tampoco constituye una operación de datos.

Después de activar una release, la tarea operativa de ingesta podrá procesar fuentes y escribir según sus contratos. Esa ejecución es distinta del despliegue, tiene su propia identidad, autorización, lote y auditoría. Una release que necesite alterar datos existentes no lo hará como efecto secundario de instalarse.

Aplicación e ingesta tendrán repositorios o unidades de release independientes, dependencias bloqueadas y entornos Python independientes cuando ambos se ejecuten nativamente. La topología exacta de los repositorios se decidirá antes de crearlos; su independencia operativa es obligatoria.

Las raíces de código, entornos, entrada, pruebas, control y Delta persistente estarán explícitamente configuradas y no se solaparán. No guardar datos operativos ni entornos virtuales dentro de un checkout. No sustituir carpetas de datos al actualizar una release.

### 3.2. Aislamiento efectivo de rutas y permisos

- La cuenta de despliegue no tendrá permisos de escritura sobre el Delta productivo.
- La cuenta de aplicación tendrá lectura de las publicaciones que necesite, sin permisos de mantenimiento o ingesta.
- La cuenta operativa de ingesta tendrá los permisos necesarios únicamente sobre las raíces aprobadas.
- Las validaciones usarán identidad/configuración que no permita escribir en producción. El nombre de la rama no sustituye estos controles.
- Cada comando validará las rutas absolutas resueltas y rechazará solapamientos o alias hacia producción, incluidos enlaces/junctions cuando sean aplicables.
- Un comando de prueba sin destino explícito y validado fallará; no heredará una ruta de producción por defecto.
- Limpieza de pruebas limitada a su ejecución identificada. Nunca reutilizar una rutina de borrado de staging sobre una raíz de datos.

Las pruebas podrán ejecutarse mientras la ingesta productiva sigue activa porque sus escrituras están aisladas. Si se utilizan muestras de producción, serán copias o snapshots aprobados y de solo lectura; no se modificarán entradas que esté leyendo la tarea activa.

En Docker, el aislamiento incluye los mounts efectivos y la delegación de acceso al motor descrita en 1.1.5. No considerar cumplida la separación solo por el usuario interior del contenedor si el operador puede modificar libremente sus mounts desde un motor con privilegios administrativos.

### 3.3. Bronze: recepción y conservación de originales

**Decisión acordada:** comenzar con los CSV originales de las nueve familias seleccionadas como Bronze, sin crear otras nueve tablas Delta de réplica en esta capa. La carpeta de llegada de Fenix es una bandeja de entrada; no es por sí sola un archivo inmutable ni una tabla de consulta.

- Capturar una copia estable antes de procesar, con identidad de recepción, origen y huella del contenido. Una vez aceptada en Bronze, no sobrescribir esa copia con una nueva revisión.
- Organizar los originales por planta, origen Fenix, familia y fecha de recepción, distinguiendo entregas con nombres coincidentes. La fecha de recepción no sustituye a las fechas de negocio contenidas en las filas.
- Conservar bytes originales y evidencia suficiente de formato/procedencia. El esquema tipado y las exclusiones de campos pertenecen a Silver; no editar el CSV recibido para adaptarlo.
- No archivar obligatoriamente las familias excluidas ni convertirlas en dependencias por el hecho de que Fenix las entregue junto con las seleccionadas.
- Registrar estados de recepción/proceso, huellas y resultados en el control persistente. Su implementación puede ser el almacén operativo previsto en la sección 9; no exige una tabla Delta por cada función de control.
- Separar la retención de Bronze de la del histórico Silver. Los CSV no tienen que conservarse indefinidamente: solo eliminarlos con carga verificada, recuperación cubierta y política de retención aprobada. La evidencia íntegra de conflictos y decisiones no dependerá de un original que pueda caducar.

Las referencias Excel autorizadas se recibirán con las mismas garantías de copia estable y procedencia; sus tablas normalizadas son referencias Silver. Los JSON de reglas siguen siendo configuración versionada, no nuevas tablas Delta por defecto.

### 3.4. Silver y Gold: datos consolidados y consumo por producción

| Ámbito | Contenido | Responsabilidad |
| --- | --- | --- |
| Silver de origen | Nueve tablas Delta consolidadas y referencias Excel normalizadas. | Conservar información seleccionada con tipos, claves, cierres y conflictos de la sección 4, sin multiplicar observaciones por producción. |
| Silver derivada | Catálogo de producciones (16) y `asignacion_produccion_ramal` (17). | Representar cada fabricación y sus límites declarados ZC–ZF, y qué producción circulaba efectivamente por cada ramal ZF durante su vigencia validada. |
| Gold / datos preparados para la app | Observaciones asociadas a producciones, agregados y salidas compatibles con el consumidor. | Facilitar consultas, comparaciones y visualización, conservando la procedencia y las reglas aplicadas. |

Mantener las nueve tablas Silver de origen con sus contratos acordados. La asociación a producción se incorporará en los datos derivados para la aplicación; no añadir ahora `id_produccion` a sus esquemas de negocio ni modificar los CSV originales.

Se recomienda resolver las asociaciones inequívocas al preparar los datos de consulta, en lugar de repetir toda la reconstrucción en cada petición. Una referencia `id_produccion` no copia el modelo ni las fechas del catálogo. Conservar la regla/versión aplicada y los registros fuente que justifican la asignación, y permitir recalcular únicamente el ámbito afectado por cierres o correcciones validados.

La materialización concreta de Gold se definirá al integrar la app: vistas o consultas cuando sean suficientes y salidas materializadas cuando lo justifiquen volumen, latencia o compatibilidad. Puede mantenerse inicialmente el formato Parquet por producción que consume la aplicación. Eso no convierte los Parquet individuales en tablas Delta de origen ni obliga a crear una tabla Delta por producción.

La necesidad ya contrastada justifica el catálogo de producciones y una tabla de asociación temporal producción–ramal. Esta última sustituye a la decisión anterior de dejar aparcada esa relación física; no es una tabla maestra de ramales ni reincorpora fuentes excluidas. No se exige una copia física adicional de todos los periodos MP9 ni una tabla universal de asignaciones de observaciones. Los resultados para la app se prepararán a partir de relaciones aceptadas, con tratamiento específico de medidas compartidas de 16.7 y 17.

### 3.5. Entregas sucesivas y vida de las tablas Delta

**Aclaración de entrega confirmada por el usuario, todavía en fase de prueba:** se prevé que, en todas las líneas, las familias modificadas durante los últimos diez minutos vuelvan a aparecer con un nombre que incorpora su fecha/hora de modificación indicada por el productor. Las familias sin cambios conservan su nombre anterior y no tienen por qué generar otro archivo en cada ciclo. En la captura aportada conviven versiones anteriores y nuevas. No considerar esta modalidad un contrato definitivo o exclusivo mientras dure la prueba.

La ingesta deberá soportar también archivos **sin hora en el nombre** que se sobrescriban o modifiquen aproximadamente cada diez minutos cuando haya registros nuevos o cambios. Un nombre sin hora puede conservar otro prefijo de fecha o ser únicamente el de la familia; su patrón deberá configurarse y validarse. Ambos modos alimentan las mismas tablas Silver y se detectan sin depender exclusivamente del nombre.

#### 3.5.1. Modalidades admitidas y ejemplos

| Modalidad | Ejemplo | Tratamiento |
| --- | --- | --- |
| Nueva versión con fecha/hora en el nombre | `16102026_91011_CONT_ZC.csv` y después `16102026_92028_CONT_ZC.csv` | Son dos entregas/revisiones de `CONT_ZC` del mismo origen, no tablas distintas. Procesar novedades y modificaciones válidas por su contrato. |
| Familia sin modificación | Un diccionario mantiene su archivo y nombre previos mientras otras familias generan versiones | No exigir una versión nueva de cada familia cada diez minutos ni interpretar su ausencia como fallo, baja o cierre. Reutilizar el último estado aceptado con su cobertura registrada. |
| Archivo sin hora sobrescrito | `CONT_ZC.csv` o un nombre fechado sin hora recibe otro contenido tras un ciclo | Detectar la revisión por contenido y metadatos, estabilizarla y procesarla aunque la ruta ya figure como recibida. No marcar una ruta como procesada para siempre. |
| Coexistencia o transición de modalidad | Una línea mantiene archivos sin hora y otra genera versiones; una familia cambia de formato durante la prueba | Admitirlo sin reinicializar Silver ni duplicar registros. La configuración discrimina planta/origen/familia y conserva trazabilidad de cada recepción. |

Los nombres de la captura parecen codificar `16102026` como 16/10/2026 y `91011` / `92028` como 09:10:11 / 09:20:28. Confirmar el patrón de fecha y la longitud variable de la hora antes de fijar el parser; no exigir seis dígitos si el productor omite el cero inicial. Conservar el nombre literal y el resultado de su interpretación. Seguir admitiendo los patrones anteriores acordados, como `20261014_CONT_ZC.csv`, mediante reglas explícitas, sin interpretar todos los prefijos con un único formato ambiguo.

Identificar la familia mediante un registro de patrones/sufijos autorizados para las nueve fuentes. Los nombres contienen guiones bajos dentro de la familia; no depender de una posición fija de fragmentos ni de que siempre exista una hora. Un patrón desconocido se registra para revisión, sin atribuirlo a otra familia por aproximación. Las fuentes excluidas siguen fuera aunque el productor también les genere nuevas versiones.

#### 3.5.2. Detección, estabilización e identidad de revisión

1. Detectar y estabilizar cada entrega seleccionada, identificarla y conservar su copia según 3.3 y 7.
2. Determinar por huella si su contenido ya fue observado, sin confundir una recepción repetida con una nueva operación de negocio.
3. Validar sus filas y comparar por las claves de cada contrato, tanto dentro del archivo como frente a Silver. Archivos distintos pueden contener filas iguales.
4. Insertar novedades y completar únicamente cambios permitidos, como cierres validados. Conservar contradicciones para revisión, sin reemplazar Silver por el último volcado.
5. Registrar el resultado también si no hay escritura. El registro de una recepción repetida no obliga a releer/escribir datos ya confirmados; un lote fallido o incierto debe reconciliarse antes de considerarlo procesado.

La identidad lógica de la fuente será planta/origen/familia, no su nombre fechado. Separar la recepción de un archivo, la revisión de contenido y la identidad de las filas de negocio. Registrar ruta/nombre originales, modalidad y patrón reconocidos, marca temporal declarada en el nombre cuando exista, metadatos de filesystem, huella de contenido, copia estable, lote y estado de proceso. Una huella no se compara globalmente entre familias u orígenes como si sus ID pertenecieran al mismo ámbito.

Metadatos como tamaño y modificación pueden ayudar a descubrir candidatos; ni ellos ni el nombre sustituyen la comprobación de contenido y estabilidad. El modo sin hora exige detectar revisiones de rutas ya conocidas; el modo versionado también debe detectar una modificación posterior de un archivo con nombre ya observado. No basar el checkpoint únicamente en el máximo nombre, máxima hora o ruta: puede llegar información atrasada, una copia repetida o el cierre de un registro antiguo.

Si cambia el nombre y el contenido es idéntico a una revisión ya confirmada del mismo ámbito, registrar la nueva recepción sin duplicar escrituras. Si se mantiene el nombre y cambia el contenido, registrar una revisión distinta y comparar todas las filas pertinentes conforme al contrato. Si el contenido cambia pero solo afecta a campos descartados o repite información de negocio, el resultado Silver puede ser `NO_CHANGE` y no generar commit. Una huella ya conocida con procesamiento fallido o incierto requiere recuperación/reconciliación, no omisión por haber sido vista.

En el modo sobrescrito, capturar una copia estable antes de parsear: un reintento utilizará esa copia y no el archivo vivo que puede cambiar diez minutos después. Si la entrada cambia mientras se captura, reintentar la estabilización según la política acordada. El nombre con hora no prueba que la escritura haya terminado: mantener los controles de entrega completa y estructura en ambas modalidades. No sobrescribir las copias ya aceptadas en Bronze al recibir otra versión de la misma ruta.

Un archivo con nombre nuevo **no implica que todas sus filas sean nuevas**, ni demuestra que sea un snapshot completo o un fichero solo de incrementos. Las nuevas entregas pueden contener novedades, repeticiones, cierres o revisiones de registros anteriores. Confirmar con el productor el alcance del contenido, pero mantener la deduplicación, no borrado por ausencia y control de actualizaciones para ambos casos; nunca concatenar íntegramente cada archivo a Silver ni sustituir la tabla por el último volcado.

La fecha/hora incorporada al nombre describe la revisión indicada por el productor; no sustituye a `FECHA`, `FECHA_ON` o `FECHA_OFF` de las filas, a la vigencia de una producción/ramal ni al periodo de diez minutos confirmado en 17.6. Una revisión recibida a las 09:20 puede contener un cierre que pertenece a una producción iniciada días antes.

#### 3.5.3. Tablas persistentes, operación y comprobaciones

**Una tabla Delta lógica se crea una vez y acumula el histórico de su familia**, consolidando orígenes con su ámbito técnico o separándolos físicamente cuando se justifique. No se crea otra tabla cada diez minutos, día, semana o producción. La ausencia de un registro en una entrega no constituye una baja ni un cierre.

Los commits se producirán cuando existan cambios válidos, no por el simple transcurso de diez minutos. Los archivos Parquet internos, las particiones y la compactación son decisiones físicas distintas de la identidad de la tabla. Elegirlas a partir del volumen y de las consultas reales; evitar miles de tablas pequeñas por entrega o producción. La sección 10 establece su mantenimiento independiente.

La consulta habitual será localizar fabricaciones anteriores de un modelo y compararlas con una actual o reciente. El catálogo resuelve qué producciones y fechas solicitar; los filtros y las salidas de consulta resuelven su acceso eficiente sin fragmentar el histórico de origen por cada fabricación.

Incluir antes de aceptar el detector de entrada estas comprobaciones automatizadas:

- Dos nombres versionados con el mismo contenido: dos recepciones trazables, ningún duplicado o commit de negocio adicional tras la primera confirmación.
- Dos nombres versionados con registros repetidos y nuevos: solo las novedades y actualizaciones permitidas producen cambios.
- Mismo nombre sin hora con contenido modificado: nueva revisión detectada, con inserciones/cierres validados sin reemplazo de Silver.
- Mismo nombre sin hora y contenido sin cambios: comprobación registrada, sin escritura innecesaria.
- Archivo ya fechado que se modifica, llegada desordenada y cierre antiguo: detección sin depender del máximo timestamp del nombre ni sobrescribir cambios más recientes aceptados.
- Mezcla de modalidades entre líneas/familias o cambio de modalidad: mismas identidades de negocio y mismo histórico, sin reinicialización.
- Sobrescritura durante captura y reintento tras un fallo: entrada estable fijada para el lote y recuperación idempotente sin leer otra versión por accidente.
- Familia sin cambios mientras otras se actualizan: no inventar una entrega, filas, bajas o cierres; controlar la actualidad de cada fuente separadamente.

## 4. Contrato de datos por tabla

Antes de la primera carga, documentar para cada tabla:

1. Esquema, tipos, unidades y significado de cada campo.
2. Clave de identidad del registro y su ámbito de unicidad.
3. Campos que pueden modificarse y precedencia entre versiones recibidas.
4. Fechas de evento/vigencia y fechas de recepción.
5. Tratamiento de nulos, cadenas vacías, ceros y valores desconocidos.
6. Reglas de validación, deduplicación y rechazo.
7. Procedencia y versión del esquema de origen.

Metadatos propuestos: origen/Fenix, planta cuando esté identificada, archivo de origen, lote de ingesta, fecha de primera recepción, fecha de última modificación observada y huella del contenido normalizado.

Las claves de los MP9 y catálogos incluirán el origen junto al ID/código cuando su unicidad no sea global. Las claves de las listas y contadores se definirán tras comprobar su grano real; no se inventará una clave definitiva sin validación.

Un hash de todos los valores ayuda a detectar cambios, pero **no sustituye a la clave de identidad**: una corrección no debe convertirse por ello en otro evento.

Las fechas se normalizarán antes de comparar registros. En los datos analizados hay valores equivalentes como `7:10:00` y `07:10:00`. Se acordará la zona horaria de cada origen y el tratamiento de cambios de horario, sin inferirla únicamente del nombre del archivo.

### 4.1. Contrato acordado: `SIP_MODELO` de Aiala

Estado: decisiones de negocio acordadas e implementación inicial de ensayo completada en `Ingesta_Delta_Lake`, con pruebas automatizadas y conciliación local de los diez archivos Aiala. Operación productiva, CI remota y despliegue pendientes. Versión inicial del contrato: `sip_modelo_aiala_v1`. Evidencia y límites en la ficha de entrega 01 indicada en 17.12.

#### 4.1.1. Significado y evidencia de origen

`SIP_MODELO` es un diccionario que permite traducir el ID interno de un modelo en Fenix al código del modelo de botella. Según el conocimiento operativo del usuario, el alta de un modelo puede acompañar su entrada efectiva o próxima en fabricación; no descartar esa señal. Sin embargo, la pareja ID–código no proporciona por sí sola el identificador ni las fechas de cada fabricación repetida. El catálogo temporal se construirá con `MP9_CAMBIO_MODELO` conforme a la sección 16; queda pendiente contrastar si las fechas operativas coinciden exactamente con el cambio físico.

Se ha contrastado la copia real ubicada en `C:/Users/iker.villen/Desktop/AI_tool/Datos/1.App_septiembre/Fenix_Aiala/Fenix`: carpetas `AV21`, `AV22`, `AV23`, `AV31` y `AV32`, con los archivos `20261013_SIP_MODELO.csv` y `20261014_SIP_MODELO.csv` de cada carpeta. Los diez archivos contienen las mismas 2.309 asociaciones distintas al considerar únicamente ID y modelo. No se ha encontrado un ID asociado a modelos diferentes ni un modelo asociado a varios ID. En todos ellos, por ejemplo, `754` corresponde a `3081/360`.

Los CSV analizados no tienen cabecera y usan `;` como separador. El orden de campos, documentado en `Fenix tablas.xlsx`, hoja «Datos Maestros 1», es `ID`, `CODIGO`, `ID_BOCMOD_ESTADO`. El separador final deja una posición vacía adicional; no constituye otro campo de negocio.

El tercer campo, `ID_BOCMOD_ESTADO`, queda excluido del diccionario, del hash de negocio y de la comparación de asociaciones. Sus variaciones no generan actualizaciones ni conflictos de modelos. No es necesario resolver su significado o precedencia temporal para este contrato.

Esta comprobación acredita la correspondencia en la muestra disponible; la ingesta deberá repetirla con cada nueva revisión estable de entrada. No se presupone unicidad de estos ID en otras plantas.

#### 4.1.2. Esquema, tipado e identidad

La tabla Delta del diccionario tendrá estos dos campos de negocio:

| Campo Delta | Campo Fenix | Tipo Delta / Arrow | Nulos | Validación |
| --- | --- | --- | --- | --- |
| `id_modelo_fenix` | `ID` | `BIGINT` / `int64` | No | Representación entera decimal, convertible sin pérdida y dentro del rango de 64 bits con signo. No aceptar valores vacíos, fraccionarios ni conversiones mediante coma flotante. |
| `codigo_modelo_botella` | `CODIGO` | `STRING` / `string` | No | Texto no vacío, preservando su contenido, ceros iniciales, letras y separadores. No convertirlo a número. |

Aunque muchos códigos parecen seguir `XXXX/XXX`, no se impondrá ese patrón: la muestra contiene, por ejemplo, `1961/27A`. No eliminar `/`, cambiar mayúsculas o suprimir ceros iniciales. Una normalización adicional requerirá una decisión explícita y versionada; no aplicarla silenciosamente al comparar asociaciones.

La clave de negocio será `id_modelo_fenix` dentro de la tabla del catálogo de Aiala. El ámbito de planta debe quedar identificado en la configuración y en la trazabilidad. Si en el futuro se reúnen varias plantas en una tabla, habrá que incluir su ámbito de catálogo en la clave antes de hacerlo; no mezclar ID de diferentes plantas automáticamente.

La procedencia por línea no forma parte de la identidad del modelo: se leerán los archivos de cada línea y se consolidará un único diccionario compartido. La relación `754 → 3081/360` se almacenará una sola vez, aunque llegue desde varias carpetas o se repita dentro del mismo CSV.

Archivo y carpeta de origen, huella de la revisión estable, número de fila, lote, versión del contrato y momento de recepción se conservarán en el control/auditoría persistente. La recepción no equivale a una fecha efectiva del modelo. No actualizar la tabla del diccionario para mantener metadatos de cada observación repetida.

#### 4.1.3. Comprobaciones y decisiones por asociación

Cada ejecución leerá una revisión estable conforme a la sección 7, validará estructura y tipos y agrupará las asociaciones antes de escribir. Comparará tanto los archivos del lote entre sí como sus candidatos con el diccionario ya publicado. También comprobará la asociación inversa modelo–ID: un código que aparezca con un ID distinto requerirá revisión antes de aceptarse, sin deducir automáticamente que ambos identifican la misma entidad.

| Situación | Resultado | Tratamiento |
| --- | --- | --- |
| Misma pareja ID–modelo repetida en un archivo, entre líneas, entre fechas o en un reintento | Duplicado idéntico / `NO_CHANGE` | Consolidar; no crear modelos adicionales ni escribir otra versión de la asociación. |
| ID existente asociado al mismo código | `NO_CHANGE` | Mantener la fila y su primera recepción; no ejecutar una actualización. |
| ID nuevo con código válido que no contradice otra asociación | `INSERT` | Insertar una sola asociación, con intención y confirmación auditadas. |
| Mismo ID con códigos diferentes, dentro del lote o frente al diccionario | `CONFLICT` | Conservar la asociación publicada, si existe; apartar los candidatos de esa clave y registrar la discrepancia para revisión. Si la clave es nueva y ambigua, no insertar ninguna de sus alternativas. |
| Mismo código de botella con ID distintos | `CONFLICT` para revisión | Registrar los ID y sus procedencias; no sustituir ni incorporar la asociación contradictoria automáticamente. |
| Campo obligatorio vacío, ID no convertible sin pérdida o estructura incompatible | `INVALID` | Apartar con motivo y procedencia; no fabricar un valor ni escribir una asociación parcial. |
| Cambia únicamente `ID_BOCMOD_ESTADO` | `NO_CHANGE` | Ignorar el cambio para este diccionario. |
| Una asociación deja de aparecer en un archivo posterior | Sin baja automática | Conservarla; la ausencia no autoriza borrado. |

No usar el orden físico de las filas, el archivo más reciente, la mayoría de repeticiones o la última llegada para resolver contradicciones. Los registros afectados quedarán identificados y separados; cualquier publicación de asociaciones independientes y válidas respetará las unidades de escritura y las reglas de lotes parciales de la sección 8, sin declarar completo un lote con incidencias pendientes.

La corrección de una asociación existente queda fuera de la actualización automática normal de esta tabla. Solo podrá hacerse tras revisión, con decisión explícita sobre la identidad y sus referencias, operación de corrección identificada y auditoría del valor anterior y nuevo. La ingesta no sobrescribirá una correspondencia aceptada para resolver el conflicto por sí sola.

#### 4.1.4. No escritura cuando no hay asociaciones nuevas

En este contrato, «nuevo» significa una nueva asociación validada, no una nueva columna. No habilitar evolución automática de esquema por recibir un CSV distinto; cualquier ampliación de campos requiere revisión y nueva versión del contrato.

Antes de invocar una escritura Delta, se determinará el conjunto de inserciones válidas contra el snapshot actual, bajo el modelo de único escritor de este plan. Si ese conjunto está vacío, el resultado de la operación será `NO_CHANGE`: no ejecutar `MERGE`, `UPDATE`, `APPEND` ni `OVERWRITE`, no reescribir archivos Parquet y no generar un commit Delta para registrar que el catálogo se ha vuelto a observar. Las tareas de mantenimiento, si se autorizan, serán operaciones separadas de esta ingesta.

Cuando sí existan asociaciones nuevas, la operación será únicamente de inserción por clave, sin rama de actualización de coincidencias, tras resolver duplicados y apartar contradicciones. No reconstruir la tabla entera a partir del último CSV ni reemplazar los archivos fuente. Un reintento verificará lo ya publicado y no volverá a insertar asociaciones confirmadas.

La ausencia de escritura en el diccionario no elimina la obligación de control: se registrarán archivo/revisión procesados, snapshot consultado, resultado, asociaciones nuevas, repeticiones, conflictos y registros inválidos. Si hay conflictos o inválidos y ninguna inserción, se registrarán esas incidencias; `NO_CHANGE` en Delta no significa que la validación haya sido satisfactoria. No inventar una versión de commit para una operación que no ha escrito.

El hash de negocio se calculará exclusivamente sobre los dos campos conservados conforme a la versión del contrato; la clave seguirá siendo el ID en su ámbito. Ni el estado descartado ni la hora de recepción, procedencia o número de intento producirán cambios de negocio.

#### 4.1.5. Criterios de aceptación para la implementación

- La primera carga controlada de la muestra contrastada produce exactamente 2.309 asociaciones compartidas, sin duplicarlas por línea o por repetición del catálogo.
- Reprocesar los mismos diez archivos no cambia el contenido, la primera recepción ni la versión Delta del diccionario; sí confirma el resultado en el control operativo.
- Un archivo modificado solo en el estado no genera escrituras en el diccionario.
- Incorporar una asociación nueva válida produce una única inserción; repetir la operación no produce otra.
- Un mismo ID con otro modelo, o un modelo con otro ID, conserva las asociaciones publicadas y deja una incidencia persistente revisable.
- ID inválidos, códigos vacíos y estructuras incompatibles quedan apartados; no se publican valores truncados, inferidos o convertidos con pérdida.
- La desaparición de un modelo de un volcado no lo borra del diccionario.
- Un fallo después de un commit y antes de su confirmación se reconcilia según la sección 8; el reintento no duplica la asociación ni su contabilización.

### 4.2. Contrato acordado: `SIP_DEFECTO` de Aiala

Estado: implementación local de ensayo completada en la entrega 02; 378 entradas válidas y 86 excluidas verificadas con los diez CSV reales. Evidencia y límites en su ficha de 17.12. Versión inicial del contrato: `sip_defecto_aiala_v1`.

#### 4.2.1. Significado y evidencia de origen

`SIP_DEFECTO` es el catálogo de códigos y descripciones de los defectos o motivos de avisos/rechazos. No contiene eventos, cantidades ni fechas de ocurrencia. Se distingue el ID interno de la entrada del código de aviso/rechazo: `LISTA_AR.COD_AR` referencia `SIP_DEFECTO.CODIGO`, no su campo `ID`.

Ejemplo real: la entrada `ID = 1`, `CODIGO = 400`, descripción larga `0400:Columpio` y descripción corta `Columpio` permite interpretar un evento con `COD_AR = 400`. No deducir el código a partir del prefijo visible en la descripción; el campo autoritativo para esta relación es `CODIGO`.

Se han comparado los archivos `20261013_SIP_DEFECTO.csv` y `20261014_SIP_DEFECTO.csv` de `AV21`, `AV22`, `AV23`, `AV31` y `AV32`, bajo `C:/Users/iker.villen/Desktop/AI_tool/Datos/1.App_septiembre/Fenix_Aiala/Fenix`. Los diez contienen las mismas 464 entradas distintas, repetidas varias veces por archivo. Antes del filtrado hay 417 códigos distintos: los códigos `0`, `551` y `552` se repiten entre ID diferentes.

Al excluir las entradas «NO USAR» según la regla de 4.2.3 quedan 378 entradas válidas, con 378 ID y 378 códigos distintos; se excluyen 86 entradas. Por ejemplo, para el código `551` se descarta la entrada `ID = 407`, con descripción larga `99501:NO USAR`, y se conserva `ID = 238`, con descripción `551: Gfa espessura 0.5mm`. No se elige entre duplicados por orden o mayoría: la exclusión de negocio se aplica antes de comprobar la unicidad.

El CSV no tiene cabecera; sus campos son `ID`, `CODIGO`, `DESCRIPCION`, `DESCRIPCION_CORTA`, `MEDIDA`, en ese orden, documentados en `Fenix tablas.xlsx`, hoja «Datos Maestros 1». El separador es `;` y la posición vacía después del separador final no es otra columna. La muestra se ha leído correctamente con una decodificación compatible con Windows-1252; la implementación fijará explícitamente la codificación del origen y no sustituirá caracteres inválidos silenciosamente.

`MEDIDA` queda excluido de la tabla Delta, del hash de negocio y de las comprobaciones de contenido: sus cambios no generan escrituras ni conflictos. La estructura del archivo sí se valida, sin desplazar columnas por ignorar ese campo.

#### 4.2.2. Esquema, identidad y relación con los eventos

La tabla Delta compartida de Aiala conservará cuatro campos de negocio:

| Campo Delta | Campo Fenix | Tipo Delta / Arrow | Nulos | Validación |
| --- | --- | --- | --- | --- |
| `id_defecto_fenix` | `ID` | `BIGINT` / `int64` | No | Entero decimal convertible sin pérdida, dentro del rango de 64 bits con signo. No aceptar vacíos, fracciones ni conversiones mediante coma flotante. |
| `codigo_aviso_rechazo` | `CODIGO` | `BIGINT` / `int64` | No | Entero decimal con las mismas garantías; no sustituirlo por el ID ni extraerlo de una descripción. |
| `descripcion` | `DESCRIPCION` | `STRING` / `string` | No | Texto no vacío después de retirar espacios de los extremos; conservar acentos, prefijos e información restante. |
| `descripcion_corta` | `DESCRIPCION_CORTA` | `STRING` / `string` | No | Texto no vacío después de retirar espacios de los extremos; conservar su contenido. |

La normalización de espacios en los extremos de las descripciones será explícita y determinista. No cambiar mayúsculas, acentos o palabras del texto conservado ni derivar una descripción de la otra para completar vacíos; la comparación del marcador «NO USAR» tiene su propia regla, descrita debajo. Conservar en la evidencia de origen los valores anteriores a esta normalización.

La clave de identidad será `id_defecto_fenix` en el catálogo de Aiala, y se exigirá además unicidad de `codigo_aviso_rechazo` dentro de ese mismo catálogo aceptado. Cada ID debe corresponder a un código y cada código a una sola entrada con sus descripciones. Estas propiedades se comprobarán en la ingesta; no se presupone que Delta imponga una clave primaria o una restricción de unicidad por sí solo.

La carpeta de línea no forma parte de la identidad: se consolidará un catálogo compartido, conservando procedencia por archivo, línea, revisión, fila y lote en control/auditoría. Si se reúnen otras plantas, sus ámbitos deberán distinguirse antes de mezclar ID o códigos.

**Una descripción no es una clave.** Distintos códigos pueden compartir descripción corta o larga sin que exista un conflicto de identidad. Los códigos `41` y `42`, por ejemplo, comparten la descripción corta `Cal.interior boca` y se mantienen separados. No fusionar, sustituir ni agrupar entradas de catálogo por igualdad o similitud de texto. La traducción desde `LISTA_AR` se hará por el código y el ámbito, nunca por la descripción.

#### 4.2.3. Exclusión de entradas «NO USAR»

Se excluirá una entrada si cualquiera de sus dos descripciones contiene el marcador completo «NO USAR» como valor del campo o como texto después de un prefijo de código separado por `:`. Para reconocer el marcador se retirarán espacios de los extremos y se comparará sin distinguir mayúsculas/minúsculas. Así, tanto `NO USAR` como `99501:NO USAR` o `99501: NO USAR` se reconocen como entradas excluidas.

No emplear una búsqueda libre de la subcadena «NO USAR» en frases más largas ni interpretar otros textos como equivalentes. La regla se aplica sobre las descripciones, no sobre una lista fija de ID o códigos: el código `0` queda fuera en la muestra porque sus entradas cumplen el marcador, no porque se haya declarado una prohibición general del número cero.

La exclusión se aplicará después de comprobar que la fila puede interpretarse estructuralmente y antes de construir el catálogo candidato y validar las relaciones ID–código. Una fila excluida no compite con otra válida del mismo código. Las filas excluidas no se insertarán en el diccionario operativo; su clasificación, motivo y procedencia quedarán registrados en control/auditoría, separados de los registros inválidos y de los conflictos.

Si en una entrada futura un ID/código previamente aceptado aparece marcado «NO USAR», se excluirá esa observación y quedará registrada, sin borrar o cambiar automáticamente la asociación publicada. Cualquier baja o reinterpretación posterior del catálogo aceptado requerirá una decisión explícita y auditable.

#### 4.2.4. Duplicados, novedades y conflictos

Sobre las entradas no excluidas se comprobarán tipos, contenido, relaciones ID–código y coincidencia con el catálogo publicado. La comparación utilizará los cuatro campos conservados y la normalización acordada, sin `MEDIDA` ni metadatos operativos.

| Situación | Resultado | Tratamiento |
| --- | --- | --- |
| Misma entrada repetida dentro de un CSV, entre líneas/fechas o en un reintento | Duplicado idéntico / `NO_CHANGE` | Consolidar; conservar una entrada, sin escritura adicional. |
| ID y código existentes con ambas descripciones iguales | `NO_CHANGE` | Mantener la entrada y su primera recepción; registrar la observación en control. |
| ID y código nuevos, válidos y sin contradicciones | `INSERT` | Insertar una sola entrada y auditar su publicación. |
| Mismo ID con códigos distintos | `CONFLICT` | Apartar los candidatos afectados y conservar la asociación publicada, si existe. |
| Mismo código con ID distintos, después de excluir «NO USAR» | `CONFLICT` | No elegir un ID arbitrariamente ni publicar duplicidad; registrar para revisión. |
| Misma pareja ID–código con alguna descripción diferente | `CONFLICT` para revisión | No sobrescribir automáticamente el texto aceptado; registrar valores anterior/candidato y procedencia. |
| Descripción coincidente entre ID y códigos diferentes | Entradas independientes | No fusionar; comprobar cada entrada por sus identificadores. |
| Cambia únicamente `MEDIDA` | `NO_CHANGE` | Ignorar para este diccionario. |
| Campo obligatorio inválido o estructura incompatible | `INVALID` | Apartar con motivo, valor original y procedencia; no completar datos mediante inferencias. |
| Una entrada desaparece de un volcado posterior | Sin baja automática | Conservar la entrada publicada. |

No resolver discrepancias por orden físico, fecha del nombre del archivo, última recepción o número de repeticiones. No dejar entrar una candidata que incumpla cualquiera de las dos unicidades: un nuevo ID con un código ya asignado también es contradictorio. Las claves afectadas se apartarán y las asociaciones válidas independientes se tratarán conforme a las unidades de escritura y recuperación de la sección 8; un lote con incidencias pendientes no se declarará completo.

Una corrección revisada de identificadores o descripciones será una operación explícita, con evidencia, decisión y auditoría de los valores anteriores y nuevos, considerando sus referencias históricas. No será un efecto automático de recibir otro CSV.

#### 4.2.5. Escritura, idempotencia y control

Antes de invocar una escritura Delta, bajo el modelo de único escritor, se calculará el conjunto de nuevas entradas válidas frente al snapshot publicado. Si está vacío, no ejecutar una operación de modificación ni generar un commit en el diccionario: no reescribir Parquet, no actualizar `last_seen` en las filas y no sustituir la tabla por el último CSV. Un lote con todas sus filas repetidas o excluidas tampoco obliga a escribir una tabla vacía. La creación inicial solo se hará en la carga controlada prevista.

Si existen novedades válidas, insertar por clave sin rama de actualización de coincidencias, después de comprobar también la unicidad del código. Un reintento verificará lo ya confirmado antes de insertar; el resultado será idéntico al de una sola ejecución satisfactoria. El hash de negocio incluirá los cuatro campos conservados; recepción, carpeta, número de intento y `MEDIDA` no formarán parte de él.

El control operativo persistente registrará archivo/revisión, contrato, snapshot consultado, filas recibidas, repeticiones consolidadas, entradas excluidas, inserciones, coincidencias, conflictos e inválidos. Distinguir contadores de filas de origen de contadores de entradas únicas para no confundir las repeticiones de los volcados con nuevos defectos. Mantener trazabilidad de los candidatos apartados y no inventar una versión Delta cuando no se haya escrito.

Las exclusiones acordadas son resultados esperados del filtrado. Los conflictos e inválidos son incidencias: la ausencia de escritura en Delta no permite ocultarlos como una validación satisfactoria. La confirmación de inserciones y los commits inciertos se gestionarán mediante las identidades de operación y la reconciliación de la sección 8, sin duplicar entradas ni contabilizaciones.

#### 4.2.6. Criterios de aceptación para la implementación

- La primera carga de la muestra contrastada consolida un catálogo de 378 entradas válidas, con 378 ID y 378 códigos; reconoce 86 entradas distintas excluidas por «NO USAR» antes de contar sus repeticiones físicas.
- Las entradas `ID = 407, CODIGO = 551` e `ID = 408, CODIGO = 552`, marcadas «NO USAR», no se publican; se conservan las válidas `ID = 238, CODIGO = 551` e `ID = 239, CODIGO = 552`.
- Los códigos `41` y `42` permanecen separados aunque compartan descripción corta. No se impone unicidad al texto de las descripciones.
- Una exclusión se reconoce desde cualquiera de las dos descripciones, incluido un marcador con prefijo de código; una frase más larga no se descarta por una simple coincidencia de subcadena.
- Reprocesar los archivos no cambia contenido, primera recepción ni versión Delta; sí registra el resultado operativo. Cambiar solo `MEDIDA` tampoco produce escritura.
- Una entrada nueva válida se inserta una vez. La misma entrada repetida entre líneas o después de un fallo no genera nuevos defectos.
- Un ID reutilizado con otro código, un código reutilizado con otro ID o una descripción modificada deja evidencia revisable y no sobrescribe lo publicado.
- Vacíos obligatorios, enteros fuera de rango y estructuras incompatibles quedan apartados, sin conversiones con pérdida ni descripciones inventadas.
- Ausencias posteriores o nuevas observaciones «NO USAR» no borran entradas aceptadas automáticamente.
- La recuperación de un commit confirmado o incierto mantiene las mismas garantías de idempotencia y auditoría que la sección 8.

### 4.3. Contrato acordado: `SIP_DEFECTO_STATUS` de Aiala

Estado: implementación local de ensayo completada en la entrega 03; ocho entradas verificadas con los diez CSV reales, replay sin escritura y recuperación probada. Evidencia y límites en su ficha de 17.12. Versión inicial del contrato: `sip_defecto_status_aiala_v1`. La equivalencia semántica de los valores de `ORIGEN` y `ZONA` sigue pendiente de confirmación, sin impedir conservarlos.

#### 4.3.1. Significado y evidencia de origen

`SIP_DEFECTO_STATUS` es el diccionario de procedencias de los avisos/rechazos: tiempo real, muestreo IFA, muestreo IFL, Delfoz y otros orígenes descritos por Fenix. La relación documentada es `LISTA_AR.STATUS → SIP_DEFECTO_STATUS.CODIGO`. `STATUS` no representa gravedad, estado abierto/cerrado ni tipo de evento; este último se interpreta mediante `LISTA_AR.TIPO` y su catálogo independiente.

Ejemplo real: `CODIGO = 2` se describe como `Muestreo IFA`, abreviatura `MF`. Un registro de `LISTA_AR` con `STATUS = 2` se interpreta con esa entrada. Aunque en la muestra coinciden los valores de `ID` y `CODIGO`, son campos diferentes y se conservarán por separado; la unión con los eventos se hará por `CODIGO`, no por esa coincidencia accidental.

Se han comparado los archivos `20261013_SIP_DEFECTO_STATUS.csv` y `20261014_SIP_DEFECTO_STATUS.csv` de `AV21`, `AV22`, `AV23`, `AV31` y `AV32`, bajo `C:/Users/iker.villen/Desktop/AI_tool/Datos/1.App_septiembre/Fenix_Aiala/Fenix`. Los diez contienen las mismas ocho entradas distintas, repetidas por archivo, sin asociaciones contradictorias y con ocho ID y ocho códigos distintos. Se consolidará un único diccionario para Aiala, sin multiplicar sus entradas por línea.

El CSV no tiene cabecera. Los campos documentados en `Fenix tablas.xlsx`, hoja «Datos Maestros 1», son `ID`, `CODIGO`, `DESCRIPCION`, `ORIGEN`, `DESCRIPCION_CORTA`, `ZONA` y `ACTIVO`, en ese orden. El separador es `;`; la posición vacía después del separador final no constituye otro campo de negocio.

Se conservarán los nombres originales de Fenix, incluidos `Delfoz ZC` y `Delfoz ZF`. La documentación Excel describe el código `4` de otra manera, pero no se usará para sustituir automáticamente el texto del CSV real. Esta discrepancia queda documentada como observación de referencia, no como motivo para alterar la entrada.

#### 4.3.2. Campos conservados, tipado y significado pendiente

| Campo Delta | Campo Fenix | Tipo Delta / Arrow | Nulos | Validación |
| --- | --- | --- | --- | --- |
| `id_status_fenix` | `ID` | `BIGINT` / `int64` | No | Entero decimal convertible sin pérdida, dentro del rango de 64 bits con signo; no aceptar vacíos, fracciones o conversiones mediante coma flotante. |
| `codigo_status` | `CODIGO` | `BIGINT` / `int64` | No | Entero decimal con las mismas garantías. Es el código que interpreta `LISTA_AR.STATUS`. |
| `descripcion` | `DESCRIPCION` | `STRING` / `string` | No | Texto no vacío tras retirar espacios de los extremos; preservar palabras, acentos y mayúsculas originales. |
| `descripcion_corta` | `DESCRIPCION_CORTA` | `STRING` / `string` | No | Texto no vacío tras retirar espacios de los extremos; preservar la abreviatura original. |
| `codigo_origen` | `ORIGEN` | `BIGINT` / `int64` | No | Conservar el código entero original. La muestra contiene `0` y `1`; no convertirlo a booleano ni asignarle etiquetas aún. |
| `codigo_zona` | `ZONA` | `BIGINT` / `int64` | No | Conservar el código entero original. La muestra contiene `0`, `1` y `2`; no deducir automáticamente sus equivalencias. |

`ORIGEN` se mantiene porque puede permitir distinguir procedencia de máquina o persona; esa interpretación es una hipótesis del negocio que deberá confirmarse antes de aplicar filtros con dichas etiquetas. `ZONA` también se mantiene para permitir su interpretación posterior. No confundir `codigo_origen`, que es un valor del registro Fenix, con la procedencia técnica del archivo o la identidad de planta.

Los valores observados no constituirán una lista cerrada de códigos permitidos: un entero nuevo en una entrada nueva puede conservarse sin inventar su significado. Si aparece en una entrada ya aceptada y altera su contenido, se tratará como cambio pendiente de revisión conforme a 4.3.4. La ausencia o el incumplimiento del tipo de un campo conservado se registrará como `INVALID`; no imputar cero ni derivar el valor a partir del nombre del origen.

`ACTIVO` queda excluido del esquema Delta, del hash y de las comparaciones de contenido. No filtrar filas por ese valor. En particular, se conservará la entrada `Muestreos ZC`, cuyo `ACTIVO` está vacío. Ignorar el campo no permite desplazar columnas ni omitir la validación estructural del CSV.

La normalización del texto se limitará a espacios de los extremos, será determinista y conservará el valor original en la evidencia de entrada. No traducir, corregir o fusionar nombres ni completar descripciones a partir de la otra columna.

#### 4.3.3. Identidad y validación en cada revisión de entrada

La clave de negocio será `id_status_fenix` en el catálogo de Aiala; se exigirá también unicidad de `codigo_status` dentro del catálogo aceptado. Cada ID corresponderá a un código, y cada código a una entrada única con sus descripciones y códigos de origen/zona. No usar las descripciones como claves ni fusionar entradas por igualdad o similitud de texto. Las unicidades las comprobará la ingesta, bajo el modelo de único escritor, sin presumir que Delta las imponga por sí solo.

La carpeta de línea no forma parte de la identidad. La planta/ámbito debe quedar identificado en la configuración y en la trazabilidad; una futura integración de otras plantas deberá distinguir sus catálogos antes de reunir los ID o códigos.

Con cada archivo nuevo o revisión estable de un archivo actualizado se validarán estructura, tipos y campos obligatorios, se consolidarán repeticiones y se compararán los candidatos entre sí y contra el snapshot publicado. Reprocesar el archivo completo no autoriza un reemplazo del catálogo. El descriptor de lote fijará la revisión estable conforme a la sección 7 para que un reintento no lea otra versión de un CSV vivo.

«Novedades» significa nuevas entradas válidas del diccionario. Una nueva columna, un cambio de orden o una estructura incompatible no activa evolución automática de esquema: debe registrarse la incompatibilidad para revisión y versionar el contrato antes de aceptarla.

#### 4.3.4. Duplicados, conflictos y cambios

| Situación | Resultado | Tratamiento |
| --- | --- | --- |
| Misma entrada repetida dentro de un CSV, entre líneas/fechas o en un reintento | Duplicado idéntico / `NO_CHANGE` | Consolidar; no crear otra entrada ni modificar la publicada. |
| ID y código existentes con los seis campos conservados iguales | `NO_CHANGE` | Mantener contenido y primera recepción; registrar la observación en control. |
| ID y código nuevos con contenido válido y sin contradicciones | `INSERT` | Insertar una sola entrada con intención y confirmación auditadas. |
| Mismo ID con códigos distintos | `CONFLICT` | Apartar los candidatos afectados; conservar la asociación publicada, si existe. |
| Mismo código con ID distintos | `CONFLICT` | No publicar varias entradas para ese código ni elegir por orden; registrar para revisión. |
| Misma pareja ID–código con cambio de descripción, abreviatura, `ORIGEN` o `ZONA` | `CONFLICT` para revisión | Mantener el contenido publicado y registrar valores anterior/candidato, campos modificados y procedencia. |
| Distintos ID y códigos con descripciones coincidentes | Entradas independientes | No fusionar; validar cada entrada mediante sus identificadores. |
| Cambia únicamente `ACTIVO`, incluido un valor vacío | `NO_CHANGE` | Ignorar el cambio para este diccionario. |
| Campo conservado inválido o estructura incompatible | `INVALID` | Apartar con motivo y evidencia; no fabricar valores ni aceptar una fila incompleta. |
| Una entrada desaparece de un volcado posterior | Sin baja automática | Conservarla; la ausencia no demuestra una baja. |

Las discrepancias no se resolverán con la última fila, la fecha del nombre del archivo, la última recepción o la mayoría de repeticiones. Si una clave nueva es ambigua dentro del lote, no se insertará ninguna alternativa hasta su revisión. Las asociaciones válidas independientes se tratarán según las unidades de escritura y recuperación de la sección 8; un lote con incidencias pendientes no se declarará completo.

Una modificación revisada requerirá decisión explícita y una operación de corrección auditable, con valores anterior/nuevo y consideración de sus referencias históricas. La llegada de un CSV no sobrescribirá automáticamente etiquetas o códigos aceptados.

#### 4.3.5. Escritura, ausencia de novedades y auditoría

Antes de escribir se calculará el conjunto de entradas nuevas válidas contra el snapshot actual. Si ese conjunto está vacío, la operación sobre el diccionario será `NO_CHANGE`: no ejecutar `MERGE`, `UPDATE`, `APPEND` ni `OVERWRITE`, no reescribir Parquet y no crear un commit Delta para reflejar una observación repetida. La creación inicial se hará únicamente en la carga controlada; el mantenimiento del almacenamiento, si procede, será una operación separada.

Cuando existan novedades válidas, insertar por clave, sin rama de actualización de coincidencias, después de comprobar también la unicidad del código. No sustituir la tabla por el último archivo recibido ni modificar archivos fuente. Los reintentos y resultados de commit inciertos se reconciliarán conforme a la sección 8, sin duplicar entradas ni contabilizaciones.

El hash de negocio incluirá exactamente los seis campos conservados, con la normalización y versión de contrato acordadas. No incluir `ACTIVO`, recepción, archivo, carpeta o número de intento; el hash no sustituye a la clave.

El control/auditoría persistente conservará revisión y huella de entrada, carpeta/línea, número de fila, lote, versión del contrato, snapshot consultado, resultado y contadores de filas recibidas, repeticiones, entradas nuevas, coincidencias, conflictos e inválidos. En los conflictos registrará valores existentes y candidatos, campos discrepantes y estado pendiente de revisión. No actualizar filas Delta por `last_seen`; la primera recepción de una entrada aceptada permanecerá inmutable.

Aunque no haya escritura Delta, debe registrarse que el archivo se ha comprobado y qué resultado produjo. Una operación sin inserciones pero con discrepancias no se informará como validación satisfactoria; no inventar una versión de commit si no se ha escrito.

#### 4.3.6. Criterios de aceptación para la implementación

- La primera carga de la muestra contrastada produce ocho entradas compartidas para Aiala, sin duplicarlas por línea, fecha o repetición física.
- Se conserva la relación del código `2` con `Muestreo IFA` y los nombres originales `Delfoz ZC` y `Delfoz ZF`, sin sustituirlos por etiquetas del Excel.
- Se conservan `ORIGEN` y `ZONA` como enteros originales, sin convertirlos a categorías o booleanos con equivalencias no confirmadas.
- `Muestreos ZC` se publica aunque `ACTIVO` esté vacío; cambiar solo `ACTIVO` no produce escrituras.
- Reprocesar los diez archivos mantiene contenido, primera recepción y versión Delta; sí registra el resultado en control.
- Una nueva entrada válida se inserta una sola vez; repetirla entre líneas o reintentar después de un fallo no genera otra.
- Reutilizar un ID con otro código, reutilizar un código con otro ID o cambiar un campo conservado deja evidencia revisable sin sobrescribir la entrada aceptada.
- Campos conservados vacíos, enteros fuera de rango y estructuras incompatibles quedan apartados; no se genera automáticamente un esquema nuevo.
- Descripciones iguales no fusionan códigos, y la desaparición de una entrada no la borra.
- La reconciliación de commits inciertos conserva las garantías de idempotencia y auditoría de la sección 8.

### 4.4. Contrato acordado: `SIP_DEFECTO_AR` de Aiala

Estado: implementación local de ensayo completada en la entrega 04; nueve entradas verificadas con los diez CSV reales, replay sin escritura y recuperación probada. Evidencia y límites en su ficha de 17.12. Versión inicial del contrato: `sip_defecto_ar_aiala_v1`.

#### 4.4.1. Significado y evidencia de origen

`SIP_DEFECTO_AR` es el catálogo de tipos de avisos/rechazos: rechazo ZF, aviso ZC/ZF, anotación, rechazo de trampilla y otros tipos descritos por Fenix. La relación documentada es `LISTA_AR.TIPO → SIP_DEFECTO_AR.CODIGO`. El tipo se distingue del defecto concreto (`COD_AR`, interpretado por `SIP_DEFECTO`) y de su procedencia (`STATUS`, interpretado por `SIP_DEFECTO_STATUS`). Esta tabla no registra sucesos, fechas ni cantidades de producción.

Se han comparado los archivos `20261013_SIP_DEFECTO_AR.csv` y `20261014_SIP_DEFECTO_AR.csv` de `AV21`, `AV22`, `AV23`, `AV31` y `AV32`, bajo `C:/Users/iker.villen/Desktop/AI_tool/Datos/1.App_septiembre/Fenix_Aiala/Fenix`. Los diez contienen las mismas nueve entradas distintas, repetidas dentro de cada archivo, con nueve ID y nueve códigos distintos y sin asociaciones contradictorias. Se consolidará un único catálogo compartido para Aiala.

El CSV no tiene cabecera y contiene `ID`, `CODIGO`, `DESCRIPCION` y `AGRUPACION`, en ese orden, según `Fenix tablas.xlsx`, hoja «Datos Maestros 1». El separador es `;`; la posición vacía después del separador final no es otra columna. Se validará la estructura sin inferir cabeceras o desplazar campos.

Se conservarán los nombres reales del CSV. Por ejemplo, el código `5` corresponde a `Rechazo molde NP`, aunque el Excel lo describa como `Rech Man ZF`; no se sustituirá automáticamente la etiqueta de Fenix por la de la documentación.

#### 4.4.2. Esquema, tipado e identidad

| Campo Delta | Campo Fenix | Tipo Delta / Arrow | Nulos | Validación |
| --- | --- | --- | --- | --- |
| `id_tipo_ar_fenix` | `ID` | `BIGINT` / `int64` | No | Entero decimal convertible sin pérdida y dentro del rango de 64 bits con signo; no aceptar vacíos, fracciones ni conversiones mediante coma flotante. |
| `codigo_tipo_ar` | `CODIGO` | `BIGINT` / `int64` | No | Entero decimal con las mismas garantías. Es el campo que interpreta `LISTA_AR.TIPO`. |
| `descripcion` | `DESCRIPCION` | `STRING` / `string` | No | Texto no vacío tras retirar espacios de los extremos; preservar palabras, acentos, mayúsculas y abreviaturas originales. |
| `codigo_agrupacion` | `AGRUPACION` | `BIGINT` / `int64` | No | Conservar el código entero original, sin derivarlo de la descripción ni asignarle etiquetas no confirmadas. |

La normalización de espacios de los extremos será explícita y determinista; la evidencia de entrada conservará el valor anterior a ella. No corregir nombres, generar descripciones ni fusionar tipos por igualdad o similitud de texto.

La clave de negocio será `id_tipo_ar_fenix` en el catálogo de Aiala; se exigirá también unicidad de `codigo_tipo_ar` en el catálogo aceptado. Aunque ambos coinciden numéricamente en la muestra, no se confundirán: las referencias desde `LISTA_AR` se resolverán por código. Delta no impone por sí solo estas unicidades; la ingesta las comprobará bajo el modelo de único escritor.

La descripción y la agrupación no son claves únicas. Varios tipos pueden compartir texto o agrupación sin fusionarse. La muestra contiene agrupaciones `1`, `2`, `3` y `4`, pero no se impondrá una lista cerrada a partir de ella. El grupo `2` incluye avisos y rechazos de trampilla: no etiquetarlo automáticamente como «avisos». La equivalencia semántica de las agrupaciones queda pendiente sin impedir conservar sus códigos.

La carpeta de línea no pertenece a la identidad. Planta/ámbito y procedencia técnica se conservarán en configuración y control/auditoría. Una futura integración con otros catálogos de planta deberá distinguir sus ámbitos antes de mezclar ID o códigos.

#### 4.4.3. Comprobaciones, duplicados y conflictos

En cada archivo nuevo o revisión estable de un archivo actualizado se validarán estructura, tipos y campos obligatorios, se consolidarán repeticiones y se contrastarán los cuatro campos conservados tanto entre candidatos como frente al snapshot publicado. Se procesará la revisión estable fijada por el descriptor del lote conforme a la sección 7; un reintento no volverá a leer una versión diferente de un CSV vivo.

| Situación | Resultado | Tratamiento |
| --- | --- | --- |
| Misma entrada repetida dentro de un archivo, entre líneas/fechas o en un reintento | Duplicado idéntico / `NO_CHANGE` | Consolidar; no crear otra entrada ni modificar la publicada. |
| Misma pareja ID–código, descripción y agrupación iguales | `NO_CHANGE` | Mantener contenido y primera recepción; registrar la observación en control. |
| ID y código nuevos con contenido válido y sin contradicciones | `INSERT` | Insertar una sola entrada y auditar su publicación. |
| Mismo ID con códigos distintos | `CONFLICT` | Apartar los candidatos afectados y conservar la asociación publicada, si existe. |
| Mismo código con ID distintos | `CONFLICT` | No admitir duplicidad de código ni elegir una alternativa arbitrariamente; registrar para revisión. |
| Misma pareja ID–código con descripción o agrupación distintas | `CONFLICT` para revisión | No sobrescribir lo aceptado; registrar valores anterior/candidato, campos discrepantes y procedencia. |
| Distintos ID y códigos con descripción o agrupación coincidentes | Entradas independientes | No fusionar; comprobar cada entrada por sus identificadores. |
| Campo obligatorio inválido o estructura incompatible | `INVALID` | Apartar con evidencia y motivo; no fabricar valores ni publicar filas incompletas. |
| Una entrada desaparece de un volcado posterior | Sin baja automática | Conservar la entrada publicada. |

No resolver incongruencias por orden físico, última llegada, fecha del nombre del archivo o mayoría de repeticiones. Si una clave nueva es ambigua dentro del lote, no insertar ninguna alternativa antes de la revisión. Las entradas independientes válidas se tratarán según las unidades de escritura y recuperación de la sección 8; un lote con incidencias pendientes no se declarará completo.

Una corrección de identificadores, descripción o agrupación requerirá decisión explícita y operación auditada, con valores anterior/nuevo y consideración de sus referencias históricas; la llegada de otro CSV no la autoriza por sí sola. Una nueva columna o estructura distinta se registrará como incompatibilidad de contrato, sin evolución automática de esquema. «Novedades» significa nuevas entradas validadas, no nuevos campos incorporados silenciosamente.

#### 4.4.4. Escritura, idempotencia y auditoría

Antes de invocar una escritura se calculará el conjunto de entradas nuevas válidas contra el snapshot actual. Si está vacío, la operación del diccionario será `NO_CHANGE`: no ejecutar `MERGE`, `UPDATE`, `APPEND` ni `OVERWRITE`, no reescribir Parquet y no generar un commit Delta por observar otra vez el catálogo. No actualizar `last_seen` en las filas ni sustituir la tabla por el último CSV. La creación inicial se hará en la carga controlada; el mantenimiento de almacenamiento, si procede, será una operación separada.

Cuando haya entradas nuevas válidas, insertar por clave sin rama de actualización de coincidencias, después de verificar también la unicidad del código. No modificar archivos fuente. Un reintento comprobará las publicaciones confirmadas y reconciliará commits inciertos conforme a la sección 8, sin duplicar entradas ni contabilizaciones.

El hash de negocio incluirá exactamente los cuatro campos conservados, con la normalización y versión de contrato acordadas; no incluir recepción, archivo, carpeta o número de intento. El hash no sustituye a la clave.

El control/auditoría persistente conservará archivo y revisión estable, huella, línea/carpeta, fila de origen, lote, versión del contrato, snapshot consultado, resultado y contadores de filas recibidas, repeticiones, entradas nuevas, coincidencias, conflictos e inválidos. En cada discrepancia registrará campos afectados, valores existentes y candidatos y estado pendiente de revisión. La primera recepción de una entrada aceptada permanecerá inmutable.

También se registrará el resultado de una revisión sin escrituras. Si solo hay duplicados, no inventar una versión Delta; si hay conflictos o inválidos y ninguna inserción, registrar esas incidencias sin presentarlas como una validación satisfactoria.

#### 4.4.5. Criterios de aceptación para la implementación

- La primera carga de la muestra contrastada produce nueve entradas compartidas para Aiala, sin duplicarlas por línea, fecha o repetición física del catálogo.
- `LISTA_AR.TIPO = 2` se interpreta como `Aviso ZC` mediante `codigo_tipo_ar`; se mantienen ID y código separados en el esquema.
- Se conserva `Rechazo molde NP` para el código `5`, sin sustituirlo por la denominación del Excel.
- Se conservan las agrupaciones como códigos numéricos, sin inferir etiquetas; tipos diferentes de un mismo grupo o con igual descripción permanecen separados.
- Reprocesar los diez archivos no modifica contenido, primera recepción ni versión Delta; sí registra el resultado operativo.
- Una nueva entrada válida se inserta una sola vez; un reintento o repetición entre líneas no añade otra.
- Un ID reutilizado con otro código, un código reutilizado con otro ID o un cambio de descripción/agrupación queda registrado para revisión sin sobrescribir lo publicado.
- Campos vacíos, enteros fuera de rango y estructuras incompatibles quedan apartados, sin conversiones con pérdida, valores inventados o ampliaciones automáticas del esquema.
- La desaparición de una entrada no la borra; una revisión sin novedades válidas no produce escrituras innecesarias.
- Un fallo después del commit y antes de confirmarlo se reconcilia según la sección 8, manteniendo idempotencia y auditoría.

### 4.5. Contrato acordado: `MP9_CAMBIO_MODELO`

Estado: implementación local de ensayo completada en la entrega 05, con 48 identidades revisadas, 46 publicadas y los dos cierres de AV32 validados. La publicación real permanece parcial: AV32/2007 está en revisión por ZF_OK y AV32/2008 pendiente de su ZC. Evidencia y límites en su ficha de 17.12. Versión inicial del contrato: `mp9_cambio_modelo_v1`. La zona horaria de las fechas y la precedencia de cambios de `ZF_OK` sin versión fiable de origen deberán confirmarse antes de automatizar esos casos.

Actualización contractual del 19/10/2026: se acuerda `mp9_cambio_modelo_v2` para recuperar `ID_MAQUINA`. El esquema y reglas vigentes descritos a continuación corresponden al objetivo v2; el ejecutable v2 está implementado y probado con datos sintéticos en revisión 33; los destinos reales ya creados permanecen en v1 hasta la reconstrucción explícita de 17.14/17.16. No reinterpretar una tabla de siete campos como si contuviera ocho ni modificar sus metadatos para adoptarla silenciosamente.

#### 4.5.1. Finalidad, alcance y evidencia real

Conservar qué modelo ha entrado y salido, y cuándo, de zona caliente y zona fría. Esta tabla contiene registros temporales, no un diccionario ni una lista de modelos únicos. Un mismo modelo puede volver a producirse en otro intervalo y los registros ZC/ZF de una producción tienen ID diferentes relacionados mediante `ID_ST1_REL`.

Se mantendrán ocho campos en su orden original relativo: `ID`, `ID_MODELO`, `FECHA_ON`, `FECHA_OFF`, `ID_MAQUINA`, `STATUS`, `ID_ST1_REL`, `ZF_OK`. `ID_MAQUINA` identifica la parte física de la tándem y forma parte del contenido de negocio, sus comparaciones y su hash v2. Siguen descartados `ID_PREPARACION` y `N_MOD`; sus cambios aislados no generan escritura. Esta decisión sustituye expresamente la exclusión de `ID_MAQUINA` acordada el 15/10, cuyo historial se conserva.

La muestra real examinada comprende los archivos `20261013_MP9_CAMBIO_MODELO.csv` y `20261014_MP9_CAMBIO_MODELO.csv` de `AV21`, `AV22`, `AV23`, `AV31` y `AV32`, bajo `C:/Users/iker.villen/Desktop/AI_tool/Datos/1.App_septiembre/Fenix_Aiala/Fenix`. Los CSV carecen de cabecera, usan `;` y repiten registros; la posición vacía tras el separador final no es otra columna. El lector validará diez campos originales antes de proyectar los ocho conservados, sin desplazar columnas al eliminar las descartadas.

En AV21, el archivo del día 14 contiene los registros `1533` y `1534` del modelo `875`, cerrado en ZC el 13/10/2026 a las 06:00:00 y en ZF a las 07:03:26; los nuevos registros `1535` y `1536` corresponden al modelo `2071`. `1536.ID_ST1_REL = 1535` relaciona el registro ZF con el ZC. El significado se apoya en `Fenix tablas.xlsx`, hoja «Datos Maestros 2».

En AV32 hay versiones del mismo ID dentro de un único archivo: `2003` aparece sin cierre en la fila 5 y con `FECHA_OFF = 14/10/2026 06:23:00` en la fila 61; `2004` aparece sin cierre en la fila 8 y con cierre `14/10/2026 07:23:14` en la fila 75. También aparece el nuevo registro `2007`, con `ZF_OK = 0` en la fila 62 y `ZF_OK = 1` en la fila 72. Estos casos demuestran que no basta con insertar ID desconocidos ni eliminar todas las filas repetidas por ID conservando una alternativa arbitraria.

#### 4.5.2. Esquema de negocio en el orden original

| Posición Delta | Campo original | Tipo Delta / Arrow | Nulos | Significado y comprobación |
| --- | --- | --- | --- | --- |
| 1 | `ID` | `BIGINT` / `int64` | No | Identidad del registro en su ámbito Fenix. Entero decimal convertible sin pérdida, dentro del rango de 64 bits con signo. |
| 2 | `ID_MODELO` | `BIGINT` / `int64` | No | ID del modelo que se interpreta mediante el diccionario `SIP_MODELO` del mismo ámbito de planta. Mismas garantías de conversión entera. |
| 3 | `FECHA_ON` | `TIMESTAMP` | No | Fecha y hora de entrada del modelo en la zona indicada por `STATUS`. |
| 4 | `FECHA_OFF` | `TIMESTAMP` | Sí | Fecha y hora de salida de esa zona; vacío de origen se normaliza a nulo, nunca a una fecha ficticia. |
| 5 | `ID_MAQUINA` | `BIGINT` / `int64` | No | Quinta columna del CSV original. Referencia a la máquina física, resuelta en `SIP_MAQUINA_MOLDEO`; discrimina las partes de la tándem. No es el código de ramal ni `CONT_ZC.MAQ_IS`. |
| 6 | `STATUS` | `BIGINT` / `int64` | No | `1 = ZC`, `2 = ZF`. No interpretarlo mediante `SIP_DEFECTO_STATUS`, que pertenece a `LISTA_AR`; no indica marcha/parada. |
| 7 | `ID_ST1_REL` | `BIGINT` / `int64` | Sí | Referencia del registro ZF a su registro ZC. Vacío se normaliza a nulo; no a cero. |
| 8 | `ZF_OK` | `BIGINT` / `int64` | No | Indicador descrito como «Cambio confirmado», pendiente de confirmar su semántica operativa y precedencia 0/1. Se conserva; no indica por sí solo producción terminada o actualmente fabricando. |

No renombrar ni reordenar estas columnas. Se validarán los enteros sin convertir mediante coma flotante. Un `STATUS` distinto de los valores definidos o un `ZF_OK` fuera de los valores actualmente definidos para el indicador se apartará para revisión del contrato, sin convertirlo por su valor de verdad ni inferir su significado.

Los timestamps se leerán con formato día/mes/año y hora, admitiendo representaciones equivalentes como `7:19:00` y `07:19:00`, que no producirán cambios. No interpretar fechas con orden mes/día ni usar la configuración regional de la máquina. Conservar el texto de origen en la evidencia. La implementación fijará zona horaria de planta, resolución de horas ambiguas/inexistentes y representación física de `TIMESTAMP` antes de la carga productiva; no asumir UTC ni convertir silenciosamente una hora local ambigua.

`ZF_OK` se conserva como confirmación de Fenix, pero no aporta por sí solo una hora de salida: nunca sustituir `FECHA_OFF`, cerrar un registro por recibir un `1` o inventar una fecha a partir de la recepción. Sus transiciones no se considerarán monotónicas sin confirmación de origen.

#### 4.5.3. Identidad, ámbito y relaciones

La identidad de escritura sigue siendo `(planta, origen_fenix, ID)`: recuperar la máquina no cambia los ID ni crea otra fila por cada variante de ese campo. El origen estable procede de configuración/carpeta, no del nombre fechado o recepción. Los ocho campos forman el contenido de negocio v2; los metadatos técnicos se colocan después. No confiar en una procedencia disponible solo en un log si el escritor no puede discriminar filas con ella.

No usar modelo, máquina, fechas o hash como sustitutos de la identidad MP9. La carpeta aporta procedencia; no prueba que exista una única máquina o producción activa. `ID_MAQUINA` se valida contra el catálogo físico y su compatibilidad con el origen/línea. Un mismo ID MP9 con máquinas distintas es conflicto; no escoger una ni generar otra identidad para ocultarlo. Las referencias ausentes quedan pendientes, sin deducir máquinas por modelo, orden, sufijo o coincidencia numérica.

`ID_MODELO` deberá resolverse en el diccionario aceptado de Aiala. Un ID aún no incorporado generará una dependencia pendiente y se reintentará después de actualizar el diccionario; no fabricar el modelo ni descartarlo silenciosamente. Una referencia contradictoria seguirá el circuito de revisión.

Para ZF, `ID_ST1_REL` debe referenciar una ZC del mismo ámbito con iguales `ID_MODELO` e `ID_MAQUINA`, publicada o válida en lote. Otra zona, modelo, máquina o referencia a sí mismo es conflicto. Una referencia ausente conserva dependencia/evidencia; no se completa por proximidad temporal, modelo común u orden. La ZC puede tener vínculo nulo. La fabricación no depende de que se cierre un AR.

#### 4.5.4. Revisión incremental, duplicados y cierres

Cada archivo nuevo o revisión estable se procesará según el descriptor de la sección 7. Se revisarán todos sus ID y versiones relevantes, incluidos los ya existentes. No limitar la detección a `ID > último ID`, a la fecha del nombre del archivo o a `FECHA_ON` posterior al último registro: el cierre puede afectar a una entrada iniciada días antes.

Después de normalizar, consolidar duplicados exactos y agrupar por identidad, comprobar coherencia dentro del lote y contra el snapshot publicado. Para una misma identidad, `ID_MODELO`, `ID_MAQUINA`, `FECHA_ON`, `STATUS` y el vínculo ZC/ZF deben ser compatibles. No escoger la primera o última fila.

| Situación | Resultado | Tratamiento |
| --- | --- | --- |
| ID nuevo con contenido válido y relaciones resueltas | `INSERT` | Insertar una única entrada; una nueva fila ZC y su nueva fila ZF conservan ID independientes. |
| Mismos ocho campos normalizados, dentro del archivo, entre revisiones o en reintentos | Duplicado / `NO_CHANGE` | Consolidar; no insertar ni actualizar la fila aceptada. |
| Registro abierto que recibe un único cierre no nulo, coherente y no anterior a `FECHA_ON`, con el resto del contenido compatible | `UPDATE` de cierre | Completar `FECHA_OFF` sobre el mismo ID; mantener su identidad, inicio y primera recepción, y auditar el cambio. |
| Versiones sin cierre y con un mismo cierre válido, con iguales valores restantes | Consolidación de cierre | El cierre informado prevalece sobre la ausencia de cierre sin depender del orden de las filas. No fabricar otra versión de producción. |
| Registro ya cerrado que recibe una copia sin cierre, con iguales valores restantes | Sin regresión / `NO_CHANGE` | Conservar el cierre. La ausencia de fecha no es una orden de reapertura; registrar la observación sin atribuirle una antigüedad exacta no demostrada. |
| Registro ya cerrado con el mismo cierre y contenido | `NO_CHANGE` | No escribir otra vez. |
| Dos fechas de cierre diferentes para la misma identidad | `CONFLICT` | Conservar lo publicado, apartar candidatos y registrar ambas fechas; no elegir máximo, mínimo o última llegada. |
| Cierre anterior al inicio, fecha inválida o campo obligatorio inválido | `INVALID` | Apartar con valor original, motivo y procedencia; no corregir automáticamente. |
| Mismo ID con otra máquina, modelo, inicio, zona o vínculo incompatible | `CONFLICT` | Mantener aceptado y candidatas completas para revisión, sin cambiar identidad ni elegir por última llegada. |
| Cambio de `ZF_OK` sin versión o regla fiable de precedencia | `CONFLICT` para revisión | Mantener el valor aceptado, si existe, y guardar candidatos; el orden del CSV no demuestra cuál debe prevalecer. |
| Una entrada desaparece de un CSV posterior | Sin baja automática | Mantener el registro; no borrarlo ni inferir una fecha de salida. |
| Solo cambian `ID_PREPARACION` o `N_MOD` | `NO_CHANGE` | Ignorar esos campos descartados; `ID_MAQUINA` ya no pertenece a esta excepción. |

La regla de completar cierres se aplica únicamente cuando el resto del contenido es compatible y los valores no nulos de cierre coinciden. No combinar fragmentos incompatibles de varias filas para construir una versión inventada. Si una identidad nueva incluye versiones contradictorias aún no resueltas, mantenerla pendiente sin escoger una alternativa para poder insertarla.

La muestra `2007` con `ZF_OK` tanto `0` como `1` quedará marcada para revisión de esa precedencia; no se afirmará que «última fila gana» ni que `1` siempre reemplaza `0`. Si se confirma una regla fiable de actualización del indicador, se incorporará explícitamente al contrato, con sus pruebas y auditoría, antes de automatizarla. Las correcciones de un inicio, modelo, vínculo o cierre ya informado también requerirán resolución explícita.

Los periodos de presencia en cada zona se interpretarán como `[FECHA_ON, FECHA_OFF)` cuando exista cierre: la hora de salida no pertenece al periodo anterior. Son fechas de entrada/salida de dos posiciones físicas de la línea, no producciones independientes ni segmentos que deban concatenarse para construir una serie. Un nulo expresa cierre no informado; no garantiza por sí solo que el modelo siga físicamente activo en el instante de consulta. No imponer continuidad exacta o una única producción abierta por carpeta ni inferir el cierre por el comienzo de otro registro. ZC y ZF mantienen sus fechas propias y pueden cambiar en instantes diferentes. La duración global de la producción se define en 16.2 desde la primera entrada ZC hasta la última salida ZF de sus registros relacionados.

#### 4.5.5. Escritura idempotente y ausencia de novedades

Antes de escribir se preparará, por identidad, una única decisión resuelta: inserción, cierre válido, coincidencia, dependencia pendiente, conflicto o inválido. Guardar la intención de las operaciones que modifican datos antes de ejecutarlas, con snapshot, clave, hash previo/candidato y revisión de entrada. El hash de negocio incluirá exactamente los siete campos conservados, con fechas y nulos normalizados; no incluir descartados, recepción, carpeta, número de intento ni fechas de ejecución. El contexto de origen discrimina la identidad de operación aunque no forme parte del contenido comparado.

La publicación será un `MERGE` condicionado por identidad completa y por la regla de cambio: insertar solo ID realmente nuevos y actualizar solo campos con transición validada. Para el cierre ordinario, la actualización será `FECHA_OFF` de nulo a la fecha aceptada, no una sustitución indiscriminada de todas las columnas. Un append de registros nuevos es una operación lógica permitida tras las comprobaciones; no significa concatenar cada CSV ni repetir filas existentes.

Si no hay inserciones ni actualizaciones válidas, no invocar escrituras, no reescribir Parquet ni generar commits Delta para registrar que se ha revisado el CSV. No reconstruir o sustituir la tabla completa, no modificar fuentes y no actualizar `last_seen` en filas sin cambios. La creación inicial y las operaciones de mantenimiento serán procesos separados y controlados.

Los cierres sí modifican una fila existente: esto es una actualización legítima y auditada, no una vulneración de la regla de no sobrescribir registros sin cambios. La primera recepción será inmutable; la modificación conservará valor anterior/nuevo y su confirmación. Se mantendrá evidencia histórica del cambio sin convertir cada observación en otra fila de negocio.

Los reintentos usarán las mismas identidades de lote/operación y la misma revisión estable de entrada. Una inserción ya confirmada o un cierre ya aplicado se resolverán como coincidencia, no como una segunda operación de negocio. Un resultado incierto de commit se reconciliará con la tabla y su evidencia según la sección 8 antes de repetir la escritura; no duplicar inserciones, cierres ni contadores de auditoría.

El modelo de único escritor y la comprobación del estado publicado antes de ejecutar la operación evitarán aplicar decisiones sobre un snapshot superado. Cualquier excepción de concurrencia obligará a revisar los candidatos contra el snapshot vigente antes de reintentar, sin degradar un cierre ni saltarse las comprobaciones de identidad.

#### 4.5.6. Auditoría, pendientes y recuperación

Registrar cada revisión comprobada aunque no escriba: archivo y huella, origen, fila/filas de evidencia, lote, contrato, transformador, snapshot, resultado y contadores separados de filas físicas, ID distintos, duplicados, inserciones, cierres, coincidencias, conflictos, inválidos y dependencias pendientes.

Las incidencias incluirán clave completa, campos afectados, valores publicados/candidatos, procedencia y estado de revisión. Diferenciar una dependencia pendiente por llegada desordenada de un dato contradictorio y de un error técnico reintentable; repetir sin cambios no resolverá por sí solo un conflicto de negocio. Conservar los candidatos para revisión y no marcar un lote completo mientras haya unidades pendientes según la sección 8.

Las unidades válidas independientes podrán publicarse bajo el protocolo de lotes parciales del plan. No dar por íntegra una relación ZF–ZC con dependencias no resueltas ni publicar un snapshot de consumo como completo si faltan unidades requeridas. El control operativo y las auditorías se conservarán fuera de repositorios e imagen de ejecución, sin forzar escrituras de esta tabla por observaciones repetidas.

Una dependencia pendiente no detendrá indefinidamente las siguientes recepciones ni la ingesta de unidades independientes. Registrar su antigüedad, reintentar al recibir información relacionada y escalar a revisión al superar el umbral operativo que se acuerde; el vencimiento no inventa relaciones o fechas. Aplicar la distinción entre producción abierta, relación pendiente y anomalía de 16.4, conservando la última publicación coherente cuando corresponda.

#### 4.5.7. Criterios de aceptación para la implementación

- El esquema v2 conserva ocho campos y el orden `ID`, `ID_MODELO`, `FECHA_ON`, `FECHA_OFF`, `ID_MAQUINA`, `STATUS`, `ID_ST1_REL`, `ZF_OK`; máquina int64 obligatoria, metadatos después y hash de los ocho campos.
- Dos fabricaciones del mismo modelo y origen en distintas máquinas no se fusionan. ZF y ZC referenciadas deben compartir máquina/modelo. Un cambio de máquina para el mismo ID se conserva como conflicto, no como actualización ordinaria.
- Reprocesar los mismos CSV o versiones idénticas normalizadas no modifica contenido, primera recepción ni versión Delta; registra el resultado en control.
- Una nueva entrada ZC y su entrada ZF se insertan una sola vez, con sus ID y relación, aunque se repitan en múltiples volcados.
- El caso AV32 `2003` se consolida con cierre `14/10/2026 06:23:00` y `2004` con cierre `14/10/2026 07:23:14`, sin duplicar ID ni depender de la posición de sus versiones abiertas/cerradas. Repetir o recibir después la copia abierta no reabre esos registros.
- Un cierre contradictorio, un inicio/modelo/zona/vínculo incompatibles o el cambio no resuelto de `ZF_OK` quedan registrados para revisión sin sobrescribir el dato aceptado; el caso `2007` aporta la muestra de indicador pendiente.
- `ZF_OK` no genera una fecha de salida ni fuerza continuidad entre intervalos; las fechas de ZC y ZF permanecen independientes.
- Modelos o registros ZC referenciados aún no recibidos generan dependencias pendientes, con reintento tras su incorporación y sin referencias inventadas.
- Campos obligatorios vacíos, fechas imposibles, cierres anteriores al inicio y enteros fuera de rango se apartan con evidencia. Las horas equivalentes con uno o dos dígitos no generan cambios; el tratamiento de horario local se valida antes de cargar producción.
- Un fallo tras aplicar un cierre o insertar y antes de confirmar se recupera sin repetir efectos o contadores; una revisión sin novedades válidas no genera commits de negocio.
- No se borran entradas ausentes de un volcado, no se fusionan producciones por modelo/carpeta y no se crea una evolución automática de esquema ante nuevas columnas incompatibles.

### 4.6. Contrato acordado: `LISTA_DEFECTOS`

Estado: implementación local de ensayo completada en la entrega 07; 33.937 filas reales conciliadas íntegramente, bases de inspección verificadas y replay sin escrituras Delta. Evidencia y límites en su ficha de 17.12. Versión del contrato: `lista_defectos_v2`, que incorpora `CAVIDAD` a la clave inicialmente propuesta en `v1`; no se ha migrado el histórico activo ni conectado esta fuente Delta a la aplicación. Mantener el uso funcional de esta fuente en la aplicación histórica, sin cambiar fórmulas ni denominadores de forma implícita por introducir Delta.

#### 4.6.1. Significado, conservación y evidencia

`LISTA_DEFECTOS` contiene observaciones de defectuosidad de las máquinas de inspección de zona fría. La documentación Fenix, hoja «Eventos» de `Fenix tablas.xlsx`, establece grabación cada diez minutos y describe cantidades inspeccionadas y rechazadas por contexto y defecto. `FECHA` es la fecha de registro: antes de asignarle una ventana de negocio hay que respetar la interpretación temporal ya utilizada en el histórico, sin convertirla automáticamente en fecha de inicio o fin de intervalo por su posición en el CSV.

Conservar las 18 columnas de negocio, con sus nombres y orden originales. No quitar columnas, preagregar filas antes de conservarlas ni sustituir `RECH` por los canales. El separador final del CSV produce una posición vacía adicional; no es una decimonovena columna de negocio. Los enriquecimientos para la aplicación se construirán aparte, sin reemplazar códigos o cantidades originales en esta tabla.

Ejemplo real, archivo `AV23/20261014_LISTA_DEFECTOS.csv` bajo `C:/Users/iker.villen/Desktop/AI_tool/Datos/1.App_septiembre/Fenix_Aiala/Fenix`: las primeras cinco filas comparten fecha `14/10/2026 00:00:00`, molde `0`, ramal `1`, máquina `13`, cavidad vacía e `INSPECC = 96`, y difieren en `COD_DEF` (`15`, `17`, `37`, `38`, `39`) y sus rechazos. Son cinco registros de defecto válidos, pero una sola base de inspección de 96 para ese contexto. La fila 6 pertenece a otra máquina, `12`, e informa 89 inspeccionadas: es un contexto diferente que se mantiene separado.

En ese archivo se han comprobado 2.304 filas, sin duplicados bajo la clave acordada en 4.6.3. Esta observación valida la muestra, no garantiza que futuras revisiones no contengan duplicados, correcciones o contradicciones.

#### 4.6.2. Esquema original y validaciones

| Posición | Columna original | Tipo Delta / Arrow | Nulos | Regla |
| --- | --- | --- | --- | --- |
| 1 | `FECHA` | `TIMESTAMP` | No | Fecha/hora válida, normalizada conforme a la zona horaria y política temporal del origen. |
| 2 | `MOLDE` | `BIGINT` / `int64` | No | Entero no negativo. `0` significa molde no leído; no es el ID del modelo de botella. |
| 3 | `LINEA` | `BIGINT` / `int64` | No | Número de ramal, conservado como código original; no equivale al nombre de carpeta AV23. |
| 4 | `COD_MAQ_ZF` | `BIGINT` / `int64` | No | Código original de la máquina de inspección. |
| 5 | `COD_DEF` | `BIGINT` / `int64` | No | Código original del defecto MIA; no interpretarlo mediante el diccionario de avisos/rechazos `SIP_DEFECTO`. |
| 6 | `INSPECC` | `BIGINT` / `int64` | No | Cantidad entera no negativa; conservar sin sumar repeticiones entre defectos. |
| 7 | `RECH` | `BIGINT` / `int64` | No | Cantidad entera no negativa, exactamente como llega de la fuente. |
| 8 | `RECH1` | `BIGINT` / `int64` | No | Contador original del canal 1, entero no negativo. |
| 9 | `RECH2` | `BIGINT` / `int64` | No | Contador original del canal 2, entero no negativo. |
| 10 | `RECH3` | `BIGINT` / `int64` | No | Contador original del canal 3, entero no negativo. |
| 11 | `RECH4` | `BIGINT` / `int64` | No | Contador original del canal 4, entero no negativo. |
| 12 | `RECH5` | `BIGINT` / `int64` | No | Contador original del canal 5, entero no negativo. |
| 13 | `RECH6` | `BIGINT` / `int64` | No | Contador original del canal 6, entero no negativo. |
| 14 | `RECH7` | `BIGINT` / `int64` | No | Contador original del canal 7, entero no negativo. |
| 15 | `RECH8` | `BIGINT` / `int64` | No | Contador original del canal 8, entero no negativo. |
| 16 | `RECH9` | `BIGINT` / `int64` | No | Contador original del canal 9, entero no negativo. |
| 17 | `RECH10` | `BIGINT` / `int64` | No | Contador original del canal 10, entero no negativo. |
| 18 | `CAVIDAD` | `STRING` / `string` | Sí | Conservar denominaciones como `8F`, `1C` o `99Z`; vacío se normaliza a nulo, no a una cavidad inventada. |

Los enteros se convertirán directamente sin pérdida y dentro del rango de 64 bits con signo; no aceptar fracciones, truncamiento o paso por coma flotante. La ausencia de una cantidad obligatoria no se convertirá a cero. Los códigos se conservarán sin reasignarlos por similitud de nombre o por coincidencias con otra fuente.

Normalizar fechas equivalentes con hora de uno o dos dígitos antes de comparar, sin usar el orden regional de la máquina. La zona horaria y los casos ambiguos/inexistentes del cambio de hora se resolverán mediante la política del origen antes de cargar producción. Conservar texto y valores originales como evidencia.

La cavidad vacía y `99Z` se conservan con su significado documentado; no descartarlos de la ingesta ni interpretarlos como paradas. Cualquier filtro funcional seguirá los criterios existentes de la aplicación y será independiente de la conservación de la fuente.

`RECH`, `RECH1` a `RECH10` permanecen independientes e intactos. No imponer `RECH = suma(RECH1…RECH10)`: en la fila 316 del ejemplo, `RECH = 1`, `RECH1 = 1` y `RECH4 = 1`. La coincidencia de canales no autoriza sustituir el total ni considerar el registro inválido por esa suma. Una anomalía de cantidades se registrará para revisión, sin corregirla automáticamente.

#### 4.6.3. Clave acordada y detección de posibles revisiones

La clave compuesta acordada para deduplicar e insertar es:

`K = (origen, FECHA, MOLDE, LINEA, COD_MAQ_ZF, COD_DEF, INSPECC, CAVIDAD)`.

`origen` identificará de manera estable planta y procedencia Fenix —por ejemplo Aiala/AV23—, no archivo fechado, lote, posición de fila o ruta absoluta de una copia. Los valores se compararán tipados y normalizados. El ámbito técnico deberá poder utilizarse en la escritura, mediante metadatos posteriores a las 18 columnas originales o separación física por origen, conforme a la sección 4; no bastará una referencia guardada solo en un log.

Cambiar máquina, defecto, molde, ramal, fecha, origen o cavidad diferencia claves: no eliminar una fila válida porque otra tenga la misma cantidad inspeccionada o coincida en los demás campos. Si solo cambia `COD_DEF`, ambas filas se conservan y el doble conteo de inspeccionadas se evita en la agregación, no borrando defectos. Dos cavidades diferentes se tratan como claves y grupos de inspección independientes, no como un conflicto por compartir el resto de la clave anterior.

Como `CAVIDAD` admite nulo, la identidad, agrupación y condición de escritura deben comparar dos cavidades nulas como iguales mediante semántica explícita de igualdad con nulos. No usar una comparación que deje de reconocer coincidencias por contener nulos, ni sustituirlos por cero o por una denominación real. Un vacío de origen normalizado a nulo representa la misma componente de clave en cada reintento. La serialización/hash de identidad también distinguirá de forma determinista nulo y texto, sin colisiones por separadores o valores de relleno.

Para una misma `K`, comparar también todos los campos restantes: `RECH` y sus diez canales. Solo la igualdad de los 18 campos normalizados permite clasificar la repetición como duplicado idéntico. Misma clave con rechazos o canales distintos es un conflicto para revisión, no una fila que se pueda descartar silenciosamente. Conservar la entrada aceptada y guardar íntegramente la candidata contradictoria según 4.6.7, sin sumarlas, duplicarlas en la tabla activa ni sobrescribir una con la otra.

**Incluir `INSPECC` en la clave no demuestra que un contador distinto sea una nueva observación independiente.** Se comprobará además la firma `S = (origen, FECHA, MOLDE, LINEA, COD_MAQ_ZF, COD_DEF, CAVIDAD)`, sin `INSPECC`. Si llega otra `K` para la misma `S` con diferente `INSPECC`, conservar los candidatos y marcar posible revisión/conflicto antes de publicarla como inserción adicional. No sumar ambos valores, elegir máximo/mínimo o reemplazar uno por la última llegada sin evidencia de versión fiable. Esta guarda mantiene la clave solicitada y evita que una posible corrección de 96 a 97 se convierta en 193 inspeccionadas. La firma incluye cavidad para no confundir dos cavidades legítimamente distintas con una revisión del contador.

Una corrección confirmada que cambie `INSPECC` altera la clave `K`: requerirá una operación explícita de sustitución de identidad, con referencia anterior/nueva y auditoría, que retire la versión anterior del snapshot de consumo sin borrar su evidencia histórica. No implementarla como un append que deje ambas cantidades activas. La precedencia de revisiones aún no demostrada queda pendiente; los casos ambiguos permanecen apartados.

#### 4.6.4. Bases de inspección y agregación sin multiplicaciones

Separar la identidad de las filas de defecto de la base de inspección que comparten. El grupo de inspección será:

`G = (origen, FECHA, MOLDE, LINEA, COD_MAQ_ZF, CAVIDAD)`.

`COD_DEF` no forma parte de `G`, porque distintos defectos pueden compartir la misma base. `INSPECC` es el valor a validar dentro del grupo, no una dimensión que permita sumar versiones incompatibles. La cavidad nula tendrá una representación determinista para agrupar/comparar; no confundirla con cero, cadena vacía sin normalizar o una cavidad concreta.

- Mantener todas las filas de defectos aceptadas y sus rechazos por separado.
- Si las filas de un mismo `G` comparten un único valor de `INSPECC`, tomarlo una sola vez al obtener su base de inspección; en el ejemplo, las cinco primeras filas aportan 96, no 480.
- Si un mismo `G` presenta cantidades inspeccionadas distintas sin explicación de grano/versiones, registrar la discrepancia y no sumar valores distintos ni escoger un máximo para resolverla. No publicar ese grupo como una base de inspección resuelta hasta validar el caso.
- Otra máquina es otro grupo: las 89 inspeccionadas de máquina `12` no se fusionan con las 96 de máquina `13`. Lo mismo ocurre si cambia fecha, origen, molde, ramal o cavidad.
- Dos grupos válidos pueden tener el mismo número de inspeccionadas sin ser duplicados. Nunca usar `sum(distinct INSPECC)` a escala de producción como regla de deduplicación numérica.
- Al agregar ventanas o producciones, usar las bases ya resueltas y las dimensiones que requiera la métrica; no volver a sumar `INSPECC` sobre filas de defectos ni sobre joins que multipliquen filas.

Separar las observaciones por máquina no demuestra que sean botellas físicas diferentes: máquinas en serie pueden inspeccionar las mismas unidades. La suma entre máquinas solo tiene sentido para una métrica definida de inspecciones, no automáticamente para «botellas únicas de la planta». Mantener denominadores y agregaciones del histórico conforme al apartado siguiente, en lugar de convertir 96 + 89 en producción única sin una regla de negocio.

No sumar los rechazos de defectos distintos para afirmar un total de botellas únicas rechazadas sin demostrar que son conjuntos disjuntos. Esta precaución no elimina ni modifica los contadores originales ni las reglas actuales de cálculo por defecto.

#### 4.6.5. Compatibilidad con la aplicación histórica verificada

La revisión del código actual identifica los campos enriquecidos en `calidad_serie/backend/app/infrastructure/parquet/schema.py`. La compatibilidad debe preservarse mediante una transformación aparte desde los códigos originales y sus referencias aprobadas, manteniendo filtros por máquina, defecto, cavidad y ramal. No reincorporar fuentes descartadas como condición implícita.

En el score MIA, `backend/app/infrastructure/repositories/customer_score.py` utiliza preferentemente las inspeccionadas reales procedentes de `LISTA_RECH`, almacenadas por el circuito `infrastructure/ingestion/inspected_totals.py`; los rechazos por defecto proceden de `LISTA_DEFECTOS`. Los agregados de `infrastructure/aggregates/mia.py` siguen el mismo criterio. El denominador existente agrupa por timestamp y ramal de máquina y toma el máximo de inspeccionadas antes de sumar las ventanas; esta es una regla de esa métrica y fuente concretas, no una autorización para resolver conflictos entre versiones de `LISTA_DEFECTOS` usando máximos.

Existe además un fallback legacy en `_fallback_inspected_total` que suma `LISTA_DEFECTOS_INSPECC` si faltan las inspeccionadas reales. No considerarlo una prueba de deduplicación correcta de los CSV nuevos ni usarlo para justificar multiplicaciones. La migración no activará silenciosamente esa ruta por faltar la nueva fuente: mantendrá explícita la cobertura y validará el denominador esperado con `LISTA_RECH`. Cualquier cambio necesario del comportamiento legacy se abordará por separado, con pruebas y revisión, sin modificar código al editar este plan.

La prueba existente `backend/tests/test_customer_score.py::test_mia_rates_use_real_inspected_totals` sirve como referencia del denominador preferido. Antes de conectar Delta, comparar sobre una muestra histórica controlada los conteos, rechazos por defecto, tasas y score de producción/turno/ventana con y sin filtros relevantes. No aceptar cambios silenciosos de fórmula ni exigir que un error de doble conteo se reproduzca como criterio de compatibilidad; documentar cualquier discrepancia y su resolución.

#### 4.6.6. Escrituras incrementales, conflictos e idempotencia

Revisar cada archivo nuevo o revisión estable conforme a la sección 7, incluidos registros de fechas ya procesadas; no deducir inmutabilidad por ser histórico. Antes de escribir, normalizar, verificar tipos/estructura y resolver duplicados y las comprobaciones `K`, `S` y `G` dentro del lote y frente al estado publicado. Las 18 columnas son fijas para este contrato: campos adicionales o cambios de posición requieren revisión de esquema, no evolución automática ni descarte silencioso.

| Situación | Resultado | Tratamiento |
| --- | --- | --- |
| Misma `K` y mismos 18 campos | Duplicado / `NO_CHANGE` | No insertar ni actualizar; conservar primera recepción y registrar la observación. |
| `K` nueva, válida, sin posible revisión contradictoria ni conflicto de grupo | `INSERT` | Insertar una sola fila con sus 18 campos y procedencia, tras preparar la intención auditable. |
| Solo cambia máquina, defecto u otra dimensión de identidad, manteniendo datos válidos | Candidata independiente | No fusionar claves; aplicar después las reglas de base compartida para no multiplicar inspeccionadas entre defectos. |
| Misma `K` con rechazos o canales diferentes | `CONFLICT` | Conservar lo aceptado y guardar íntegramente cada candidata en el registro persistente de revisión; no sumar, descartar, duplicar en la tabla activa ni sobrescribir. |
| Misma `S` con otro `INSPECC` | Posible revisión / `CONFLICT` | No publicar otra fila activa automáticamente; resolver la precedencia y, si es corrección, sustituir la versión mediante operación explícita. |
| Mismo `G` con bases inspeccionadas incompatibles | Incidencia de agregación | Mantener evidencia y no publicar una base resuelta ni un resultado completo que dependa de ella hasta aclararla. |
| Tipo, estructura o cantidad obligatoria inválidos | `INVALID` | Apartar con valor original y motivo; no rellenar, truncar ni convertir ausencias a cero. |
| Registro ausente de una revisión posterior | Sin baja automática | Mantener la fila; no interpretar la ausencia como una orden de borrado. |

La presencia de dos filas con `INSPECC` igual no basta para excluir una, y un cambio de contador no basta para declarar otra observación independiente. Las discrepancias solo se resolverán con regla de fuente/versionado demostrada o decisión revisada y auditada. El hash de los 18 campos detectará cambios, pero no sustituirá a `K` ni a los controles de revisión y agregación.

Publicar solo decisiones resueltas bajo el modelo de único escritor. Un append será únicamente de candidatas nuevas validadas, no una concatenación de cada CSV. No reemplazar tablas o archivos fuente, no sobrescribir coincidencias ni actualizar `last_seen` en Delta por observaciones repetidas. Si no hay inserciones o correcciones explícitamente resueltas, no invocar escrituras ni crear commits o reescrituras Parquet; sí registrar resultados operativos.

La publicación deberá comprobar estado vigente y unicidad por `K`; si hay un conflicto de concurrencia, reevaluar contra el snapshot actual. Reutilizar identidades de lote y operación en reintentos, con entrada estable y hash determinista. Un commit incierto se reconciliará según la sección 8 antes de repetirlo. Un registro o corrección ya confirmado no vuelve a sumar botellas, rechazos, agregados ni contadores confirmados de auditoría.

#### 4.6.7. Auditoría y criterios de aceptación

Auditar revisión/huella, origen, archivo y fila, contrato, snapshot consultado, claves `K` y firmas/grupos afectados, hash, operación prevista/confirmada, valores discrepantes, motivo y estado de revisión. Separar filas físicas, duplicados, filas nuevas, conflictos, inválidos, posibles revisiones y grupos de inspección resueltos. La cantidad de filas de defectos leídas no es la cantidad de botellas inspeccionadas. No declarar un lote o snapshot completo si depende de unidades o grupos pendientes, conforme a la sección 8.

Los candidatos conflictivos se conservarán en el registro de incidencias del almacén persistente de control/auditoría descrito en 9.1, separado de la tabla Delta de negocio. Guardar los 18 campos completos de la fila candidata, sus valores originales de evidencia, la clave normalizada, el contenido/hash aceptado contra el que se compara, campos discrepantes, origen, archivo/revisión/fila, lote y estado `PENDING_REVIEW`. Este registro debe permitir revisar el caso aunque el CSV original ya no esté disponible; no basta una línea de consola, un contador de errores o una referencia a un archivo que puede desaparecer.

Si una fila aceptada tiene `RECH = 2` y llega la misma clave con `RECH = 3`, la tabla activa conserva `2` y el registro de revisión guarda íntegra la candidata con `3`. No producir `5`, ignorar la candidata, duplicar la fila en los datos activos ni aplicar la corrección automáticamente. Repetir esa candidata no creará otro caso idéntico: identificarlo de forma determinista por contrato, clave, hash aceptado/candidato y motivo, manteniendo por separado las evidencias de nuevas recepciones y reintentos. La resolución posterior conservará decisión, responsable o proceso autorizado y trazabilidad de cualquier corrección, sin eliminar la evidencia del desacuerdo.

- La tabla conserva los 18 nombres en el orden original, incluidos `RECH` y los diez canales, sin eliminar ceros ni cavidades vacías/de no producción de la fuente.
- Las cinco primeras filas del ejemplo AV23 permanecen como cinco defectos distintos, pero aportan una base de 96 al grupo correspondiente, no 480.
- La fila de máquina `12` con 89 inspeccionadas permanece en otro grupo. Una máquina distinta con el mismo contador también permanece separada; no se deduplica por cantidad numérica.
- Cambiar únicamente `COD_DEF` conserva ambas filas de defecto y cuenta una sola base compartida si el grupo y `INSPECC` coinciden. Cambiar molde, ramal, máquina, fecha, origen o cavidad no permite fusionar bases por parecido.
- Dos filas que solo difieren en `CAVIDAD` tienen claves y grupos independientes y no generan por ese motivo un conflicto de identidad. La firma de posibles revisiones también distingue esas cavidades.
- Misma `K` con distinto rechazo/canal conserva la fila aceptada y guarda los 18 campos de la candidata en revisión persistente; no se elimina como supuesto duplicado exacto ni se publica otra fila activa. El ejemplo de 2 y 3 rechazos no produce 5 ni una sustitución automática; repetir la candidata no duplica el caso de revisión.
- Misma `S` con 96 y 97 inspeccionadas no produce 193 ni dos versiones activas sin resolución. Reprocesar claves con cavidad nula reconoce la coincidencia y tampoco genera duplicados.
- El caso real `RECH = 1`, `RECH1 = 1`, `RECH4 = 1` se conserva íntegro; no se recalcula el total a partir de la suma de canales.
- Las 2.304 filas del archivo AV23 del día 14 conservan su contenido original tras tipado; reprocesarlo no duplica filas, bases, rechazos ni versiones Delta sin cambios.
- Dos orígenes con iguales códigos/fechas no se mezclan. Fechas equivalentes y nulos normalizados no generan falsas novedades; cantidades inválidas quedan apartadas con evidencia.
- Fallos antes/después del commit y reintentos no duplican efectos; una ejecución sin novedades escribe únicamente control/auditoría, no la tabla de negocio.
- La comparación con el histórico conserva filtros, tasas y denominadores aprobados; el score MIA no pasa silenciosamente de `LISTA_RECH` a una suma de inspeccionadas repetidas de `LISTA_DEFECTOS`.

### 4.7. Contrato acordado: `LISTA_RECH`

Estado: implementación local de ensayo completada en la entrega 08; 202.931 filas reales conciliadas con sus nueve campos íntegros, replay sin escrituras y recuperación probada. Evidencia, coste de control observado y límites en su ficha de 17.12. Versión inicial del contrato: `lista_rech_v1`. Aplicar las garantías de `LISTA_DEFECTOS` de 4.6, adaptando su clave y la comparación a las nueve columnas de esta fuente, sin eliminar ni transformar sus contadores de negocio. La integración con filtros enriquecidos, producciones y score completo no forma parte de esta carga de origen y sigue pendiente antes de conectar la app.

#### 4.7.1. Finalidad y compatibilidad con el histórico

`LISTA_RECH` contiene inspeccionadas, rechazadas y rechazos automáticos por contexto de fecha, molde, ramal, máquina y cavidad. No incluye código de defecto. Según `Fenix tablas.xlsx`, hoja «Eventos», su frecuencia de grabación es de diez minutos. Se conserva la interpretación temporal del histórico: no deducir por la posición de la fila una ventana distinta ni utilizar la fecha de recepción como fecha del registro.

Decisión confirmada: `LISTA_RECH` aporta preferentemente las inspeccionadas del denominador MIA; `LISTA_DEFECTOS` aporta los rechazos por defecto del numerador. Son fuentes complementarias, no intercambiables. La introducción de Delta no cambiará fórmulas, filtros o elección del denominador ni activará silenciosamente la suma legacy de inspeccionadas repetidas entre defectos. Aplicar la validación de compatibilidad y las referencias de código descritas en 4.6.5.

Ejemplo real de `AV23/20261014_LISTA_RECH.csv`, bajo `C:/Users/iker.villen/Desktop/AI_tool/Datos/1.App_septiembre/Fenix_Aiala/Fenix`: la primera fila tiene fecha `14/10/2026 00:00:00`, molde `0`, ramal `1`, máquina `13`, `INSPECC = 96`, `RECH = 12`, `RECH_AUTO = 0`, cavidad y `FECHA_R` vacías. Esa base de 96 se repite en cinco defectos distintos de `LISTA_DEFECTOS`, pero no son 480 inspeccionadas. La segunda fila de `LISTA_RECH` contiene 18 inspeccionadas y cero rechazos para molde `1`, cavidad `8F`: se conserva aunque no haya rechazo.

La muestra AV23 del día 14 contiene 17.831 filas, sin duplicados bajo la clave de 4.7.3. Esta comprobación no exime de revisar cada nueva entrada o actualización futura.

#### 4.7.2. Esquema original y reglas de tipado

Conservar las nueve columnas originales, sin renombrarlas ni reordenarlas. El CSV usa `;`, no tiene cabecera y deja una posición vacía tras el separador final, que no es una décima columna de negocio.

| Posición | Columna original | Tipo Delta / Arrow | Nulos | Validación y significado |
| --- | --- | --- | --- | --- |
| 1 | `FECHA` | `TIMESTAMP` | No | Fecha/hora del registro, con la política temporal del origen. |
| 2 | `MOLDE` | `BIGINT` / `int64` | No | Entero no negativo; `0` significa molde no leído, no un total de todos los moldes. |
| 3 | `LINEA` | `BIGINT` / `int64` | No | Código numérico del ramal; la procedencia AV23 u otra línea se conserva en el ámbito técnico. |
| 4 | `COD_MAQ_ZF` | `BIGINT` / `int64` | No | Código original de máquina de inspección. |
| 5 | `INSPECC` | `BIGINT` / `int64` | No | Cantidad entera no negativa, conservada sin incrementos por reintentos. |
| 6 | `RECH` | `BIGINT` / `int64` | No | Contador original de rechazadas, entero no negativo. |
| 7 | `RECH_AUTO` | `BIGINT` / `int64` | No | Contador original de rechazos automáticos, entero no negativo e independiente de `RECH`. |
| 8 | `CAVIDAD` | `STRING` / `string` | Sí | Conservar texto, incluidos `8F`, `1C` o `99Z`; vacío se normaliza a nulo. |
| 9 | `FECHA_R` | `TIMESTAMP` | Sí | Fecha del posible rechazo automático asociado, según Fenix; vacío se normaliza a nulo. |

Validar conversión entera sin pérdida y rango de 64 bits con signo, sin truncar fracciones o usar coma flotante. Cantidades obligatorias ausentes, negativas o no interpretables quedan apartadas, no convertidas a cero. Los ceros válidos se conservan: no eliminar filas porque `RECH`, `RECH_AUTO` o `INSPECC` sean cero.

Normalizar ambas fechas con formato día/mes/año y hora de uno o dos dígitos, conforme a la zona horaria y resolución de cambio de hora del origen. No completar `FECHA_R` con `FECHA`, la recepción o una fecha ficticia, ni exigir que ambas coincidan. Mantener evidencia de las representaciones originales.

No imponer `RECH_AUTO <= RECH`, `RECH_AUTO <= INSPECC`, `RECH_AUTO = RECH` ni sustituir un contador por su suma. En la fila 5039 de la muestra, para `14/10/2026 02:40:00`, aparecen `INSPECC = 5`, `RECH = 0`, `RECH_AUTO = 13` y `FECHA_R = 14/10/2026 02:32:45`. Se conserva esa información íntegra; esas diferencias no son por sí solas errores de esquema. Tampoco exigir que un `RECH_AUTO` positivo traiga siempre `FECHA_R`: hay casos reales con fecha vacía.

#### 4.7.3. Clave, igualdad con nulos y posibles revisiones

La clave equivalente a la acordada para `LISTA_DEFECTOS`, sin `COD_DEF`, será:

`K_R = (origen, FECHA, MOLDE, LINEA, COD_MAQ_ZF, INSPECC, CAVIDAD)`.

`origen` será el identificador estable de planta y procedencia Fenix, no el archivo fechado, lote o fila. Materializarlo mediante metadatos técnicos posteriores a las nueve columnas originales o separación física por origen, de modo que el escritor pueda usarlo para discriminar identidades. No mezclar códigos iguales de distintas líneas/orígenes.

Cambiar fecha, molde, ramal, máquina, origen o cavidad diferencia identidades; no fusionarlas por compartir cantidad inspeccionada. Comparar cavidades nulas con igualdad explícita de nulos y serialización determinista, como en 4.6.3, sin inventar valores que puedan confundirse con cavidades reales.

Una repetición solo es idéntica si coinciden la clave y los nueve campos normalizados. Para una misma `K_R`, diferencias en `RECH`, `RECH_AUTO` o `FECHA_R` generan un caso de revisión, no una inserción adicional ni una actualización automática. Esto incluye una `FECHA_R` antes vacía que llegue informada: conservar la nueva evidencia sin sustituir el registro aceptado hasta resolver la modificación.

La firma de posible revisión del contador será:

`S_R = (origen, FECHA, MOLDE, LINEA, COD_MAQ_ZF, CAVIDAD)`.

Si la misma `S_R` aparece con otra cantidad `INSPECC`, no interpretar automáticamente la nueva clave como otras botellas inspeccionadas. Registrar posible corrección/versionado, mantener la candidata íntegra y no sumarla ni elegir máximo, mínimo o última llegada como resolución del conflicto. La eventual corrección confirmada de `INSPECC` requerirá una operación explícita de sustitución de identidad y trazabilidad anterior/nueva, sin dejar dos versiones activas en los datos de consumo.

#### 4.7.4. Conteos y relación con `LISTA_DEFECTOS`

Una observación aceptada de inspección se contará una vez en su contexto `S_R`. `LISTA_RECH` no necesita la deduplicación entre códigos de defecto que requiere `LISTA_DEFECTOS`, pero sí deduplicación de revisiones, reintentos y filas repetidas. No sumar cantidades distintas para una misma observación pendiente de resolución ni utilizar la cantidad numérica como identidad global.

Conservar por separado los contextos de máquina, molde, cavidad, ramal, origen y fecha. Dos máquinas con igual `INSPECC` no son duplicados por ese motivo, y máquinas con cantidades diferentes tampoco se fusionan en una misma observación. Las agregaciones posteriores usarán las dimensiones y reglas de la métrica histórica: una suma entre máquinas en serie no demuestra un total de botellas físicas únicas.

No sumar directamente las filas después de un join con `LISTA_DEFECTOS`: una observación de `LISTA_RECH` puede corresponder a varios códigos de defecto y quedar multiplicada. Resolver bases/denominadores antes de ese cruce o utilizar una relación que garantice su cardinalidad; comprobar que 96 inspeccionadas siguen siendo 96 al incorporar los cinco defectos del ejemplo.

La elección de inspeccionadas para el score MIA conservará la regla histórica verificada de 4.6.5 y sus filtros, incluida la distinción entre una regla de agregación de una métrica y la resolución de conflictos de fuente. No usar esa regla como permiso para escoger arbitrariamente una de dos revisiones de un contador, ni sumar automáticamente todas las máquinas o todos los moldes para sustituir el denominador existente.

`RECH`, `RECH_AUTO` y `FECHA_R` se mantendrán independientes para sus usos funcionales. No calcular rechazos por defecto desde `LISTA_RECH`, que no tiene `COD_DEF`, ni obtener botellas únicas rechazadas sumando defectos o ambos contadores sin una regla de negocio validada.

#### 4.7.5. Escrituras, conflictos e idempotencia

Revisar cada archivo nuevo y cada revisión estable de uno actualizado, incluidas fechas ya procesadas, según la sección 7. Antes de escribir, validar estructura/tipos, normalizar y comparar claves, firmas y contenido dentro del lote y contra el snapshot publicado. No activar evolución automática de esquema ante nuevas columnas o posiciones incompatibles.

| Situación | Resultado | Tratamiento |
| --- | --- | --- |
| Misma `K_R` y mismos nueve campos | Duplicado / `NO_CHANGE` | No insertar ni actualizar; conservar primera recepción y registrar la comprobación. |
| `K_R` nueva, válida y sin posible revisión contradictoria | `INSERT` | Insertar una sola fila con sus nueve campos, origen e intención/confirmación auditadas. |
| Contexto diferente por máquina, molde, ramal, cavidad, fecha u origen | Candidata independiente | No fusionar por igualdad de cantidades; validar en su ámbito y aplicar después la agregación de la métrica. |
| Misma `K_R` con `RECH`, `RECH_AUTO` o `FECHA_R` diferentes | `CONFLICT` | Conservar lo aceptado y guardar íntegra la candidata en revisión persistente, sin sumar, descartar, duplicar en datos activos ni sobrescribir. |
| Misma `S_R` con otro `INSPECC` | Posible revisión / `CONFLICT` | No publicar otra versión activa sin resolver la identidad y precedencia. |
| Tipo, fecha o campo obligatorio inválidos | `INVALID` | Apartar con evidencia y motivo; no imputar valores ni aceptar conversiones con pérdida. |
| Fila ausente de un volcado posterior | Sin baja automática | Conservar lo publicado; la ausencia no autoriza borrado. |

Aplicar el mismo circuito persistente de revisión de 4.6.7, guardando aquí los nueve campos completos de la candidata, valores originales, clave, fila/hash aceptados, diferencias, procedencia y estado `PENDING_REVIEW` en el almacén de control/auditoría de 9.1. No basta un contador o una línea de consola. Repetir la misma candidata no crea otro caso idéntico: usar identidad determinista de contrato, clave, hash aceptado/candidato y motivo, con evidencias de recepción separadas. La revisión posterior registrará decisión y cualquier corrección explícitamente autorizada.

Publicar solo candidatas resueltas bajo el modelo de único escritor y comprobar unicidad por `K_R`, con igualdad correcta para cavidades nulas. El append de novedades es lógico y validado, no concatenación ciega de cada CSV. No sobrescribir coincidencias, reemplazar tablas, actualizar `last_seen` en Delta por repeticiones ni modificar archivos fuente. Si no hay inserciones o correcciones explícitamente resueltas, no invocar escrituras, reescribir Parquet ni generar commits Delta; sí registrar resultados e incidencias en control.

El hash de negocio incluirá los nueve campos normalizados; no archivo, recepción, fila, intento o lote. No sustituye a la clave. Preparar intenciones y confirmar resultados por operación estable, usando la misma revisión de entrada en los reintentos. Ante un commit incierto, reconciliar según la sección 8 antes de repetir; no duplicar filas, botellas, rechazos, agregados ni contadores confirmados. Un cambio de snapshot por concurrencia exige reevaluar los candidatos, no aplicar una decisión obsoleta.

#### 4.7.6. Auditoría y criterios de aceptación

Conservar revisión/huella, archivo, origen, fila de evidencia, contrato, snapshot consultado, clave/firma, hash, operación prevista/confirmada y resultado. Separar filas físicas, novedades, coincidencias, duplicados, inválidos, posibles revisiones y casos de revisión pendientes; ninguno de esos contadores técnicos equivale por sí solo a botellas inspeccionadas. No informar éxito completo si quedan unidades requeridas pendientes conforme a la sección 8.

- Conservar los nueve nombres en el orden original, incluidos `RECH_AUTO`, `CAVIDAD` y `FECHA_R`, con tipos/nulos acordados y sin perder filas por tener cero rechazos.
- Las 17.831 filas del archivo AV23 del día 14 conservan su contenido tras tipado. Reprocesarlas o reenviarlas no duplica filas ni cantidades y no produce otra versión Delta si no hay cambios válidos.
- La observación de 96 inspeccionadas aporta una base de 96 al contexto correspondiente; el cruce con los cinco defectos no la convierte en 480. La fila con 18 inspeccionadas y cero rechazos también se conserva.
- Contextos con otra máquina, molde, ramal, cavidad, fecha u origen se mantienen separados aunque el contador coincida. Dos cavidades nulas normalizadas reconocen la misma clave en cada reintento.
- Misma clave con `RECH = 2` y después `3` conserva el valor aceptado y guarda íntegra la candidata para revisión, sin producir 5 ni sobrescribir. Repetir el conflicto no duplica el caso.
- Cambios de `RECH_AUTO` o `FECHA_R`, incluida una fecha antes vacía, siguen el mismo circuito; no se completan automáticamente.
- Misma firma con 96 y 97 inspeccionadas no produce 193 ni dos versiones activas antes de resolver la posible corrección.
- Se conserva el caso real de 5 inspeccionadas, 0 rechazadas y 13 rechazos automáticos; no imponer relaciones de subconjunto o sumas no demostradas entre contadores. Una fecha automática vacía se mantiene nula.
- Fechas equivalentes de uno o dos dígitos no generan novedades. Campos obligatorios inválidos o fuera de rango quedan apartados, con evidencia persistente.
- Reintentos, fallos parciales y commits inciertos se recuperan sin duplicar efectos; sin novedades válidas se escribe solo control/auditoría, no la tabla de negocio.
- La validación con el histórico mantiene las tasas/denominadores aprobados del score MIA: inspeccionadas preferentemente de `LISTA_RECH` y rechazos por defecto de `LISTA_DEFECTOS`, con los mismos filtros y ventanas.

### 4.8. Contrato acordado: `LISTA_AR`

Estado: implementación local de ensayo completada en la entrega 09: 177 tramos reales conciliados íntegramente, agrupaciones/nulos conservados y replay sin escrituras; referencias y cierres incrementales probados. Evidencia y límites en su ficha de 17.12. Versión inicial del contrato: `lista_ar_v1`. El diseño del cómputo de minutos-cavidad se tratará posteriormente y no se decide en esta sección.

#### 4.8.1. Finalidad y conservación de tramos

`LISTA_AR` contiene avisos/rechazos con sus fechas de apertura/cierre, máquina, cavidad, modelo, procedencia y clase de registro. Las relaciones de interpretación son `COD_AR → SIP_DEFECTO.CODIGO`, `STATUS → SIP_DEFECTO_STATUS.CODIGO` y `TIPO → SIP_DEFECTO_AR.CODIGO`. `STATUS` no indica gravedad, zona ZC/ZF ni estado abierto/cerrado; aquí indica procedencia, a diferencia de su significado en `MP9_CAMBIO_MODELO`.

Conservar los 12 campos originales, en su orden, y una fila por cada tramo distinto que proporciona la fuente. No agrupar, fusionar, compactar intervalos ni eliminar filas por compartir `ID_Agrupación`, defecto, cavidad o cualquier subconjunto de atributos. `ID_Agrupación` es una relación entre registros y puede repetirse o faltar; no es un identificador único de fila ni una clave suficiente para actualizarla.

Ejemplo contrastado: filas 5 y 6 de `AV21/20261014_LISTA_AR.csv`, bajo `C:/Users/iker.villen/Desktop/AI_tool/Datos/1.App_septiembre/Fenix_Aiala/Fenix`. Ambas tienen defecto `129` (`Pliegue cuello`), máquina `21`, cavidad `2F`, tipo `3` (`Aviso ZF`), modelo `2031/487`, color `O`, proceso `PS`, molde `34` y agrupación `39954`. La primera registra `14/10/2026 09:59:33 → 10:33:51` con `STATUS = 1` (`Tiempo Real`); la segunda registra `10:33:51 → 11:13:43` con `STATUS = 2` (`Muestreo IFA`). Se publicarán como dos filas independientes, relacionadas por su agrupación, sin que la segunda sustituya a la primera.

No basta tampoco con agrupación y `STATUS`: en AV32 la agrupación `19607` tiene dos tramos consecutivos con `STATUS = 2`. En AV23 del día 13 existen cinco filas con agrupación vacía. Ambos casos deben poder conservarse, sin inventar una agrupación ni asumir que una repetición de ID indica duplicación.

#### 4.8.2. Esquema original y tipado

Los nombres documentados en `Fenix tablas.xlsx`, hoja «Eventos», se conservan en este orden. El CSV usa `;`, carece de cabecera y su posición vacía tras el separador final no constituye otro campo.

| Posición | Columna original | Tipo Delta / Arrow | Nulos | Significado y comprobación |
| --- | --- | --- | --- | --- |
| 1 | `COD_AR` | `BIGINT` / `int64` | No | Código original del defecto o motivo, interpretado por `SIP_DEFECTO`. |
| 2 | `FECHA_ON` | `TIMESTAMP` | No | Inicio del tramo, no necesariamente apertura inicial de toda la agrupación. |
| 3 | `MAQ_IS` | `BIGINT` / `int64` | No | Número de máquina IS, por ejemplo `21` para AV21. |
| 4 | `CAVIDAD` | `STRING` / `string` | Sí | Cavidad original; conservar texto y normalizar vacío a nulo. |
| 5 | `STATUS` | `BIGINT` / `int64` | No | Código de procedencia del registro. |
| 6 | `TIPO` | `BIGINT` / `int64` | No | Código de clase de aviso/rechazo. |
| 7 | `MODELO` | `STRING` / `string` | No | Código de botella, no ID interno Fenix. Preservar `/`, letras y ceros iniciales. |
| 8 | `FECHA_OFF` | `TIMESTAMP` | Sí | Cierre del tramo; vacío se normaliza a nulo, nunca a la hora de recepción. |
| 9 | `COLOR` | `STRING` / `string` | No | Valor textual original. No requiere reincorporar un catálogo descartado para conservarlo. |
| 10 | `PROCESO` | `STRING` / `string` | No | Valor textual original, como `SS` o `PS`, sin reinterpretación automática. |
| 11 | `MOLDE` | `BIGINT` / `int64` | No | Número de molde, no identificador del modelo de botella. |
| 12 | `ID_Agrupación` | `BIGINT` / `int64` | Sí | Relación entre tramos; vacío se normaliza a nulo. No sustituirlo por un ID generado como si procediera de Fenix. |

Validar enteros decimales y rango de 64 bits con signo sin pasar por coma flotante, truncar o convertir ausencias a cero. Los textos obligatorios deben ser no vacíos; cualquier normalización de espacios de extremos será determinista, conservará la evidencia original y no cambiará palabras, mayúsculas o etiquetas de negocio.

Normalizar fechas equivalentes con hora de uno o dos dígitos antes de comparar, con formato día/mes/año y política de zona horaria/cambio de hora del origen. Un cierre informado no puede preceder a su inicio; si ocurre, apartar con evidencia sin corregirlo. No completar un cierre ausente mediante el inicio de otra fila, la fecha del archivo, recepción o fin de turno.

Las referencias se resolverán con los diccionarios aprobados y su ámbito. Conservar códigos originales y registrar referencias aún no resueltas o contradictorias sin inventar etiquetas ni borrar la fila de evidencia. No excluir eventos porque falte `ID_Agrupación` ni fusionarlos por compartir una descripción traducida. Las incidencias de referencia seguirán el protocolo de dependencias/revisión antes de declarar íntegra la interpretación requerida para consumo.

#### 4.8.3. Comparación íntegra e identidad estable del tramo

Para detectar una repetición exacta se compararán `origen` y los 12 campos normalizados, incluidos inicio, cierre, procedencia y agrupación. La huella de contenido incluirá los 12 campos, sin metadatos de recepción. Solo la coincidencia completa permite omitir otra escritura como duplicado idéntico; no utilizar únicamente agrupación, agrupación/procedencia o defecto/cavidad.

La identidad estable para localizar el tramo y completar su cierre será:

`K_A = (origen, COD_AR, FECHA_ON, MAQ_IS, CAVIDAD, STATUS, TIPO, MODELO, COLOR, PROCESO, MOLDE, ID_Agrupación)`.

`FECHA_OFF` participa en la comparación completa, pero no en la identidad estable: pasar de nulo a un cierre válido no debe crear otro tramo. Esta separación entre coincidencia íntegra e identidad de actualización conserva toda la información y permite cierres idempotentes. No utilizar el hash de todos los campos como sustituto de `K_A` para localizar una entrada abierta.

El origen será estable por planta/procedencia Fenix, no por archivo fechado o posición de fila, y deberá poder utilizarse en las condiciones de escritura mediante metadatos técnicos posteriores a las 12 columnas o separación física por origen. No mezclar identificadores de agrupación iguales de diferentes orígenes. Comparar `CAVIDAD` e `ID_Agrupación` nulos como iguales con semántica explícita, serialización tipada y determinista; no rellenarlos con valores reales o ceros inventados.

Cambiar inicio, procedencia u otro componente de la identidad produce una candidata a tramo independiente, aunque se mantenga la agrupación. No deducir que es una corrección del tramo anterior por compartir ID; evaluar su contenido y las comprobaciones de calidad, preservando los tramos legítimos. Una corrección identificada de atributos de identidad requerirá una operación explícita y auditable, sin sustituir automáticamente ni dejar dos versiones activas del mismo tramo corregido.

#### 4.8.4. Duplicados, cierres y contradicciones

Procesar cada archivo nuevo o revisión estable conforme a la sección 7, incluidas filas iniciadas en fechas anteriores. Un CSV del día 14 puede contener tramos iniciados el día 13; no limitar la lectura o búsqueda al día del nombre de archivo. Agrupar por `K_A` solo para comparar versiones del mismo tramo, no para agrupar toda la historia de un evento de AR.

| Situación | Resultado | Tratamiento |
| --- | --- | --- |
| Misma identidad y mismos 12 campos normalizados | Duplicado / `NO_CHANGE` | Conservar una fila y su primera recepción; no escribir otra versión. |
| Nueva identidad válida, incluso con agrupación ya presente | `INSERT` | Insertar otra fila independiente con sus 12 campos; conservar las anteriores. |
| Mismo tramo abierto que recibe un único cierre coherente, no anterior al inicio | `UPDATE` de cierre | Completar solo `FECHA_OFF` sobre el mismo tramo, con evidencia y auditoría. |
| Versiones abiertas y cerradas del mismo tramo con un único cierre no nulo válido | Consolidación de cierre | Conservar una fila cerrada, sin elegir por orden físico ni crear otra fila por la observación abierta. |
| Tramo ya cerrado que recibe una versión sin cierre y demás campos idénticos | Sin regresión / `NO_CHANGE` | Mantener el cierre informado; la ausencia de fecha no es una orden de reapertura. |
| Mismo tramo con dos cierres no nulos diferentes | `CONFLICT` | Mantener lo aceptado y guardar íntegra la candidata contradictoria para revisión; no elegir máximo, mínimo o última llegada. |
| Mismo ID de agrupación con otro inicio, `STATUS` u otros atributos legítimos de tramo | Registro independiente | No deduplicar ni sobrescribir solo por la agrupación; validar e insertar la nueva identidad. |
| Inicio/cierre inválidos, fin anterior al inicio, tipo incompatible o campo obligatorio ausente | `INVALID` | Apartar con evidencia y motivo, sin inventar fechas o completar valores. |
| Tramo ausente de un volcado posterior | Sin baja automática | Mantener lo publicado; no borrar, fusionar ni cerrar por ausencia. |

La consolidación de cierres exige que todos los demás campos de `K_A` coincidan. Si hay valores incompatibles, no construir una fila mezclando fragmentos de distintas candidatas. Un cambio de cierre ya informado o una corrección de identidad requerirá resolución explícita y trazabilidad anterior/nueva. No interpretar sucesión temporal, contigüidad o solapamiento como duplicado exacto.

Conservar las contradicciones íntegras en el almacén persistente de control/auditoría de 9.1, según el circuito de 4.6.7: los 12 campos candidatos, valores originales de evidencia, `K_A`, contenido/hash aceptados, campos discrepantes, origen, archivo/revisión/fila, lote, motivo y estado `PENDING_REVIEW`. No basta una línea de consola. La misma candidata repetida no creará otro caso idéntico: usar identidad determinista de contrato, clave, hashes aceptado/candidato y motivo, con evidencias de recepción separadas.

La fila aceptada se conserva hasta resolver el caso; la candidata no se descarta, no se suma, no se publica como otro tramo para eludir el conflicto y no sobrescribe lo aceptado. Mantener la evidencia y la decisión de revisión aunque se autorice después una corrección.

#### 4.8.5. Escrituras idempotentes y cálculo pendiente de minutos-cavidad

Preparar una única decisión resuelta por identidad antes de escribir, con snapshot consultado, hash previo/candidato y operación estable. Publicar bajo el modelo de único escritor: insertar identidades nuevas y actualizar exclusivamente cierres validados. No reemplazar indiscriminadamente columnas, filas o tablas; no modificar fuentes ni actualizar `last_seen` en Delta por observaciones repetidas.

Si no hay inserciones o cierres/correcciones explícitamente resueltos, no invocar escrituras, reescribir Parquet ni crear commits de esta tabla. Sí registrar revisiones comprobadas, repeticiones e incidencias en control. Usar la misma revisión estable e identidad de operación en reintentos; reconciliar commits inciertos conforme a la sección 8 antes de repetir una escritura. Una inserción o cierre ya confirmado no vuelve a generar efecto de negocio ni contabilización confirmada.

El control conservará archivo/revisión/huella, origen, filas de evidencia, contrato, snapshot, identidad, hash, intención, confirmación y resultado. Distinguir filas leídas, tramos nuevos, cierres, coincidencias, duplicados, contradicciones, inválidos y dependencias de referencia. Contar filas no equivale a contar agrupaciones ni eventos conceptuales. No declarar completo un lote o snapshot con unidades requeridas pendientes, conforme a la sección 8.

**El cómputo de minutos-cavidad queda pendiente de una decisión posterior.** Esta ingesta no calculará, sumará, fusionará o repartirá minutos, no recortará intervalos a turnos o producciones y no creará duraciones sustitutas. Se conservan todos los tramos y sus campos originales para permitir decidir después cómo tratar avisos/rechazos simultáneos, cambios de procedencia, agrupaciones, cierres ausentes y límites temporales. Tampoco se equipara un aviso/rechazo a una parada física de la cavidad.

La preservación de filas no obliga a contarlas como eventos conceptuales independientes ni resuelve por sí sola sus minutos-cavidad. La lógica actual de la aplicación no se modifica al editar este plan; cualquier futura integración o cambio de cálculo se validará por separado, sin que la deduplicación de ingesta elimine información temporal necesaria.

#### 4.8.6. Criterios de aceptación para la implementación

- Conservar las 12 columnas con sus nombres y orden originales; agrupación/cavidad vacías y cierres no informados mantienen sus nulos tipados y evidencia.
- El caso AV21 `39954` produce dos filas: `09:59:33–10:33:51`, `STATUS = 1`, y `10:33:51–11:13:43`, `STATUS = 2`, ambas del 14/10/2026. No se fusionan ni una sustituye a la otra.
- La agrupación AV32 `19607` conserva sus dos tramos con `STATUS = 2`; agrupación/procedencia tampoco es una clave de fila.
- Las cinco filas AV23 del día 13 sin agrupación se conservan si son válidas; no generan un ID Fenix ficticio ni se eliminan como duplicadas por compartir nulo.
- Reprocesar filas idénticas, fechas equivalentes con una o dos cifras en la hora o claves con nulos reconocidos no duplica tramos ni genera commits sin cambios.
- Un tramo abierto se cierra una sola vez al llegar un único fin válido; repetirlo o recibir después la versión abierta no lo duplica ni reabre. Dos fines diferentes conservan lo aceptado y guardan íntegra la candidata para revisión persistente, sin duplicar casos en reintentos.
- Un inicio o procedencia distintos pueden identificar otro tramo de la misma agrupación y no se interpretan automáticamente como corrección del anterior.
- Fecha imposible, fin anterior al inicio, conversión numérica con pérdida o campo obligatorio inválido se apartan con evidencia; no se completan usando recepción o tramos vecinos.
- Fallos parciales y commits inciertos se reconcilian sin duplicar inserciones, cierres ni contadores de auditoría. Las discrepancias pendientes no se presentan como éxito completo.
- La carga no calcula ni altera minutos-cavidad: no fusiona intervalos, recorta fechas o toma agrupación/fila como número de eventos conceptuales. Se conserva la información para definir ese cálculo posteriormente.

### 4.9. Contrato acordado: `CONT_ZC`

Estado: implementación local de ensayo completada en la entrega 10: 827 observaciones reales conciliadas con sus 17 campos, velocidades DECIMAL(12,2), fórmula/elegibilidad verificadas y replay sin escrituras. Evidencia y límites en su ficha de 17.12. Versión inicial del contrato: `cont_zc_v1`. Con ello las nueve fuentes tienen implementación de ensayo; asignación a producciones, integración del score y operación productiva siguen pendientes.

#### 4.9.1. Finalidad, evidencia y conservación

`CONT_ZC` contiene contadores y velocidades de zona caliente, con frecuencia de grabación documentada de diez minutos en `Fenix tablas.xlsx`, hoja «Eventos». Se conservarán sus 17 columnas, en el orden y con los nombres originales, aunque algunas aparezcan a cero o estén documentadas como no utilizadas. No sumar entre sí puntos de medida que pueden contar las mismas botellas, ni eliminar columnas por coincidencia con otro contador en la muestra.

Se han examinado los archivos `20261013_CONT_ZC.csv` y `20261014_CONT_ZC.csv` de `AV21`, `AV22`, `AV23`, `AV31` y `AV32`, bajo `C:/Users/iker.villen/Desktop/AI_tool/Datos/1.App_septiembre/Fenix_Aiala/Fenix`. No se han encontrado duplicados por máquina/fecha dentro de cada archivo; los campos están informados y las velocidades presentan dos decimales. Estas observaciones describen la muestra y no sustituyen la validación de cada revisión futura.

Ejemplo real, primera fila de `AV23/20261014_CONT_ZC.csv`: máquina `23`, fecha `14/10/2026 00:00:00`, `RUEDA = 1583`, `TEORICO_A = 1614`, `TEORICO_B = 0`, `EXPULSOR = 1613`, `VEL_A = 80.01`, `VEL_B = 0.00` y `SALIDA_MAQ_IS = 1`. Para esa observación, la fórmula acordada produce aproximadamente `98,08 %`.

El CSV carece de cabecera, usa `;` y deja una posición vacía tras el separador final. Se comprobarán los 17 campos reales antes de tiparlos; esa posición vacía no es una columna adicional. Cambios de estructura, columnas o posiciones requieren revisión y versión de contrato, no evolución automática ni proyección silenciosa que pierda datos.

#### 4.9.2. Esquema original y tipado acordado

| Posición | Columna original | Tipo Delta / Arrow | Nulos | Significado documentado y regla |
| --- | --- | --- | --- | --- |
| 1 | `MAQ_IS` | `BIGINT` / `int64` | No | Identificador numérico de máquina IS; por ejemplo `23` para AV23. |
| 2 | `FECHA` | `TIMESTAMP` | No | Fecha/hora de la observación, conforme a la política temporal del origen. |
| 3 | `CONT_ZC_1` | `BIGINT` / `int64` | No | Botellas producidas por máquina A según la documentación. Conservar el contador original. |
| 4 | `CONT_ZC_2` | `BIGINT` / `int64` | No | Botellas producidas por máquina B según la documentación. Conservar el contador original. |
| 5 | `MAQUINA_A` | `BIGINT` / `int64` | No | Contadas a la salida de máquina A. |
| 6 | `MAQUINA_B` | `BIGINT` / `int64` | No | Contadas a la salida de máquina B. |
| 7 | `TUNEL` | `BIGINT` / `int64` | No | Contador de salida del túnel, documentado como no utilizado; conservarlo. |
| 8 | `RUEDA` | `BIGINT` / `int64` | No | Contador a la salida de rueda; numerador del rendimiento acordado. |
| 9 | `IMP_ARCA` | `BIGINT` / `int64` | No | Contador cuyo significado no especifica la documentación; conservar sin reinterpretar. |
| 10 | `TEORICO_A` | `BIGINT` / `int64` | No | Botellas teóricas cortadas por máquina A. |
| 11 | `TEORICO_B` | `BIGINT` / `int64` | No | Botellas teóricas cortadas por máquina B. |
| 12 | `EXPULSOR` | `BIGINT` / `int64` | No | Contador a la salida del expulsor; conservar separado de rueda y máquinas. |
| 13 | `CONT_ZC_4` | `BIGINT` / `int64` | No | Documentado como no utilizado; conservarlo. |
| 14 | `VEL_A` | `DECIMAL(12,2)` / `decimal128(12,2)` | No | Velocidad de corte del feeder A, con valor decimal exacto. |
| 15 | `VEL_B` | `DECIMAL(12,2)` / `decimal128(12,2)` | No | Velocidad de corte del feeder B, con valor decimal exacto. |
| 16 | `CONT_ZC_3` | `BIGINT` / `int64` | No | Documentado como no utilizado; conservarlo. |
| 17 | `SALIDA_MAQ_IS` | `BIGINT` / `int64` | No | Indicador Fenix: `0` selecciona contadores de máquinas A/B; `1`, expulsor. No convertirlo a otro campo ni usarlo para alterar la fórmula de rueda. |

Los contadores serán enteros no negativos, con conversión directa sin pérdida y dentro del rango de 64 bits con signo. Las velocidades serán decimales no negativos: interpretar el punto decimal explícitamente y validar precisión/escala sin redondear o truncar valores incompatibles. Representaciones equivalentes como `80.01` y `80.010`, o `80` y `80.00`, normalizadas a su valor exacto no deben generar diferencias artificiales; un valor con decimales significativos más allá de la escala o fuera de rango quedará apartado, no redondeado. No pasar por coma flotante para validar o generar el hash.

Un campo obligatorio vacío, una cantidad no interpretable o negativa y un indicador de salida fuera de `0`/`1` se apartarán con evidencia y motivo. No convertir ausencias a cero. Los ceros válidos se conservan, incluidos contadores, velocidades y teóricos; no eliminar una observación por tener cero producción teórica ni inferir a partir de ella una parada física o un dato inexistente.

Normalizar fechas con formato día/mes/año y hora de uno o dos dígitos conforme a la zona horaria y política de cambios de hora del origen; no usar la configuración regional del servidor. Conservar los valores originales como evidencia. No asumir que los contadores deben crecer con el tiempo ni calcular sus diferencias entre filas como si fueran acumulados sin demostrarlo.

#### 4.9.3. Fórmula confirmada y compatibilidad

Para las observaciones comparables y elegibles de una ventana:

`rendimiento_ZC_porcentaje = 100 × SUM(RUEDA) / SUM(TEORICO_A + TEORICO_B)`.

Es la fórmula confirmada por el usuario y verificada en `calidad_serie/backend/app/infrastructure/repositories/customer_score.py`, con su preparación de observaciones en `infrastructure/aggregates/zc.py`. Agregar numerador y denominador sobre el mismo conjunto de observaciones, filtros y ventana; no usar una media aritmética de porcentajes individuales ni sumar contadores de distintos puntos de paso para crear el numerador.

`SALIDA_MAQ_IS` describe la elección de contador de salida de máquina/expulsor, pero no cambia el rendimiento confirmado a `EXPULSOR / teóricos` o `MAQUINA_A + MAQUINA_B / teóricos`. Se conserva como información original. Cualquier futura métrica de otro punto de medida requerirá definición separada y no se introducirá como efecto de migrar la fuente.

La conservación de la fila y su elegibilidad para una métrica son distintas. Mantener en Delta las observaciones válidas con teórico cero; para reproducir el cálculo histórico, la selección de puntos de rendimiento respetará el requisito actual de teórico positivo y registrará la cobertura excluida. Si no hay denominador positivo elegible, el porcentaje será no evaluable, no un cero, cien o infinito inventados. No usar el numerador de puntos excluidos con el denominador de otro conjunto.

No imponer como regla de esquema `RUEDA <= TEORICO_A + TEORICO_B`, forzar el porcentaje al rango 0–100 ni corregir contadores automáticamente por discrepancias entre puntos de medida. Una observación o ventana anómala se conserva y se señala para validación de negocio/instrumentación, sin ocultarla mediante recortes o sustituciones. No convertir huecos de diez minutos en filas de ceros ni interpolar observaciones ausentes.

La aplicación histórica lee estos campos desde su fuente de rendimiento existente; `CONT_ZC` aportará las nuevas observaciones mediante un adaptador independiente. Validar la correspondencia de `MAQ_IS` con línea y producción y la misma interpretación de fechas/ventanas; no confundir ese número con el `ID_LINEA_ZC` del Excel histórico ni copiar sus equivalencias numéricas sin validación. No repartir el contador de rueda entre máquinas A/B o producciones mediante proporciones inventadas.

**Decisión de negocio para máquinas tándem con rueda común:** cuando dos producciones simultáneas comparten una única rueda sin desglose por modelo, el rendimiento se calcula en el ámbito máquina/periodo y se asocia el mismo porcentaje a ambas producciones coincidentes. No exigir un reparto por botella que la instrumentación no permite. Mantener una sola observación física Silver por `K_ZC` y aplicar la asociación en los datos derivados, con alcance compartido explícito y sin duplicar contadores al agregar varias producciones. Reglas y comprobaciones en 16.7.

Antes de conectar la nueva fuente, contrastar muestras históricas controladas de producción y turno: mismas observaciones elegibles, sumas de rueda/teóricos, porcentaje, cobertura y contribución al score. Mantener las restantes fórmulas, pesos y límites existentes; la validación de la ingesta no autoriza modificar el cálculo de la aplicación.

#### 4.9.4. Identidad, novedades, duplicados y conflictos

La clave será `K_ZC = (origen, MAQ_IS, FECHA)`, con origen estable por planta/procedencia Fenix. Archivo fechado, lote, fila y momento de recepción no forman parte de la identidad. Materializar el ámbito técnico tras las 17 columnas o mediante separación física por origen para que pueda utilizarse en la condición de escritura. No mezclar observaciones de máquinas/orígenes diferentes por coincidir fecha o cantidades.

Los contadores, velocidades e indicador son contenido, no componentes adicionales de identidad. Una variación para la misma máquina/fecha no se convertirá automáticamente en otra fila válida, ni se sumará como producción adicional. Comparar siempre los 17 campos tipados y normalizados dentro del lote y frente al snapshot publicado; el hash de contenido no sustituye a la clave.

| Situación | Resultado | Tratamiento |
| --- | --- | --- |
| Misma `K_ZC` y mismos 17 campos | Duplicado / `NO_CHANGE` | Consolidar; no insertar ni actualizar y conservar primera recepción. |
| Clave nueva, válida y no contradictoria | `INSERT` | Insertar una sola observación con sus 17 campos, procedencia e intención/confirmación auditadas. |
| Otra fecha, máquina u origen, con cantidades iguales | Observación independiente | No deduplicar por valor numérico; validar su propia clave. |
| Misma `K_ZC` con cualquier contador, velocidad o indicador diferente | `CONFLICT` | Conservar lo aceptado y guardar íntegra cada candidata contradictoria en revisión persistente; no sumar, elegir máximo/mínimo, duplicar en datos activos o sobrescribir. |
| Misma clave nueva con varias versiones incompatibles en el lote | `CONFLICT` | Apartar las alternativas hasta resolverlas; no escoger una por orden o mayoría para poder insertarla. |
| Tipo, fecha, rango, escala o campo obligatorio inválidos | `INVALID` | Conservar evidencia y motivo; no redondear, imputar o fabricar cantidades. |
| Una observación no aparece en otro volcado | Sin baja automática | Mantener lo publicado; ausencia no autoriza borrado ni imputación. |

Una corrección confirmada requerirá decisión explícita y operación auditada de valor anterior/nuevo, con su precedencia demostrada y efecto controlado sobre los agregados dependientes. No conservar dos versiones activas de la misma observación ni tratar una corrección como incremento acumulativo. La mera última llegada, posición del CSV, nombre de archivo o número de repeticiones no demuestra precedencia.

#### 4.9.5. Escritura idempotente, revisión y recuperación

Aplicar la revisión estable de entrada y descriptor de lote de la sección 7 a cada archivo nuevo o actualizado. Revisar también fechas ya procesadas para detectar cambios tardíos; no limitar la búsqueda a timestamps posteriores al último checkpoint. Normalizar, validar y consolidar antes de preparar decisiones de escritura.

Guardar las candidatas conflictivas en el registro persistente de control/auditoría de 9.1, según 4.6.7: los 17 campos completos, valores originales, clave, contenido/hash aceptados, campos discrepantes, archivo/revisión/fila, origen, lote y estado `PENDING_REVIEW`. No basta una línea de consola ni una referencia a un CSV que puede desaparecer. Repetir la misma candidata no duplica el caso; utilizar identidad determinista de contrato, clave, hashes aceptado/candidato y motivo, con evidencias de recepción separadas.

El hash de negocio se calculará sobre los 17 campos normalizados, incluidos decimales exactos y fecha canónica, sin metadatos de ejecución. Preparar intenciones con clave, operación estable, snapshot y hashes antes de escribir; confirmar resultados sin confundir decisiones previstas con commits publicados.

Publicar únicamente decisiones resueltas bajo el modelo de único escritor, con inserciones por clave y sin rama de actualización automática de coincidencias. Un append es de observaciones nuevas validadas, no concatenación de cada CSV. Antes de ejecutar, comprobar el estado vigente; si cambia por concurrencia, reevaluar candidatos. Las correcciones explícitamente resueltas siguen su operación auditada, no una sustitución indiscriminada del lote o de la tabla.

Si no hay inserciones o correcciones explícitamente resueltas, no invocar escrituras, reescribir Parquet ni generar commits Delta. No actualizar `last_seen` en las filas por una repetición, modificar archivos fuente o reconstruir la tabla con el último CSV. La primera recepción permanece inmutable y las observaciones repetidas se registran en control.

Los reintentos reutilizarán identidad de lote/operación, entrada estable y versión de contrato/transformador. Un commit incierto se reconciliará conforme a la sección 8 antes de repetirlo; una publicación ya confirmada se resolverá como coincidencia. No duplicar filas, cantidades, agregados de rueda/teóricos ni contadores confirmados de auditoría. Mantener límites y clasificación de fallos de la sección 8: un conflicto de negocio no se resuelve repitiendo indefinidamente la escritura.

Auditar revisiones/huellas, archivo/fila/origen, contrato, snapshot consultado, claves, hashes, operación prevista/confirmada, resultado y contadores separados de filas recibidas, nuevas, repetidas, conflictivas, inválidas y pendientes. Registrar cobertura temporal y exclusiones de cálculo por separado de las incidencias de ingesta. No declarar completo un lote o snapshot con unidades requeridas pendientes; recuperar unidades parciales según la sección 8.

#### 4.9.6. Criterios de aceptación para la implementación

- Conservar exactamente los 17 nombres y el orden original; contadores e indicador son `BIGINT`, fecha `TIMESTAMP` y ambas velocidades `DECIMAL(12,2)`, sin pérdida de precisión.
- La primera observación AV23 del día 14 conserva rueda 1583 y teóricos 1614 + 0; el porcentaje de esa observación elegible es aproximadamente 98,08 %, sin sustituir rueda por expulsor aunque el indicador sea 1.
- Agregar ventanas mediante sumas sobre el mismo conjunto elegible, no media simple de porcentajes. Un teórico nulo no es válido como cantidad obligatoria; un teórico cero válido se conserva y su elegibilidad se trata aparte. Sin denominador elegible positivo, no inventar un porcentaje.
- Reprocesar los mismos archivos o representaciones equivalentes de fechas/decimales no modifica contenido, primera recepción o versión Delta. Sí registra el resultado operativo.
- Una clave nueva se inserta una vez; otra fecha/máquina/origen con idénticos contadores permanece como observación independiente.
- Misma clave con rueda, teóricos, otra cantidad, velocidad o indicador distintos conserva lo aceptado y guarda los 17 campos candidatos para revisión, sin sumar ni publicar otra observación activa. Repetir el caso no duplica la incidencia.
- Cantidades negativas, campos obligatorios vacíos, enteros fuera de rango o velocidades no representables exactamente quedan apartados, sin rellenar, truncar o redondear.
- Ceros, contadores documentados como no utilizados y observaciones anómalas representables se conservan; no se calculan diferencias de contadores, imputan huecos ni corrigen porcentajes por inferencia.
- Fallos antes/después del commit y reintentos se recuperan sin duplicar filas, sumas de cálculo o auditoría confirmada; sin novedades válidas no se escriben datos Delta.
- La comparación con el histórico valida línea/producción, ventanas, cobertura, rueda, teóricos y score antes de conectar la nueva fuente; el despliegue no cambia ni recalcula datos por sí mismo.
- Dos producciones tándem que comparten máquina, rueda y periodo reciben el mismo porcentaje de máquina. Consultarlas conjuntamente no suma dos veces la misma observación física ni interpreta ese porcentaje como rendimiento individual medido por modelo. Un cambio dentro de un periodo de diez minutos requiere la política temporal explícita de 16.7, sin repartir contadores por aproximación.

## 5. Inserciones, actualizaciones y deduplicación

La regla inicial será `MERGE` por clave validada, subordinada al contrato específico de cada tabla. En los cuatro diccionarios `SIP_MODELO`, `SIP_DEFECTO`, `SIP_DEFECTO_STATUS` y `SIP_DEFECTO_AR` prevalecen las secciones 4.1 a 4.4: solo insertar entradas nuevas validadas; no actualizar automáticamente el contenido aceptado ni escribir cuando no hay inserciones:

En `MP9_CAMBIO_MODELO` prevalece la sección 4.5: además de insertar entradas nuevas, se completan cierres válidos sobre el mismo ID. No extender a esta tabla temporal la prohibición de actualizar que se ha acordado para los diccionarios; sí mantener la ausencia de escrituras cuando no hay cambios válidos y el registro de contradicciones para revisión. Los cambios no resueltos de `ZF_OK` no se automatizan.

En `LISTA_DEFECTOS` prevalece la sección 4.6: conservar las 18 columnas, usar la clave solicitada con `INSPECC`, comprobar posibles revisiones mediante la firma sin ese contador y deduplicar las bases compartidas por varios defectos al agregar. No insertar automáticamente una posible corrección como otra observación ni alterar los contadores de rechazo.

En `LISTA_RECH` prevalece la sección 4.7: conservar las nueve columnas y aplicar la clave equivalente sin `COD_DEF`, incluida `CAVIDAD`, con los mismos controles de posibles revisiones de `INSPECC`, candidatos persistentes de revisión e idempotencia. Es la fuente preferente del denominador MIA, sin multiplicarlo al cruzarlo con filas de defectos ni cambiar las reglas históricas de agregación.

En `LISTA_AR` prevalece la sección 4.8: conservar cada tramo con sus 12 campos, aunque comparta agrupación/procedencia con otro, comparar íntegramente para deduplicar y completar solo cierres validados sobre la identidad estable. No fusionar intervalos ni decidir aquí el cómputo de minutos-cavidad.

En `CONT_ZC` prevalece la sección 4.9: identidad por origen/máquina/fecha, conservación de los 17 campos, repetición sin escritura y variaciones de contenido en revisión persistente. No sumar versiones de una observación, sobrescribir contadores automáticamente ni alterar la fórmula confirmada con rueda y teóricos.

| Situación | Acción |
| --- | --- |
| Clave inexistente | Insertar. |
| Clave existente con contenido cambiado | Actualizar con la versión que corresponda según la regla de precedencia. |
| Clave existente sin cambios | No escribir una modificación innecesaria. |
| Duplicados idénticos dentro del lote | Consolidar. |
| Registros contradictorios para la misma clave | Resolver solo con una regla probada; en otro caso, apartar y registrar el conflicto. |

No usar `append` indiscriminado de cada CSV: los volcados contienen repeticiones y nuevas copias de registros existentes. Tampoco sumar medidas repetidas como si fueran producción adicional.

Una nueva versión del mismo tramo de `LISTA_AR` puede incorporar `FECHA_OFF`; esa actualización mantiene la identidad del tramo. Cambios de inicio/procedencia pueden corresponder a otro tramo de la misma agrupación, no a una actualización del anterior. Las correcciones de otros campos no se aplicarán automáticamente sin resolución explícita conforme a 4.8.

Los intervalos de medidas pueden recibir correcciones: no se considerarán inmutables sin demostrarlo. La detección no se limitará a «fecha del registro mayor que la última fecha procesada».

La ausencia de una fila en un volcado posterior no implica eliminación. Antes de interpretar bajas debe demostrarse que la fuente es una instantánea completa y cuál es su alcance.

### 5.1. Contrato de actualización por registro

Cada registro tendrá una clave de negocio determinista, contexto de origen y un hash calculado sobre su contenido normalizado de negocio. Fechas de recepción, número de intento y otros metadatos operativos no formarán parte de ese hash.

Antes de escribir se clasificará el registro:

| Resultado | Regla y evidencia |
| --- | --- |
| `INSERT` | Clave ausente: insertar una sola entidad con su procedencia. |
| `UPDATE` | Misma entidad, cambio válido y precedencia comprobada: mantener la clave, aplicar la corrección y registrar hashes anterior/nuevo. |
| `NO_CHANGE` | Mismo contenido: no modificar cantidades, vigencias, primera recepción ni versión de negocio. |
| `STALE` | Versión de origen demostrablemente anterior: no sobrescribir la más reciente; registrar el descarte. |
| `CONFLICT` | Misma clave con valores incompatibles y sin precedencia fiable: apartar; no escoger arbitrariamente. |
| `INVALID` | Incumplimiento de esquema o reglas: apartar con motivo y procedencia. |

No incrementar contadores acumulados como efecto de un reintento. Cuando una cantidad de origen sea un valor corregible, la actualización asignará ese valor, no lo volverá a sumar. Conservar las cantidades de fuente y derivar los agregados posteriormente evita acumulaciones irreversibles.

La precedencia se definirá por tabla a partir de versiones/timestamps de origen fiables o de una regla de instantáneas comprobada. La mera llegada más tarde de un archivo no demuestra que cada fila sea más reciente. Sin evidencia suficiente, el caso queda en conflicto.

Cambiar una clave identificadora no se tratará como una actualización normal: requerirá una regla explícita para corrección de identidad y sus referencias. Una fecha de cierre vacía tampoco volverá a abrir automáticamente un evento previamente cerrado por recibir un volcado antiguo.

Para cada fila insertada o modificada se conservarán clave, origen, lote, operación, versión del contrato y hash de negocio. La primera recepción será inmutable. Las observaciones repetidas sin cambio se registrarán en el control operativo cuando corresponda, sin forzar una escritura Delta por actualizar `last_seen`.

### 5.2. Idempotencia por fila y por operación

Un reintento debe reproducir el mismo contenido normalizado a partir de una entrada estable y la misma versión de transformador. No regenerar IDs con la hora actual, orden accidental de filas ni números aleatorios.

Dentro de un lote, una tabla recibirá como máximo una decisión resuelta por clave. Un `MERGE` no se utilizará como mecanismo para resolver duplicados contradictorios de la entrada. Delta no impone por sí solo una clave primaria única: esa propiedad será una validación de la ingesta.

La identidad de una operación será determinista a partir del lote, tabla, unidad de escritura y versión de transformación. `attempt_id` identificará la ejecución/reintento y será diferente; no reemplaza a `operation_id`.

Las funciones de tokens de transacción del motor se usarán únicamente tras comprobar su semántica para las operaciones y versión elegidas. Son una ayuda, no una sustitución del `MERGE` idempotente ni del control de recuperación.

## 6. Histórico de datos y de cambios

Separar:

- **Histórico de producción:** medidas y eventos de distintas fechas conservados a largo plazo.
- **Histórico de modificaciones:** versiones anteriores de registros, diccionarios y reglas.

Los diccionarios y referencias que cambien tendrán un historial explícito de versiones cuando sea necesario para interpretar o reproducir resultados antiguos. Se acordará si el cálculo debe usar las reglas vigentes al producir o las reglas actuales; no aplicar retroactivamente cambios de criticidad de forma implícita.

Se distinguirá la fecha efectiva declarada por la fuente de la fecha en que observamos el cambio. Si no conocemos la primera, no se fabricará a partir de la segunda.

El historial técnico de Delta no será la única auditoría permanente: su recuperabilidad depende de la conservación del log y de los archivos de datos correspondientes.

## 7. Ciclo de una ejecución

1. Impedir el solapamiento con otro escritor.
2. Detectar archivos nuevos o modificados mediante huella y metadatos, incluidos nombres versionados y rutas sin hora sobrescritas, conforme a 3.5. No tratar una ruta ya recibida o un nombre sin cambios como garantía de contenido inmutable.
3. Obtener una copia estable: mecanismo de entrega acordado o comprobación de estabilidad con reintento si cambia durante la lectura.
4. Registrar el lote y sus fuentes.
5. Leer y normalizar con esquema explícito.
6. Validar y consolidar el lote antes del `MERGE`.
7. Publicar las transacciones de las tablas afectadas.
8. Registrar las versiones resultantes y los contadores.
9. Marcar el lote completado únicamente al terminar todas las operaciones exigidas.

La frecuencia se ajustará a la llegada real de los volcados y al desfase aceptado. Programar más ejecuciones no crea datos más recientes que los suministrados por Fenix.

El lote fijará un descriptor persistente con sus archivos/huellas, copias estables, contratos, código y configuración. Reintentar significa procesar ese descriptor, no volver a leer un CSV vivo que quizá ya cambió. Una modificación de la fuente genera otra revisión de entrada, no cambia silenciosamente un lote en curso.

El checkpoint de consumo se avanzará solo después de completar y validar la publicación exigida. La marca «archivo detectado» nunca equivaldrá a «datos publicados».

## 8. Recuperación y coherencia entre tablas

La ejecución debe ser idempotente: repetir una entrada ya procesada no duplica medidas, eventos, agregados ni versiones de negocio sin cambios. No se prometerá una transacción distribuida «exactly once» entre todas las tablas y el registro de control.

### 8.1. Estados persistentes

| Ámbito | Estados previstos |
| --- | --- |
| Lote | `DISCOVERED`, `PREPARING`, `PREPARED`, `WRITING`, `PARTIAL`, `RECONCILING`, `VALIDATING`, `COMPLETED`, `FAILED_RETRYABLE`, `QUARANTINED`. |
| Operación de tabla | `PREPARED`, `COMMITTING`, `COMMITTED`, `NO_CHANGE`, `UNKNOWN`, `FAILED_RETRYABLE`, `FAILED_PERMANENT`. |

Cada transición conservará fecha, intento, release, causa y evidencia. El estado actual podrá actualizarse; el historial de transiciones no se sobrescribirá. Tras una caída, un estado `WRITING` no se asumirá completado ni fallido sin reconciliarlo.

### 8.2. Escritura de una tabla y confirmación incierta

1. Guardar la intención de escritura y su conjunto normalizado antes del commit, con `operation_id`, claves/hashes, huella de entrada y versión Delta observada.
2. Ejecutar una unidad acotada de `MERGE` mediante el motor Delta, incorporando identificadores de lote/operación y release a los metadatos del commit cuando el motor lo permita.
3. Confirmar el commit con su versión y las métricas disponibles; comprobar las invariantes de la operación.
4. Registrar el resultado confirmado y sus cambios de filas en el control persistente.

**Escenario crítico: Delta confirma la escritura y el proceso cae antes de actualizar el control.** Al reiniciar, la operación estará pendiente/`UNKNOWN`. Primero se consultará la evidencia de commits y se contrastará con la intención preparada; no se repetirá ciegamente ni se declarará fallo por ausencia del acuse.

Si se demuestra el commit, reconstruir su confirmación y finalizar la auditoría sin reescribir datos. Si se demuestra que no hubo commit, reintentar la misma operación. Si no puede determinarse, mantener `UNKNOWN`, bloquear su publicación y escalar la reconciliación; un timeout no demuestra que la escritura haya fallado.

El diseño de metadatos y pruebas debe permitir localizar esa evidencia con el motor elegido. La retención de logs, archivos y entradas preparados cubrirá todos los lotes sin resolver. No ejecutar limpieza que elimine evidencia de una operación pendiente.

Una operación puede crear Parquet sin alcanzar un commit Delta; esos archivos no son filas publicadas. No adoptarlos manualmente ni borrarlos durante la recuperación. Su limpieza corresponde al mantenimiento soportado y a la política de retención.

### 8.3. Lotes parcialmente publicados

Las transacciones Delta son por tabla; si una tabla necesita varias unidades de escritura, cada unidad tendrá su identidad y estado. El lote solo se completará tras confirmar y validar todas las unidades requeridas.

Ejemplo: las tablas A y B se confirman, y falla C. Conservar A/B, marcar el lote `PARTIAL` y recuperar C. No borrar A/B ni restaurar tablas para simular una transacción global. Las unidades confirmadas se omiten en el reintento después de verificar su evidencia; las pendientes se reanudan desde entradas estables.

En la primera implementación no se aplicará otro lote que modifique las mismas entidades mientras el anterior esté parcial o sin reconciliar. Se podrán detectar y preparar entradas posteriores, pero su escritura permanecerá ordenada. Cualquier paralelización futura deberá definir y probar cómo evita sobrescrituras de versiones nuevas por reintentos antiguos.

La futura publicación para la aplicación será un descriptor versionado de snapshots por tabla correspondiente a un lote validado. Se conservará la última publicación completa hasta que la siguiente lo esté. No leer «la última versión» de cada tabla independientemente si puede pertenecer a un lote incompleto.

### 8.4. Reintentos y fallos permanentes

| Tipo de fallo | Tratamiento |
| --- | --- |
| Fuente aún en escritura o fallo transitorio de lectura | Reintentar la captura estable con límite y espera progresiva. |
| Bloqueo temporal/servicio de almacenamiento temporalmente no disponible | Reintento limitado, con espera exponencial y variación aleatoria. |
| Conflicto de commit | Refrescar el snapshot, comprobar otros escritores y reconciliar; no insistir sobre una versión obsoleta. |
| Resultado de commit desconocido | Reconciliar antes de cualquier nueva escritura. |
| Esquema incompatible, clave conflictiva o regla de negocio inválida | No reintentar automáticamente la misma entrada; apartar y reportar. |
| Disco lleno, permisos incorrectos o configuración inválida | Detener y reportar una condición operativa; no mantener un bucle indefinido. |

Los límites de intentos, tiempo total, esperas y códigos de salida serán configurables y documentados. Agotarlos no cambia el lote a completado. El siguiente arranque reconocerá el lote pendiente y aplicará el procedimiento de recuperación.

Un reintento normal mantendrá la versión de transformador y configuración del lote. Si una reparación de código es necesaria para una entrada ya parcialmente escrita, se registrará una revisión de recuperación explícita con relación al lote original; validar su compatibilidad y las unidades ya confirmadas antes de continuar.

Los registros apartados requieren una política: inicialmente, una infracción de una regla obligatoria impide completar/publicar el lote. Permitir publicación parcial de filas válidas sería una decisión de negocio explícita por tabla, nunca una manera de ocultar un fallo.

No editar `_delta_log` manualmente ni simular un rollback borrando archivos. Revertir código no revierte datos. Cualquier reparación de datos tendrá un procedimiento separado, trazable y validado.

## 9. Control de ingesta y calidad

Mantener una tabla de control independiente del log técnico de Delta con:

- Lote, fuente, huella, inicio, fin y estado.
- Filas leídas, consolidadas, insertadas, actualizadas y apartadas.
- Versiones Delta resultantes por tabla.
- Errores y motivo de los registros apartados.
- Versión del transformador y de los esquemas aplicados.

Los reintentos quedarán registrados sin perder el resultado del intento anterior. Los datos originales del registro problemático deben poder rastrearse mientras dure la retención acordada.

### 9.1. Tres niveles de evidencia

| Evidencia | Contenido | Uso |
| --- | --- | --- |
| `_delta_log` | Commits, acciones sobre archivos, versiones y metadatos soportados. | Determinar qué snapshot existe y si una transacción se confirmó. No editarlo. |
| Control operativo | Lotes, intentos, operaciones, transiciones, entradas, versiones y contadores confirmados. | Reanudar, reconciliar y reportar sin perder intentos previos. |
| Auditoría de registros | Clave, decisión, hashes anterior/nuevo, procedencia, operación y versión confirmada; payload anterior/nuevo o referencia retenida cuando corresponda. | Explicar cada inserción/corrección y su pertenencia a una escritura. |

La primera implementación local usa SQLite transaccional, WAL y `synchronous=FULL`, fuera del repositorio, para control operativo e intención/auditoría de cambios. Su operación sobre el almacenamiento productivo se validará en la fase 0. El motor permite ensayar recuperación, pero no crea una transacción común con Delta. Si se elige otro almacén, deberá cumplir el mismo contrato; un archivo de texto mutable no será el mecanismo de recuperación.

El control y Delta no comparten transacción: la reconciliación de la sección 8 resuelve ese límite. La intención preparada de cada cambio de fila se conserva antes del commit y solo se marca aplicada con evidencia de una versión Delta confirmada. Las claves de auditoría impedirán duplicar una misma operación/fila al reintentar.

### 9.2. Contadores y reportes

- Distinguir filas leídas, duplicadas de entrada, candidatas a inserción/actualización, confirmadas, sin cambio, obsoletas, conflictivas e inválidas.
- Una decisión preparada no es una escritura confirmada. Los contadores previstos y confirmados tendrán nombres diferentes.
- Los totales de negocio se computarán por operaciones confirmadas distintas, no sumando cada intento. Los reintentos tienen sus propios contadores de ejecución.
- Las métricas del motor se conservarán como evidencia; si no permite conocer un contador, se reportará desconocido, no cero inventado.
- Un estado incierto se mostrará como pendiente de reconciliación. Un fallo parcial nunca se reportará como éxito.
- `NO_CHANGE` no tiene por qué generar una nueva versión Delta. Se registra el resultado y el snapshot observado sin inventar un commit.
- Los errores tendrán códigos estructurados y detalle suficiente para actuar, sin credenciales en los logs.
- Al terminar, emitir resumen legible y estructurado con lote, intento, release, resultado, tablas afectadas y problemas pendientes; los fallos producen código de salida no satisfactorio para la tarea programada.

La auditoría por fila se mantendrá fuera de la consola para no generar millones de líneas de log. En grandes lotes se preparará/procesará por unidades acotadas, sin cargar todo el historial en memoria.

Comprobaciones mínimas: fechas válidas y coherentes, cantidades válidas, componentes obligatorios de clave no nulos, referencias resolubles y ausencia de duplicados tras normalización. Los componentes nulos expresamente permitidos por un contrato —como `CAVIDAD` en la clave de `LISTA_DEFECTOS`— tendrán igualdad y serialización deterministas para no duplicar registros. Un dato ausente o no interpretable no se convertirá silenciosamente en cero.

No cambiar fórmulas, sumar inspeccionadas repetidas entre defectos ni confundir rechazo con parada física. No se incorporará un ajuste temporal de minutos-cavidad sin datos y criterios validados; las tablas de secciones siguen excluidas.

## 10. Almacenamiento, mantenimiento y retención

- Empezar sin particiones en tablas pequeñas. Elegir particiones de las tablas grandes a partir de volumen y consultas; evitar particionar por cavidad o cada producción por defecto.
- Agrupar la ingesta para evitar generar un archivo pequeño por fila.
- Compactar cuando las métricas de tamaño/número de archivos lo justifiquen, sin solaparla con el escritor inicial.
- Gestionar checkpoints y logs mediante el motor y una política validada.
- Definir retención de versiones y lectores antes de ejecutar `VACUUM`. No usar retención cero para ahorrar espacio.
- No eliminar manualmente Parquet que parezcan antiguos: pueden pertenecer a versiones que aún se necesitan.
- Respaldar las tablas incluyendo datos y `_delta_log` como un conjunto coherente, y probar su restauración.
- Definir una retención acotada para CSV/Excel de entrada y copias de trabajo. Eliminarlos solo después de una carga verificada y conforme a esa política.

Conservar años de producción no obliga a conservar todas las versiones técnicas durante años. La retención de datos, auditoría, originales y versiones recuperables se acordará por separado.

## 11. Fases de ejecución y criterios de aceptación

### Requisitos de GitHub y despliegue que acompañan a las fases

Los procedimientos completos se definen en la sección 15. Ninguna fase queda aceptada solo por pasar en el ordenador local: la CI y las pruebas controladas en el Windows Server destino deben cubrir el comportamiento correspondiente.

### Fase 0 — Validación del entorno

- [ ] Confirmar sistema operativo, arquitectura, Python permitido y recursos disponibles.
- [ ] Acordar ubicación de entrada, almacenamiento Delta, permisos y cuenta de ejecución.
- [ ] Validar lectura, escritura, actualización y recuperación con delta-rs en el almacenamiento destino.
- [ ] Fijar versiones compatibles del runtime y del protocolo Delta.
- [ ] Establecer raíces separadas, cuentas/permisos y bloqueo compartido entre operación, mantenimiento y activación.
- [ ] Validar el almacén de control persistente y el procedimiento de reconstrucción tras una caída.
- [ ] Confirmar acceso desde el servidor a GitHub/paquetes o preparar paquetes offline.
- [ ] Si se adopta Docker, validar runtime soportado, plataforma de imagen, mounts persistentes y mecanismo de entrega con o sin SSH.

Salida: entorno viable en la modalidad elegida; Docker preferido cuando esté disponible y soportado, nativo como alternativa.

### Fase 1 — Contratos y muestra de una Fenix

- [ ] Documentar los 9 esquemas seleccionados y las tres referencias Excel.
- [ ] Validar claves, duplicados, actualizaciones y significado de los campos temporales.
- [ ] Determinar cómo relacionar modelo, máquina y ramal con las fuentes seleccionadas, sin reintroducir automáticamente catálogos excluidos.
- [ ] Validar correspondencias MIA y MDL, incluidas las ambiguas.
- [ ] Acordar zona horaria, política de intervalos abiertos y configuración autoritativa.
- [ ] Documentar precedencia por registro, huellas y política de cuarentena.
- [ ] Validar el catálogo de 16 con las cinco fabricaciones de AV32, cierres tardíos, modelos repetidos y producciones simultáneas; separar los acuerdos de negocio de las incidencias aún pendientes de origen.
- [ ] Confirmar correspondencia de máquinas tándem con sus producciones y significado temporal de cada observación `CONT_ZC`, sin exigir un reparto de rueda por modelo.
- [ ] Implementar y verificar la relación temporal de 17 con intervalos sin solapamiento, referencias válidas y huecos de preparación; mantener AV31 pendiente de la consulta del usuario.
- [ ] Aplicar la semántica confirmada de muestras al final de diez minutos, sin extender el periodo por huecos de captura y sin asignar por la hora final exclusivamente.

Salida: contratos suficientes para una primera ingesta; incertidumbres documentadas.

### Fase 2 — Primera carga controlada

- [ ] Cargar una muestra representativa en tablas Delta.
- [ ] Comparar cantidades, eventos y referencias con los originales.
- [ ] Verificar que las repeticiones de los volcados no incrementan los resultados.
- [ ] Registrar lote, huellas y versiones.
- [ ] Verificar que archivos nuevos con contenido repetido y archivos reescritos producen el mismo estado Silver; Bronze conserva las entregas según su política sin crear tablas por archivo.
- [ ] Cubrir los dos modos de 3.5, su coexistencia y transición entre líneas/familias: nombre nuevo con contenido repetido y nombre sin hora sobrescrito con contenido nuevo, con pruebas de estabilidad y recuperación.
- [ ] Conservar las producciones históricas cerradas del catálogo aunque dejen de aparecer en los siguientes volcados.
- [ ] Ensayar una asociación producción–ramal aceptada, su evidencia y una corrección posterior, sin alterar los CSV ni las fechas MP9 originales.

Salida: carga inicial conciliada con el origen.

### Fase 3 — Ingesta incremental y fallos

- [ ] Repetir el mismo lote y comprobar que no cambia el resultado.
- [ ] Incorporar nuevos intervalos.
- [ ] Incorporar una corrección de cantidad y el cierre de un evento.
- [ ] Detectar una modificación antigua aunque no avance la fecha máxima.
- [ ] Ensayar claves conflictivas y referencias no resueltas.
- [ ] Simular una interrupción entre tablas y recuperar sin duplicados.
- [ ] Comprobar que dos ejecuciones no escriben simultáneamente.
- [ ] Simular caída antes del commit, después del commit y antes del acuse en el control.
- [ ] Reconciliar un resultado incierto sin duplicar datos ni auditoría por fila.
- [ ] Comprobar que un reintento antiguo no sobrescribe una corrección más reciente.
- [ ] Verificar que una repetición no genera nuevas versiones de negocio por cambiar timestamps operativos.
- [ ] Conciliar reportes previstos/confirmados y totales que no sumen intentos repetidos.
- [ ] Completar el cierre ZF de una producción sin cambiar su identificador, reabrirla por copias antiguas ni esperar al cierre de `LISTA_AR`.
- [ ] Escalar una referencia pendiente tras su umbral operativo sin bloquear indefinidamente otras entregas, inventar cierres o publicar relaciones incompletas como completas.
- [ ] Rechazar solapamientos de asociaciones por ramal; probar huecos confirmados de preparación, falta de cobertura y muestras de transición sin repartir cantidades.
- [ ] Reconstruir asignaciones afectadas por una corrección de vigencia con operación estable, auditoría y publicación coherente; el reintento no duplica registros ni conteos.

Salida: crecimiento y actualización fiables, con recuperación probada.

### Fase 4 — Operación sostenida

- [ ] Programar la tarea y observar varios ciclos reales.
- [ ] Medir duración, memoria, espacio, tamaño de archivos y retraso de datos.
- [ ] Validar compactación, retención y restauración de una copia.
- [ ] Ampliar a otras Fenix solo tras resolver el ámbito de sus IDs y códigos.
- [ ] Ensayar despliegue inicial, actualización sin cambios de dependencias y actualización con cambios.
- [ ] Probar que el despliegue y validación no tienen permisos de escritura en el Delta productivo.
- [ ] Validar activación en frontera de lote, compatibilidad de lotes pendientes y rollback de código.
- [ ] Verificar fallos de instalación/validación: la release anterior y sus datos siguen utilizables.

Salida: ingesta operable y documentada.

### Fase 5 — Conexión con la aplicación, posteriormente

- [ ] Elegir lecturas Delta directas o una exportación controlada compatible con la app.
- [ ] Usar versiones coherentes y reflejar las correcciones en las salidas derivadas.
- [ ] Validar una producción abierta y un cambio real de modelo de principio a fin.
- [ ] Comparar resultados con el cálculo histórico existente antes de sustituir entradas.
- [ ] Buscar un modelo y recuperar sus fabricaciones históricas sin que el usuario conozca las fechas; comparar una antigua con una actual o reciente.
- [ ] Resolver y versionar asociaciones a partir de `asignacion_produccion_ramal`, sin alterar los nueve esquemas de origen; la relación temporal se valida en Silver y las referencias de consulta se materializan cuando corresponda.
- [ ] Verificar rendimiento compartido tándem en dos producciones y ausencia de doble conteo al consultarlas conjuntamente.
- [ ] Medir consultas de histórico, comparación y últimas ocho horas con objetivos explícitos de latencia/cobertura; la normalización del modelo no sustituye esta medición.

Las lecturas deben respetar el snapshot Delta. No escanear indiscriminadamente todos los Parquet del directorio: puede haber archivos de versiones anteriores. La actualización de cachés y de la pantalla se tratará en esta fase.

## 12. Decisiones pendientes

| Tema | Pendiente de acordar |
| --- | --- |
| Entorno remoto | Sistema, runtime permitido, rutas y garantías del almacenamiento. |
| Repositorios y ramas | Repositorio/unidad de release independiente de ingesta; convención `development` y `main`, accesos y protecciones disponibles. |
| Distribución | Artefacto de release o checkout exacto, comprobación de CI y conectividad/instalación offline. |
| Docker y SSH | Runtime soportado, ubicación del motor, plataforma de imagen, acceso a registry o paquete offline y acceso administrativo independiente de SSH. |
| Persistencia en contenedores | Ubicación real de mounts, transferencia de inputs Windows–host del motor, permisos/bloqueos y pruebas de filesystem. |
| Entornos Python | Inventario, lockfile, identidad de entorno y caché de paquetes; nunca modificar el entorno de un proceso activo. |
| Recuperación | Límites de reintento, almacén de control, evidencia de commit y política ante resultados inciertos. |
| Entrega de Fenix | Modalidad versionada por fecha/hora prevista para todas las líneas, aún en pruebas. Soportar también archivos sin hora sobrescritos/modificados y coexistencia de modos según 3.5. Confirmar patrones exactos, zona horaria de la marca, cadencia efectiva, alcance de contenido y señal de archivo completo; no condicionar la identidad Silver al modo de entrega. |
| Materialización de contratos | Los nueve contratos Fenix están documentados en 4.1 a 4.9. Implementar y probar ámbitos técnicos, claves y condiciones de escritura; confirmar política temporal, precedencia de revisiones y correspondencia línea/producción antes de automatizar casos pendientes. `LISTA_AR` identifica tramos, no solo agrupaciones. |
| Minutos-cavidad de avisos/rechazos | Definir posteriormente reglas de cómputo y solapamientos. `LISTA_AR` conserva sus tramos independientes; esta ingesta no decide ni ejecuta ese cálculo. |
| Relaciones MP9 | Vinculación de `MP9_CAMBIO_MODELO` con las medidas; confirmar zona horaria/horario local y precedencia de cambios de `ZF_OK` antes de automatizarlos. Las otras dos fuentes MP9 descartadas no son dependencias del alcance actual. |
| Catálogo de producciones | Regla de negocio acordada en 16: primera entrada ZC y última salida ZF relacionadas por `ID_ST1_REL`, conservando producciones históricas. Concretar formato estable del ID y esquema físico, probar unicidad de raíz ZC y revisar agrupaciones excepcionales sin fusionar por modelo/fecha. |
| Pendientes y cobertura | Fijar umbral de alerta, periodicidad de revisión y recuperación de relaciones pendientes. No hay un máximo de duración de producción acordado; el vencimiento de una alerta nunca fabrica un cierre. |
| Asociación a producción | Tabla temporal y garantías acordadas en 17. Validar vigencias efectivas y correspondencias por origen; conservar evidencias observadas de AV32 y esperar la aclaración del usuario sobre AV31. No extrapolar correspondencias de la muestra a todos los cambios futuros. |
| Máquinas tándem | Rendimiento de máquina compartido acordado, sin reparto de rueda por modelo. Muestras de diez minutos con fecha de fin confirmadas. Validar correspondencias máquina–producciones, presencia efectiva en la rueda y cobertura parcial; una vigencia ZF no define automáticamente la de ZC. |
| Preparación y transición | Huecos reales entre modelos y separación MP9/presencia efectiva acordados. Verificar cómo se identifican con evidencia los límites de circulación; no interpretar ausencia de CSV como preparación ni adjudicar una muestra por su hora final solamente (17.5–17.7). |
| Calidad AV31 | La posible incidencia de las dos fabricaciones abiertas de `1358/242` la consultará personalmente el usuario. No fusionar, cerrar ni decidir su correspondencia a ramales hasta recibir su aclaración. No bloquea el desarrollo de ingesta de origen ni las unidades independientes. |
| Rendimiento de consulta | Definir objetivos p95 de latencia, volumen y concurrencia y probar el motor de lectura elegido. Decidir organización física y materializaciones por medida, no por una promesa de latencia mínima del esquema relacional. |
| Reglas externas | Cobertura de códigos, equivalencias ambiguas y fuente autoritativa de cada referencia. |
| Versiones de negocio | Reglas históricas frente a reglas actuales al recalcular el score. |
| Retención | Datos, cambios de negocio, versiones Delta, originales y registros apartados. |
| Rendimiento | Volumen previsto, frecuencia de carga, particionado y compactación. |
| Integración | Se decidirá después de estabilizar la ingesta. |

## 13. Referencias técnicas

- [Distribución Python de deltalake](https://pypi.org/project/deltalake/).
- [Transacciones Delta en delta-rs](https://delta-io.github.io/delta-rs/latest/how-delta-lake-works/delta-lake-acid-transactions/).
- [MERGE en delta-rs](https://github.com/delta-io/delta-rs/blob/main/docs/usage/merging-tables.md).
- [Requisitos de almacenamiento de Delta Lake](https://docs.delta.io/delta-storage/).
- [Buenas prácticas de Delta Lake](https://docs.delta.io/best-practices/).
- [Entornos virtuales de Python](https://docs.python.org/3/library/venv.html).
- [Bloqueo y sincronización de dependencias con uv](https://docs.astral.sh/uv/concepts/projects/sync/).
- [Ejecutores de GitHub Actions](https://docs.github.com/en/actions/how-tos/manage-runners/github-hosted-runners/use-github-hosted-runners).
- [Entornos y despliegues de GitHub Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments).
- [Descarga de artefactos de workflows](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts).
- [Metadatos y propiedades de commits de delta-rs](https://delta-io.github.io/delta-rs/latest/api/transaction/).
- [OpenSSH Server en Windows](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse).
- [Soporte de contenedores on-prem en Windows Server](https://learn.microsoft.com/en-us/troubleshoot/windows-server/containers/support-for-windows-containers-docker-on-premises-scenarios).
- [Docker Desktop y Windows Server](https://docs.docker.com/desktop/troubleshoot-and-support/faqs/windowsfaqs/).
- [Buenas prácticas de construcción de imágenes](https://docs.docker.com/build/building/best-practices/).
- [Bind mounts y host del motor](https://docs.docker.com/engine/storage/bind-mounts/).
- [Exportación de imágenes Docker](https://docs.docker.com/reference/cli/docker/image/save/).
- [Carga de imágenes Docker](https://docs.docker.com/reference/cli/docker/image/load/).

## 14. Registro de decisiones

| Fecha | Decisión |
| --- | --- |
| 2026-10-14 | Documentar el plan antes de implementar. |
| 2026-10-14 | Mantener las 11 tablas seleccionadas y todas las exclusiones acordadas. |
| 2026-10-14 | Proponer ingesta independiente en Python con delta-rs y un único escritor. |
| 2026-10-14 | Tratar actualizaciones mediante claves validadas y operaciones idempotentes, no append indiscriminado. |
| 2026-10-14 | Posponer la integración con la aplicación hasta validar la ingesta y operación del histórico. |
| 2026-10-14 | Separar aplicación, ingesta, despliegue, datos persistentes y pruebas, con permisos independientes. |
| 2026-10-14 | Ninguna actualización o rollback de repositorio puede escribir o migrar Delta productivo. |
| 2026-10-14 | CI en Windows y despliegue manual reproducible por release/commit validado; `.bat` como entrada a PowerShell. |
| 2026-10-14 | `development` valida en pruebas; `main` permite promover releases aprobadas, sin escritura de datos como parte de instalar. |
| 2026-10-14 | Reutilizar entornos compatibles e instalar únicamente diferencias necesarias; cambios incompatibles se preparan fuera del entorno activo. |
| 2026-10-14 | Reconciliar commits inciertos y recuperar lotes parciales con identidades estables, evidencia persistente y auditoría por registro. |
| 2026-10-14 | Incorporar Docker como distribución preferida de la ingesta, condicionada a un runtime soportado; conservar ejecución nativa como alternativa. |
| 2026-10-14 | Separar virtualización, runtime Docker y acceso SSH; contemplar instalación manual con registry o imagen offline. |
| 2026-10-14 | Persistencia fuera de la imagen/contenedor y actualización por digest/ID probado, sin mutaciones de tablas al desplegar. |
| 2026-10-14 | Acordar `SIP_MODELO` como diccionario compartido de Aiala, con `id_modelo_fenix` entero de 64 bits y `codigo_modelo_botella` texto obligatorio; descartar el estado. |
| 2026-10-14 | Verificar asociaciones de modelos en cada entrada; consolidar duplicados, insertar solo asociaciones nuevas válidas y registrar contradicciones sin sobrescribir las publicadas. Sin novedades válidas, no escribir ni generar commits en el diccionario Delta; mantener control y auditoría aparte. |
| 2026-10-14 | Acordar `SIP_DEFECTO` como catálogo compartido de Aiala con ID/código enteros de 64 bits y ambas descripciones de texto; excluir `MEDIDA` y las entradas «NO USAR» en cualquiera de las descripciones. |
| 2026-10-14 | Validar relación inequívoca ID–código en los defectos aceptados, sin fusionar códigos por compartir descripción. Consolidar duplicados, insertar solo novedades válidas y registrar contradicciones o cambios de descripción para revisión; no escribir el diccionario cuando no hay novedades. |
| 2026-10-14 | Acordar `SIP_DEFECTO_STATUS` compartido de Aiala con ID/código, ambas descripciones y los códigos numéricos `ORIGEN` y `ZONA`, sin equivalencias semánticas aún confirmadas; excluir `ACTIVO` y conservar los nombres Fenix, incluidos Delfoz. |
| 2026-10-14 | Comprobar cada revisión de `SIP_DEFECTO_STATUS`, consolidar repeticiones e insertar solo novedades válidas; registrar contradicciones y cambios de contenido para revisión, sin sobrescribir lo aceptado ni escribir Delta cuando no hay inserciones. |
| 2026-10-15 | Acordar `SIP_DEFECTO_AR` como catálogo compartido de Aiala con ID/código enteros de 64 bits, descripción original y código de agrupación entero, sin asignar etiquetas de grupo no confirmadas. |
| 2026-10-15 | Aplicar a `SIP_DEFECTO_AR` las mismas garantías de los diccionarios anteriores: validar cada revisión, consolidar duplicados, insertar solo entradas nuevas válidas, registrar contradicciones o cambios para revisión y no sobrescribir ni escribir Delta sin novedades. |
| 2026-10-15 | Conservar en `MP9_CAMBIO_MODELO` los nombres y orden originales `ID`, `ID_MODELO`, `FECHA_ON`, `FECHA_OFF`, `STATUS`, `ID_ST1_REL`, `ZF_OK`; excluir `ID_MAQUINA`, `ID_PREPARACION` y `N_MOD`. Mantener entradas/salidas ZC/ZF y su vínculo, con tipos y nulos de 4.5. |
| 2026-10-15 | Revisar cada actualización de `MP9_CAMBIO_MODELO`, consolidar duplicados, insertar ID nuevos válidos y completar cierres coherentes sobre el mismo ID sin reabrir por copias vacías. Registrar contradicciones y dependencias, mantener idempotencia y auditoría y no escribir Delta sin cambios válidos; no inferir fechas a partir de `ZF_OK` ni automatizar su precedencia aún no confirmada. |
| 2026-10-15 | Excluir `MP9_MOD_LINEA_ZF` por decisión del usuario al no necesitar seguimiento por ramal. Reducir el alcance de once a diez fuentes: cuatro diccionarios, `MP9_CAMBIO_MODELO`, `MP9_PARAM_MODELO`, las tres listas y `CONT_ZC`. Esta decisión sustituye al mantenimiento de once fuentes registrado inicialmente el 14/10; no genera cambios ni borrados en los CSV de origen. |
| 2026-10-15 | Excluir también `MP9_PARAM_MODELO` por decisión del usuario. El alcance vigente pasa de diez a nueve fuentes: los cuatro diccionarios, `MP9_CAMBIO_MODELO`, `LISTA_DEFECTOS`, `LISTA_RECH`, `LISTA_AR` y `CONT_ZC`. No exigir parámetros de la tabla descartada para la ingesta ni eliminar los CSV originales. |
| 2026-10-15 | Conservar las 18 columnas originales de `LISTA_DEFECTOS`, con sus nombres y orden, manteniendo `RECH` y los diez canales sin recalcularlos. Usar la clave acordada origen + fecha + molde + ramal + máquina + defecto + inspeccionadas. |
| 2026-10-15 | Separar deduplicación de filas de defecto y bases inspeccionadas compartidas; conservar contextos distintos por máquina/molde/cavidad/ramal. Comprobar posibles correcciones al cambiar `INSPECC`, registrar conflictos y no duplicar conteos. Mantener compatibilidad histórica, denominador MIA preferente de `LISTA_RECH`, idempotencia y auditoría; sin novedades válidas, no escribir Delta. |
| 2026-10-15 | Ampliar la clave de `LISTA_DEFECTOS` con `CAVIDAD` y actualizar su firma de posibles revisiones; dos cavidades distintas son identidades independientes. Comparar nulos explícitamente para mantener idempotencia. Esta decisión sustituye a la clave inicial sin cavidad; contrato `lista_defectos_v2`. |
| 2026-10-15 | Ante la misma clave con rechazos/canales distintos, conservar lo aceptado y guardar íntegra la fila candidata en el registro persistente de revisión, sin sumar, descartar, duplicar en datos activos ni sobrescribir; deduplicar también los casos repetidos de revisión. |
| 2026-10-15 | Conservar las nueve columnas originales de `LISTA_RECH`, en su orden y sin alterar contadores. Aplicar la clave origen + fecha + molde + ramal + máquina + inspeccionadas + cavidad y las mismas garantías de deduplicación, revisión persistente, idempotencia y no escritura sin novedades de `LISTA_DEFECTOS`. |
| 2026-10-15 | Confirmar que `LISTA_RECH` aporta preferentemente las inspeccionadas del denominador MIA y `LISTA_DEFECTOS` los rechazos por defecto del numerador; no multiplicar bases al cruzar ambas fuentes ni cambiar silenciosamente reglas del histórico. |
| 2026-10-15 | Conservar los 12 campos de `LISTA_AR` y los tramos separados de una misma agrupación, incluidos `39954` en AV21 y `19607` en AV32; `ID_Agrupación` no es clave única de registro. Comparar el contenido íntegro y usar identidad de tramo sin `FECHA_OFF` para completar cierres válidos sin duplicar. |
| 2026-10-15 | Aplicar a `LISTA_AR` repeticiones sin escritura, cierres idempotentes, conservación íntegra de candidatas contradictorias para revisión y auditoría/recuperación persistentes. Posponer el cómputo de minutos-cavidad; no fusionar intervalos ni modificar la lógica de cálculo al documentar la ingesta. |
| 2026-10-15 | Conservar los 17 campos originales de `CONT_ZC`, con `BIGINT` para contadores/indicador, `TIMESTAMP` para fecha y `DECIMAL(12,2)` para velocidades. Clave origen + máquina + fecha; duplicados sin escritura y variaciones conservadas íntegramente para revisión, con idempotencia, auditoría y recuperación de commits inciertos. |
| 2026-10-15 | Confirmar rendimiento ZC mediante `100 × SUM(RUEDA) / SUM(TEORICO_A + TEORICO_B)` sobre observaciones elegibles comparables; no sustituir rueda por expulsor mediante el indicador, promediar porcentajes individuales o inventar resultados sin denominador positivo. Quedan documentados los nueve contratos Fenix; implementación y validaciones operativas pendientes. |
| 2026-10-15 | Acordar Bronze con CSV seleccionados inmutables por recepción, sin réplicas Delta inicialmente; Silver con nueve tablas de origen consolidadas y referencias. La retención de originales es independiente del histórico y de la evidencia de revisión. |
| 2026-10-15 | Admitir entregas de archivos nuevos cada diez minutos o revisiones de archivos existentes: deduplicar por contratos, completar solo actualizaciones válidas y registrar el proceso. Crear una tabla Delta por familia/ámbito, no por entrega, día o producción; elegir particionado y compactación por medición. |
| 2026-10-15 | Acordar un catálogo derivado Silver que incluya todas las fabricaciones observadas, también las históricas cerradas; buscar por modelo para recuperar producciones anteriores sin exigir fechas al usuario. |
| 2026-10-15 | Delimitar cada producción por la primera `FECHA_ON` ZC y la última `FECHA_OFF` ZF de sus registros relacionados mediante `MP9_CAMBIO_MODELO.ID_ST1_REL`. Mantener identidad estable, no unir entradas por modelo/fechas próximas ni separar una serie por turnos, archivos o paradas. No usar `LISTA_AR` para determinar el cierre de producción. |
| 2026-10-15 | Conservar concurrencia y tránsito entre ZC y ZF: la entrada de un modelo nuevo en ZC puede preceder a la salida del anterior en ZF. No imponer una única producción activa por línea/carpeta ni cerrar otra fabricación por la entrada de un modelo distinto. |
| 2026-10-15 | Incorporar el ejemplo AV32 del 14/10: 125 filas originales, diez ID y cinco fabricaciones; tres cerradas y dos abiertas según los valores observados. Los cambios de `ZF_OK` siguen pendientes de precedencia y no se resuelven automáticamente por el ejemplo. |
| 2026-10-15 | Mantener los nueve contratos Silver de origen. Incorporar preferentemente `id_produccion` en datos derivados para consulta, con trazabilidad y revisión de ambigüedades. Aparcar la tabla independiente de ramales y no exigir cuatro tablas relacionales nuevas ni una tabla universal de asignaciones. |
| 2026-10-15 | Una relación pendiente se registra, reevalúa y escala tras un umbral por acordar; no bloquea indefinidamente ingestas independientes ni cierra producciones por timeout. Una producción abierta y un aviso/rechazo abierto son situaciones distintas. |
| 2026-10-15 | En máquinas tándem con una rueda compartida sin desglose por modelo, asociar el mismo rendimiento máquina/periodo a las producciones coincidentes. Conservar contadores físicos una sola vez y evitar doble conteo en consultas conjuntas; no inventar repartos A/B o porcentajes individuales por producción. |
| 2026-10-16 | Incorporar a la derecha del esquema de catálogo `ID_ST1_REL`, copiado del vínculo ZF–ZC validado como referencia de trazabilidad de la misma producción. No representa a la producción anterior y no se reutilizará con ese significado. |
| 2026-10-16 | Validar los archivos MP9 y sus diccionarios de los días 13 y 14 en AV21, AV22, AV23, AV31 y AV32: 1287 filas MP9, 48 ID y 24 producciones candidatas, 17 cerradas y siete sin cierre. Todos los vínculos ZF–ZC se resuelven uno a uno; la precedencia de `ZF_OK` de AV32/2007 continúa pendiente. La nueva investigación de asignación de defectos, rechazos y contadores se comunicará por chat, sin incorporar sus resultados al plan hasta acordarlos. |
| 2026-10-16 | Tras su revisión en el chat, incorporar los resultados de la investigación de asignación y acordar una tabla derivada Silver de asociación temporal línea–ramal–producción. Sustituye la decisión de aparcar esta relación, sin reincorporar `MP9_MOD_LINEA_ZF` ni otra fuente excluida y sin cambiar los nueve contratos de origen. |
| 2026-10-16 | Conservar vigencias de circulación efectiva ZF y evidencia de cruces modelo–molde–cavidad, diferenciadas de las fechas declaradas MP9. No unir fabricaciones solo por modelo ni usar avisos esporádicos como prueba universal de todas las filas o vigencias. AV31 queda a la espera de la consulta del usuario. |
| 2026-10-16 | Confirmar que la fecha de las muestras de diez minutos indica su final: 07:30 representa 07:20–07:30. La hora de recepción no altera el periodo y el primer registro a las 08:00 no fija una primera botella física exactamente a las 08:00. |
| 2026-10-16 | Admitir periodos reales sin producción durante la preparación. No prolongar la producción antigua ni adelantar la nueva para rellenarlos. Una muestra con botellas antiguas y preparación se atribuye a la saliente; con primeras botellas nuevas y preparación, a la entrante. No asignar al modelo nuevo únicamente por la hora final ni inventar repartos si hay mezcla o contradicción. |
| 2026-10-16 | Exigir identidad, referencias válidas, no solapamiento y correcciones auditadas en la relación temporal. Preparar `id_produccion` en datos de consulta para evitar repetir cruces en cada petición; medir latencia real en lugar de atribuir una garantía de rendimiento mínimo al diseño de tablas. |
| 2026-10-16 | Iniciar la futura implementación en entregas pequeñas: base de ingesta y `SIP_MODELO`, demás diccionarios, MP9/catálogo, medidas y relaciones/consumo. La confirmación del despliegue remoto y las incidencias de AV31 no impiden desarrollar y validar localmente las unidades independientes. Esta edición solo modifica el plan, no inicia código ni despliegues. |
| 2026-10-16 | Documentar el piloto de entrega para todas las líneas: las familias modificadas en los últimos diez minutos generan un nombre con fecha/hora nueva; las no modificadas conservan el anterior y pueden convivir varias versiones. Mantener soporte de archivos sin hora sobrescritos o modificados, incluido modo mixto/cambio de formato. Detectar revisiones por contenido y estabilización, no solo por nombre; conservar las mismas tablas y claves Silver. Esta revisión documental no inicia la implementación. |
| 2026-10-16 | Por instrucción del usuario, crear `Visualizador/Ingesta_Delta_Lake` para todo el código, pruebas, configuración de ejemplo, scripts y documentación de ingesta. Mantener el plan arquitectónico como referencia única y descomponer su ejecución en doce fichas pequeñas. No modificar backend/frontend ni sus entornos. |
| 2026-10-16 | Completar la entrega 01 local: Python 3.12 y dependencias bloqueadas propias, descubrimiento/captura Bronze, contrato `SIP_MODELO`, inserciones Delta, SQLite y bloqueo de escritor; 62 pruebas superadas y cobertura 92,46 %. Conciliación real: 316333 filas de diez archivos → 2309 asociaciones; replay sin filas/archivos/versiones Delta adicionales y originales intactos. Workflow CI preparado, sin ejecución en GitHub ni despliegue productivo. |
| 2026-10-16 | Completar la entrega 02: `SIP_DEFECTO`, cuatro campos conservados, MEDIDA sin efecto y exclusiones NO USAR auditadas. Diez CSV reales → 378 entradas válidas y 86 exclusiones distintas, replay sin commits y originales intactos; 114 pruebas y cobertura 93,97 %. Continuar solo con código y comprobaciones locales; GitHub y despliegue se aplazan por indicación del usuario. |
| 2026-10-16 | Completar la entrega 03: `SIP_DEFECTO_STATUS`, seis campos conservados y ACTIVO sin efecto; nombres Fenix y códigos ORIGEN/ZONA preservados. Diez CSV reales → ocho entradas, cero incidencias y replay sin cambios; 159 pruebas y cobertura 94,70 %. Control/tabla independientes, campos discrepantes auditados y recuperación sin duplicados. Sin nueva semántica de códigos ni despliegue remoto. |
| 2026-10-16 | Completar la entrega 04: `SIP_DEFECTO_AR`, cuatro campos conservados, ID/código únicos y agrupación numérica sin etiquetas inferidas. Diez CSV reales → nueve entradas, cero incidencias y replay sin cambios; 196 pruebas y cobertura 95,25 %. Cuatro diccionarios con controles/tablas independientes, conflictos y recuperación probados; sin modificar aplicación ni desplegar. |
| 2026-10-16 | Completar la entrega 05 de ensayo: MP9 conserva siete campos originales más el origen técnico, valida modelos/ZC referenciados, inserta por origen/ID y solo actualiza cierres nulo→fecha compatibles mediante MERGE. Aiala: 46 registros iniciales y dos cierres, 48 ID revisados; AV32/2007 en revisión y 2008 pendiente, sin fingir carga completa. Replay sin commits adicionales; 242 pruebas y cobertura 94,71 %. Fechas locales timestamp_ntz explícitas solo para ensayo; zona/DST y precedencia ZF_OK productivas pendientes. |
| 2026-10-16 | Completar la entrega 06 local: catálogo derivado desde MP9/modelos aceptados, ID canónico por planta/origen/raíz ZC y búsqueda de solo lectura por código de botella. Aiala: 24 candidatas, 23 publicadas —17 cerradas/seis sin cierre— y AV32/2007 pendiente; no se elude revisión de fuente. Cierre tardío conserva identidad; publicaciones fijan snapshots, composición/cobertura y permiten recuperar fallos entre tablas. 268 pruebas, cobertura 93,67 %, replay sin commits adicionales ni cambios en fuentes. |
| 2026-10-16 | Completar la entrega 07 local: LISTA_DEFECTOS conserva sus 18 campos y añade origen técnico; valida clave con cavidad, firma sin INSPECC y grupo sin defecto. Diez CSV reales → 33.937 filas y 23.419 bases compatibles, sin incidencias; replay sin commits y originales intactos. Rechazos/canales no se recalculan; contradicciones completas persisten en revisión y las unidades independientes pueden publicarse como parciales. 307 pruebas, cobertura 93,03 %. Consulta de bases de solo lectura, sin tratar inspecciones entre máquinas como botellas únicas ni activar el fallback del score. LISTA_RECH y consumo siguen pendientes. |
| 2026-10-16 | Completar la entrega 08 local: LISTA_RECH conserva nueve campos y origen técnico, claves con cavidad nula y firmas sin INSPECC; cambios de RECH/RECH_AUTO/FECHA_R requieren revisión íntegra, sin actualización automática. Diez CSV reales → 202.931 filas, cero incidencias, 84 fechas automáticas vacías válidas con RECH_AUTO positivo; replay sin commits y originales intactos. Núcleo numérico histórico contrastado en seis escenarios, sin integración del score. 347 pruebas, cobertura 93,12 %. Auditoría JSON presenta coste elevado de espacio/tiempo; optimizar en otra entrega antes de producción, preservando recuperación y evidencia. |
| 2026-10-16 | Completar la entrega 09 local: LISTA_AR conserva doce campos y origen técnico, tramos separados por identidad compuesta y cierres nulo→fecha válidos con igualdad de nulos. Diez CSV → 177 tramos cerrados, incluidos AV21/39954, AV32/19607 y cinco sin agrupación; referencias resueltas por códigos en los tres diccionarios. Replay sin commits, fuentes/diccionarios intactos. Dependencias persistentes, cierre/reintentos y snapshots fijados probados; 395 pruebas y cobertura 92,85 %. Sin cálculo de minutos-cavidad, asignación de producción o integración del score. |
| 2026-10-16 | Completar la entrega 10 local: CONT_ZC conserva 17 campos y origen técnico, velocidades DECIMAL(12,2) exactas y claves origen/MAQ_IS/FECHA; conflictos íntegros y reintentos sin duplicados. Diez CSV → 827 observaciones, igualdad campo a campo y replay sin commits; 135 puntos >100 % preservados/señalados, no recortados. Consulta de rendimiento resuelto con teórico positivo, porcentaje nulo si no evaluable y ventana de finales de muestra sin imputaciones. 455 pruebas, cobertura 92,81 %. Las nueve fuentes quedan implementadas en ensayo; relaciones, consumo/score, horario productivo, optimización y despliegue pendientes. |
| 2026-10-19 | Confirmar AV31B/ID_MAQUINA 8/1617–1618, modelo 1358/242 desde 03/07/2026 06:00, y AV31A/7/1643–1644 desde 24/09. Catálogos originales resuelven ramales 1–2 frente a 3–4. Autorizar MP9 v2 con ID_MAQUINA y reincorporar SIP_MAQUINA_MOLDEO/SIP_LINEA_ZF con alcance mínimo y vigencias. Objetivo: once fuentes; implementación/migración separadas y pendientes. |
| 2026-10-19 | Distinguir producción abierta de actividad instantánea y frescura; STATUS/ZF_OK no acreditan marcha/parada. Mantener final global ZF y pendiente la semántica/precedencia ZF_OK de AV32. Secuencia: validación local → GitHub/CI → piloto servidor → validación continua/integración app. |
| 2026-10-19 | Verificar las tándem detectadas en los cinco orígenes disponibles: AV31A/7 y AV32A/9 usan ramales 3–4; AV31B/8 y AV32B/10 usan 1–2. Las correspondencias proceden de catálogos, no de una regla por letra. Mantener abierta una producción conocida sin cierre válido, sin exigir consulta SQL ni otro indicador. Las fronteras MIA y ZF_OK conservan sus pendientes específicos. |

## 15. GitHub, entornos Windows y ciclo de despliegue

### 15.1. Modelo operativo inicial

El servidor es Windows y por ahora no dispone de acceso SSH; se prevé habilitar virtualización, pero no se ha confirmado un runtime de contenedores operativo. La sección 1.1 define Docker como distribución preferida si TI valida ese runtime. La ejecución nativa con Python y tarea programada sigue siendo alternativa. Ninguna modalidad sustituye las garantías de integridad, almacenamiento y recuperación.

GitHub será la fuente del código, contratos, pruebas, lockfiles y scripts. La CI verificará cada cambio y preparará releases de imagen o nativas, según la modalidad. La instalación/activación inicial será manual desde el servidor, mediante `.bat` que delegue la lógica en PowerShell y use el acceso autorizado al host del motor si procede. No describir este circuito como despliegue continuo automático mientras no exista el mecanismo de entrega y ejecución correspondiente.

No subir a Git datos de producción, tablas Delta, `_delta_log`, entornos virtuales, credenciales ni logs operativos. Las muestras de prueba serán sintéticas o aprobadas. Las referencias de negocio se distribuirán/versionarán por un canal autorizado; su existencia como copia local no autoriza publicarlas en un repositorio externo.

### 15.2. Ramas y destinos

Convención propuesta: `development` para preparación y pruebas; `main` para código promocionable a producción. No crear ambas convenciones `dev` y `development` a la vez.

| Procedencia y modo | Acción permitida | Destino de datos |
| --- | --- | --- |
| PR/`development` — validación | Preparar/reutilizar entorno candidato, ejecutar pruebas y registrar resultados. | Raíz de prueba propia de esa ejecución; sin credenciales/permisos productivos de escritura. |
| `main` — promoción productiva | Validar la release exacta en pruebas y activar el código para la tarea operativa. | El despliegue no escribe datos Delta. La ingesta posterior utiliza su configuración productiva. |
| Rama desconocida, procedencia no verificable o combinación incompatible | Rechazar. | Ninguna escritura. |

El procedimiento recibirá modo y release explícitos y verificará su procedencia. No deducir autorización únicamente con `git branch`: un artefacto puede estar fuera de una rama y un nombre local puede modificarse. Las reglas de CI/entornos de GitHub y los permisos del servidor complementan la comprobación de metadata.

**Un merge a `main` genera un candidato de producción, no una orden de modificar tablas ni de ejecutar una migración.** Requiere CI satisfactoria para ese commit, release identificada y activación mediante el procedimiento. `main` no evita las pruebas sobre un destino separado: ambas procedencias deben validarse.

La validación de `development` no registra/sustituye la tarea productiva, no cambia el puntero de release activa y no ejecuta mantenimiento productivo. Las pruebas no consumen ni adelantan checkpoints de la ingesta real.

### 15.3. CI y paquete de release

La CI ejecutará comprobaciones de formato, análisis estático, pruebas unitarias e integración Delta con una versión fijada de Python. En modalidad nativa, un runner Windows probará la ingesta. En modalidad Docker Linux, la CI probará obligatoriamente la imagen Linux final; las comprobaciones Windows cubrirán los scripts de instalación/operación Windows que se distribuyan y la alternativa nativa si se mantiene soportada. Probar en Windows fuera de un contenedor no sustituye las pruebas de la imagen Linux ni viceversa.

Los runners hospedados no reproducen todos los permisos, antivirus, rutas, filesystem o políticas de vuestro Windows Server. Por ello, se exige también una validación controlada en el destino antes de activar una release.

Cada paquete/descriptor de release identificará: repositorio, commit exacto, procedencia, ejecución CI y resultado, hashes del artefacto/lockfile, Python/arquitectura compatibles y versiones de contratos/control/protocolo soportadas. Los checks aplicables al commit desplegado deben verificarse; no basta con que otro commit de `development` estuviera en verde.

Preferencia: distribuir el artefacto de release validado. Alternativa: clonar inicialmente y obtener después una etiqueta/commit exacto con correspondencia demostrada a la CI. No realizar `git pull` sobre el código de un proceso en ejecución ni desplegar una rama mutable sin identificar el commit.

Si hay acceso a paquetes, instalar desde el lockfile. Sin ese acceso, generar un paquete offline con wheels compatibles y huellas verificables; probar su instalación en un entorno limpio equivalente. No subir el entorno virtual a Git ni trasladarlo entre directorios.

### 15.4. Entornos virtuales y dependencias incrementales —modalidad nativa—

Este apartado se aplica a ejecución nativa, no a instalar paquetes en un contenedor operativo. Con Docker, las dependencias se fijan en el build, se reutiliza caché de capas y se distribuye otra imagen; el servidor obtiene/carga la nueva imagen y valida sin modificar la anterior. Las reglas de aislamiento, activación y recuperación son comunes a ambas modalidades.

El script inspeccionará si existe el entorno previsto y si es utilizable: Python, arquitectura, intérprete, integridad del entorno e inventario de paquetes. Comparará nombres/versiones y dependencias transitivas con el lockfile aplicable a la release, y comprobará consistencia mediante las herramientas elegidas.

| Resultado | Comportamiento |
| --- | --- |
| Entorno compatible existente y dependencias conformes | Reutilizar sin reinstalar paquetes. |
| Paquetes requeridos ausentes | Instalar solo los ausentes y las dependencias necesarias, conforme al lockfile, en un entorno que no esté siendo usado por la ingesta activa. |
| Versión instalada incompatible con el lockfile | Preparar el cambio necesario de versión; no aceptar un paquete solo porque su nombre esté instalado. |
| Paquetes sobrantes | Aplicar la política acordada de entorno exacto. Cualquier retirada se hará en el candidato inactivo, no sobre un entorno operativo. |
| Python/arquitectura incorrectos, entorno corrupto o sin trazabilidad | Crear/reconstruir un candidato; conservar el entorno activo intacto. |

El criterio «solo instalar lo nuevo» evita reinstalar dependencias que ya cumplen, pero no puede impedir una actualización o retirada expresamente requerida por una release. Dejar versiones incompatibles no es reproducible. No usar actualizaciones generales a las últimas versiones ni resolver dependencias libremente en producción.

Propuesta de herramienta: `uv` con lockfile y sincronización congelada. La sincronización exacta puede retirar paquetes sobrantes; esa acción solo se admite sobre un candidato inactivo y se incluirá en el reporte. La herramienta definitiva se fijará tras validar el entorno remoto.

Para evitar modificar un entorno en uso, gestionar entornos con identidad basada en Python/arquitectura y conjunto bloqueado de dependencias. La ubicación será estable y externa a Git:

- Si el entorno compatible ya existe y es conforme, reutilizarlo sin mutaciones.
- Si el conjunto cambia, preparar un entorno candidato con otra identidad, reutilizando descargas/caché para evitar costes innecesarios. Si ese candidato ya existe, inspeccionarlo e instalar únicamente sus diferencias.
- No copiar una `.venv` entre ubicaciones ni moverla después de instalar. Los entornos son reconstruibles, pero sus rutas pueden formar parte de los ejecutables instalados.
- Un entorno compartido por releases conformes se considera inmutable mientras alguna esté activa o retenida para rollback.
- No instalar dependencias concurrentemente con pruebas que utilicen el mismo entorno.
- La tarea utilizará la ruta explícita del intérprete; no dependerá de una consola con el entorno previamente activado.

El reporte de preparación enumerará paquetes reutilizados, instalados, cambiados o retirados y el motivo. Si no hay diferencias, no ejecutar instalaciones innecesarias.

### 15.5. Primer despliegue

En modalidad Docker, sustituir la preparación de código/venv por la obtención y comprobación de la imagen exacta, mounts y descriptor. Los pasos de validación separada, lectura sin escrituras, activación y auditoría se mantienen. No instalar Python/paquetes en Windows solo para ejecutar una imagen Linux.

1. Verificar release/CI/huellas, requisitos de servidor, conectividad o paquete offline y rutas de instalación.
2. Verificar la separación de raíces y permisos. La preparación de infraestructura de datos es un paso independiente; no inicializar Delta productivo desde el instalador.
3. Instalar el código en una ubicación de release final y estable, externa a los datos.
4. Inspeccionar/preparar el entorno candidato conforme al apartado anterior.
5. Ejecutar validaciones en un directorio de prueba exclusivo, con muestras y configuración de prueba; comprobar creación, inserción, corrección, idempotencia y fallo/recuperación.
6. Validar en modo de solo lectura la configuración/rutas y compatibilidad de producción; este modo no crea tablas, no avanza checkpoints y no ejecuta merges.
7. Registrar el descriptor activo y crear/configurar la tarea operativa con rutas explícitas y sin solapamientos.
8. Registrar recibo de instalación, release, entorno, pruebas y configuración. La primera ingesta productiva será una ejecución operativa identificada y auditada, no una prueba del instalador.

Un fallo antes de activar no modifica datos ni deja una tarea productiva parcialmente configurada. Los resultados y datos de prueba quedan separados y tienen su propia retención.

### 15.6. Despliegues posteriores

En modalidad Docker, los siguientes pasos se aplican a imágenes/digests y configuraciones de ejecución. Preparar el candidato significa pull/load y validar la imagen; no hacer `pip install` en el contenedor activo. Activar significa cambiar la imagen que ejecutarán los próximos jobs sin eliminar ni sustituir los mounts de datos/control. Conservar la imagen anterior mientras sea necesaria para rollback o recuperación.

1. Adquirir el bloqueo de despliegue para impedir dos activaciones simultáneas.
2. Leer el descriptor de la release/entorno activos y detectar si la versión solicitada ya está activa. En ese caso verificar y devolver un resultado idempotente; no reiniciar ni instalar de nuevo por defecto.
3. Verificar la nueva release exacta y su CI, compatibilidad de esquema/control/protocolo y contratos soportados.
4. Preparar código y entorno candidato, inspeccionando y aplicando únicamente las diferencias de dependencias necesarias. No editar el código ni el entorno que ejecuta la tarea activa.
5. Ejecutar todas las pruebas del candidato sobre su destino aislado. Si fallan, descartar la activación y mantener la release anterior.
6. Solicitar una frontera segura de activación: impedir nuevos arranques programados, comprobar instancias lanzadas manualmente y esperar a que finalice el lote/unidad conforme a la política. No terminar por la fuerza un proceso que podría estar confirmando un commit.
7. Confirmar que no hay lote parcial ni operación `UNKNOWN` sin resolver. Si la hay, resolver con la release anterior o aplicar el procedimiento explícito de recuperación compatible antes de cambiar el ejecutor.
8. Adquirir el bloqueo operativo compartido, comprobar que no existe un escritor activo y activar el descriptor de release/entorno/configuración mediante sustitución atómica soportada.
9. Actualizar la tarea si corresponde, comprobar su configuración sin escrituras productivas y reanudar la programación. Liberar bloqueos de forma controlada.
10. Registrar el resultado y conservar la release, entorno y descriptor anteriores para rollback. Observar la siguiente ejecución real mediante su auditoría operativa.

La preparación y pruebas pueden coexistir con una ingesta activa aislada. **La activación no se solapa con un escritor productivo.** Bloquear solo el Programador de tareas es insuficiente: la exclusión debe abarcar también ejecuciones manuales y mantenimiento.

Si la espera de una frontera segura excede el límite acordado, abortar la activación conservando la versión previa y restaurando la programación que se hubiera suspendido. Si falla el cambio del descriptor/tarea, reconciliar qué versión quedó activa y restaurar la configuración anterior de manera idempotente; no iniciar dos escritores.

Un despliegue desde `development` termina tras la preparación/validación y reporte: no ejecuta los pasos de activación productiva. Una release de `main` solo puede activarse si cumple las mismas pruebas y el resto de requisitos.

### 15.7. Rollback y cambios incompatibles

Rollback significa recuperar código, entorno y configuración de una release anterior, usando la misma frontera segura y bloqueos. **No restaurar automáticamente las tablas Delta ni el control operativo.** Las escrituras que ya se confirmaron permanecen como hechos auditados.

En Docker se selecciona la imagen previa por identidad inmutable, manteniendo las ubicaciones de datos y control. No combinar migración de host/storage con una simple actualización o rollback de imagen; el traslado de datos será otra operación con validación propia.

Antes de volver atrás, comprobar que la release anterior entiende las tablas, metadatos y estados de control actuales. Si no es compatible, no arrancarla: corregir hacia delante o ejecutar un procedimiento específico de recuperación.

Una migración de esquema, elevación de protocolo Delta, reinterpretación histórica o cambio de identidad de filas será una operación de datos separada: plan, pruebas, copia/recuperación, identidad de operación y autorización del alcance. No se ocultará en un script de instalación, importación de módulo, prueba básica o arranque de servicio.

El cambio de reglas de negocio se versionará y quedará reflejado en nuevos lotes/configuración según la política acordada. Una actualización de código no recalculará todo el histórico silenciosamente.

### 15.8. Pruebas de aceptación del despliegue

- [ ] Una validación de `development` no puede escribir, compactar ni limpiar Delta productivo.
- [ ] Una release `main` sin CI válida para su commit o con metadata incoherente no se activa.
- [ ] La prueba aislada es obligatoria también al promover a producción.
- [ ] Entorno ya conforme: no se reinstala; paquete ausente: se instala solo la diferencia necesaria.
- [ ] Cambio de dependencias: el entorno activo no se modifica y el candidato coincide con el lockfile.
- [ ] Primera instalación y actualización no producen commits ni alteran archivos Delta productivos por acciones del instalador.
- [ ] Fallo al instalar/probar/activar: la versión anterior permanece o se restaura sin tocar los datos.
- [ ] Activación bloqueada mientras exista un escritor o un commit pendiente de reconciliar.
- [ ] Repetir el mismo despliegue no crea otra tarea ni otro escritor.
- [ ] Rollback de código no borra ni revierte lotes confirmados.
- [ ] Cambio incompatible se detecta antes de activar y exige un procedimiento de datos separado.
- [ ] Recibo de despliegue y auditoría permiten identificar commit, entorno, pruebas y versión activa real.

Las comprobaciones de ausencia de cambios en Delta se harán en una ventana sin escritor o en una réplica de ensayo. No atribuir a un despliegue los commits legítimos de una ingesta que haya seguido ejecutándose durante la preparación.

## 16. Catálogo de producciones y asociación de datos a la aplicación

Estado actual: `catalogo_producciones_v2` implementado y probado con datos sintéticos, con máquina física desde MP9 v2 y su catálogo conforme a 17.14/17.16, conservando las identidades de producción. Los resultados reales históricos descritos debajo corresponden a entrega 06 v1 sobre nueve fuentes; no se han reconstruido ni migrado con el código nuevo. El catálogo sigue siendo Silver derivada; salidas/agregados para consulta pertenecen a 3.4. La revisión 31 sustituye las exclusiones anteriores de ID_MAQUINA y los dos catálogos físicos, sin alterar los otros ocho contratos de origen.

### 16.1. Finalidad e identidad de una producción

El catálogo conservará cada fabricación de un modelo de botella en la planta, con su inicio y su final conocidos, incluida la información histórica cerrada que continúe apareciendo en los volcados. Permitirá buscar un modelo, descubrir cuándo se fabricó anteriormente y comparar esas producciones con una actual o reciente sin que el usuario tenga que recordar fechas.

Distinguir tres identidades:

- **Modelo de botella:** referencia del diccionario `SIP_MODELO`. Puede fabricarse muchas veces.
- **Producción:** una entrada o serie de fabricación concreta, continua como entidad de negocio desde su entrada ZC hasta su salida ZF. Una parada temporal no crea automáticamente otra producción.
- **Registro MP9:** entrada/salida de un modelo en una zona. Los registros ZC y ZF tienen ID distintos y pueden describir la misma producción.

La identidad del modelo no identifica la producción. Tampoco lo hacen la carpeta y la fecha: una línea puede alojar diferentes modelos simultáneamente. No aplicar `MIN(FECHA_ON)` y `MAX(FECHA_OFF)` sobre todo el histórico de un modelo; mezclaría fabricaciones separadas por meses o años.

Una nueva entrada ZC crea una producción candidata. Su referencia natural inicial es `(planta, origen_fenix, ID_ZC)`, con el ámbito estable de 4.5.3. La implementación fija el identificador canónico `fenix-production-v1:<planta>:<origen_fenix>:<ID_ZC>`, por ejemplo `fenix-production-v1:AIALA:AV32:2003`. Los ámbitos se validan sin `:` y el ID entero utiliza su representación decimal canónica; la clave es inequívoca y no depende de modelos, fechas actualizables, orden de filas o hash de toda la fila. Los cierres mantienen ese mismo identificador.

Comprobar por origen que una raíz ZC representa efectivamente una fabricación. Si una fabricación necesita agrupar excepcionalmente varias raíces ZC, exigir evidencia o resolución explícita, conservar la composición y los identificadores anteriores y versionar la regla. No fusionar raíces por compartir modelo o por proximidad temporal. El vínculo ZF–ZC no demuestra por sí solo una relación entre dos raíces ZC diferentes.

### 16.2. Delimitación temporal acordada

ZC es la zona caliente, al principio de la línea; ZF es la zona fría, al final. Sus fechas describen presencia en dos posiciones físicas. No son dos producciones distintas ni una segmentación artificial de la producción global.

1. Detectar una entrada ZC nueva y válida (`STATUS = 1`) e identificar su producción candidata.
2. Resolver cada registro ZF (`STATUS = 2`) mediante su `ID_ST1_REL` contra un registro ZC del mismo ámbito y modelo, conforme a 4.5.3.
3. Dentro de la composición validada de esa producción, fijar `fecha_inicio_zc` como la primera `FECHA_ON` de sus registros ZC.
4. Fijar `fecha_fin_zf` como la última `FECHA_OFF` de sus registros ZF únicamente cuando todos los registros ZF correspondientes conocidos estén cerrados, sus relaciones sean válidas y no exista una dependencia o contradicción que impida confirmar el final. No tomar el máximo de las fechas no nulas ignorando otros registros abiertos.
5. Si todavía falta su registro ZF, existe una salida ZF sin cierre o no se puede confirmar la relación, conservar `fecha_fin_zf = NULL` y distinguir cobertura pendiente de cierre todavía no recibido. No fabricar un final.
6. No separar una producción por cambios de turno, archivos diarios, nuevas entregas o paradas temporales. No unir producciones únicamente por compartir modelo y fechas próximas.

No cerrar una producción por el inicio de otra, por `ZF_OK`, por su desaparición de un archivo, por timeout ni por la fecha de recepción. Una salida de ZC no equivale al final global: el producto anterior puede seguir recorriendo ZF. Conservar las fechas de cada zona en los registros MP9 originales para comprobar la delimitación derivada.

Puede entrar un modelo nuevo en ZC antes de que el anterior haya terminado de salir de ZF. También puede continuar otro modelo por un circuito paralelo de la misma línea. La comprobación de cambios debe seguir los registros y vínculos correspondientes, no una secuencia global de todos los modelos de una carpeta. No exigir que primero salga el anterior de ZF y después entre el nuevo en ZC. Estas fechas son las declaradas por Fenix; la fabricación como entidad de negocio puede incluir preparación o paradas sin flujo de botellas. La circulación efectiva para asignar observaciones se representa por separado en 17, admitiendo huecos y evidencia posterior sin sobrescribir MP9.

Los límites se interpretarán con la política temporal de planta y, para evitar doble pertenencia exacta al cambio, con periodos `[inicio, fin)` cuando haya cierre. La continuidad física exacta y la precisión de las fechas Fenix frente al cambio efectivo deben contrastarse; no utilizar la hora de llegada del archivo como sustituto.

### 16.3. Contenido del catálogo y trazabilidad

La tabla siguiente describe el esquema v2 implementado en `<silver_root>/<planta>/catalogo_producciones`. `ID_MAQUINA` BIGINT obligatorio precede a `ID_ST1_REL`, que sigue como última columna de negocio. La denominación física se resuelve en el catálogo de máquinas, sin duplicarla como atributo editable ni renombrar silenciosamente `linea=AV31` a `AV31B`. Los timestamps de ensayo siguen siendo timestamp(us)/timestamp_ntz bajo `local_naive_test_v1`; la política productiva permanece pendiente. Los cambios de versión/migración se rigen por 17.14/17.16.

| Campo lógico | Tipo propuesto | Nulos | Finalidad |
| --- | --- | --- | --- |
| `id_produccion` | `STRING` | No | Identidad estable y única en el catálogo. |
| `planta` | `STRING` | No | Ámbito de negocio y separación entre instalaciones. |
| `origen_fenix` | `STRING` | No | Procedencia estable que discrimina los ID MP9. |
| `linea` | `STRING` | No | Línea autorizada correspondiente al origen; no implica exclusividad de producción. |
| `id_zc_raiz` | `BIGINT` | No | Registro ZC que origina la identidad candidata. |
| `id_modelo_fenix` | `BIGINT` | No | Referencia al diccionario aceptado de modelos de la planta. |
| `fecha_inicio_zc` | `TIMESTAMP` | No | Primera entrada ZC de la composición validada. |
| `fecha_fin_zf` | `TIMESTAMP` | Sí | Última salida ZF confirmada; nulo si no puede cerrarse. |
| `ID_MAQUINA` | `BIGINT` | No | Máquina física original de la raíz ZC, validada con sus registros ZF y catálogo vigente. |
| `ID_ST1_REL` | `BIGINT` | Sí | Última columna de negocio: vínculo original del registro ZF con su ZC, dentro de esta misma producción. Nulo si todavía falta un vínculo aceptado. No identifica una producción anterior. |

El control propio `<control_root>/control-catalogo_producciones.sqlite` conserva intenciones por producción, valores anterior/nuevo, composición aceptada y propuesta, regla, snapshots y estados de cobertura. Los estados `VALID`, `PENDING_ZF` y `REVIEW` se mantienen en metadatos, separados de fecha_fin_zf. Una relación válida con salida ZF aún abierta no equivale a cobertura pendiente; una raíz sin ZF conserva nulos y `PENDING_ZF`. Las candidatas MP9 no aceptadas figuran únicamente en diagnósticos, sin introducirse como producciones publicadas.

**Significado de la columna solicitada:** en AV32, el registro ZF `2004` tiene `ID_ST1_REL = 2003`; ambos describen la fabricación de `2001/477`. El valor `2003` no apunta a la fabricación anterior. En el caso validado de una raíz ZC por producción, coincide con `id_zc_raiz`; se conserva explícitamente por trazabilidad y para facilitar la lectura del catálogo, no como una segunda relación editable. Comprobar esa igualdad al publicar. Si se validan composiciones excepcionales con varias raíces y vínculos distintos, revisar su representación antes de reducirlos a un único valor; no elegir uno arbitrariamente.

Las consultas de fabricaciones anteriores se resolverán por modelo y fechas del catálogo, conservando separadas las fabricaciones concurrentes. Este campo no permite deducir qué producción precede a otra dentro de una línea con varios circuitos. No añadir ahora una relación de predecesora inferida ni utilizar el orden de los ID como cronología de negocio.

Resolver el código de botella mediante `SIP_MODELO`, evitando una segunda correspondencia editable en el catálogo. Las fechas se derivan de MP9; no admitir ediciones independientes que puedan contradecir el origen sin una resolución auditada y una regla explícita.

Conservar en los metadatos de derivación o en evidencia persistente los ID MP9 componentes, snapshots fuente, contrato/versión de regla, lote, operación, hashes, motivos de revisión y cambios anterior/nuevo. La forma física de representar esa composición no implica aprobar ahora otra tabla relacional obligatoria. Los estados operativos de cobertura/revisión se mantendrán separados del hecho de tener o no fecha de cierre.

Si se integra el histórico existente de la aplicación, preservar sus identificadores o mantener correspondencias explícitas con los nuevos; no sustituirlos automáticamente por IDs derivados de una copia Fenix parcial. El catálogo consolidado debe señalar la procedencia y cobertura del histórico incorporado.

### 16.4. Producciones abiertas, referencias pendientes y límites de espera

Precisar el estado de negocio sin añadir un indicador fuente ficticio: una `FECHA_OFF` nula significa que ese registro MP9 no tiene cierre informado. No acredita que la máquina esté fabricando ahora, ni por sí sola que `fecha_fin_zf=NULL` sea una producción íntegramente identificada. Los estados derivados y la frescura se definen en 17.14.5. `STATUS` indica zona; `ZF_OK` no es un indicador de marcha/parada y su semántica operativa sigue pendiente de confirmación.

`ID_ST1_REL` es un campo de `MP9_CAMBIO_MODELO`, no de `LISTA_AR`. El cierre de un aviso/rechazo no determina el de una producción; no esperar a que los AR estén cerrados ni buscar parejas ZC/ZF de AR para cerrar el catálogo. `LISTA_AR.STATUS` describe procedencia conforme a su diccionario; no es el `STATUS` de zona de MP9.

| Situación | Interpretación y actuación |
| --- | --- |
| Producción con entrada y relación conocidas, pero sin salida ZF informada | Se considera abierta hasta recibir un cierre válido, por acuerdo operativo del 19/10. Puede durar días/semanas y no exige una señal SQL o de actividad adicional. No hay un máximo de duración acordado. |
| Registro ZF recibido antes de su ZC o modelo referenciado | Dependencia pendiente; conservar evidencia y reevaluar al llegar el dato relacionado. No emparejar por fecha/modelo aproximados. |
| Falta información esperable o la fuente lleva tiempo sin actualizarse | Incidencia de cobertura/actualidad, diferenciada de una producción físicamente abierta. |
| Relación incompatible, cierre contradictorio o contenido no resuelto | Revisión de negocio según 4.5, con candidatas completas y sin sobrescribir lo aceptado. |

Definir un umbral configurable de antigüedad para alertar y escalar dependencias, considerando cadencia real, retrasos de entrega y ventanas sin servicio. Su valor y periodicidad de revisión no están acordados todavía; el ejemplo conversado de tres entregas no constituye una regla aprobada. Registrar primera detección, última comprobación, entregas relevantes, evidencia y responsable/estado de resolución en control.

Reevaluar pendientes cuando llegue información relacionada y mediante revisión programada. Acotar reintentos técnicos conforme a 8.4; no convertir una dependencia ausente en un bucle bloqueante ni un conflicto en un reintento infinito.

La ingesta de entregas y unidades independientes seguirá avanzando bajo el protocolo de la sección 8. Mantener identificadas las unidades incompletas y la última publicación coherente; no declarar íntegro un lote o snapshot que aún carezca de sus unidades requeridas. Si se necesita publicar ámbitos independientes, su unidad de publicación deberá definirse explícitamente. Un vencimiento de alerta nunca inventa relaciones, fechas de cierre ni observaciones.

### 16.5. Ejemplo de referencia: AV32, archivo del 14/10/2026

Fuentes locales verificadas:

- `C:/Users/iker.villen/Desktop/AI_tool/Datos/1.App_septiembre/Fenix_Aiala/Fenix/AV32/20261014_MP9_CAMBIO_MODELO.csv`.
- `C:/Users/iker.villen/Desktop/AI_tool/Datos/1.App_septiembre/Fenix_Aiala/Fenix/AV32/20261014_SIP_MODELO.csv`.

El archivo MP9 contiene **125 filas físicas, diez ID distintos, cinco modelos y trece versiones de fila distintas**. Las versiones adicionales corresponden a los cierres de `2003`/`2004` y al indicador `ZF_OK` de `2007`; no son nuevas producciones. El archivo de fecha 14 incluye historia anterior y no acredita por su nombre que cubra las 24 horas completas.

Esta proyección explicativa muestra los diez ID y las fechas de cierre observadas; traduce el modelo mediante el diccionario y `STATUS` por su zona. Los siete campos originales de Silver, incluido `ZF_OK`, se conservan conforme a 4.5; esta presentación no cambia su orden ni elimina campos del contrato.

| ID MP9 | ID_MODELO / botella | FECHA_ON | FECHA_OFF observada | STATUS | ID_ST1_REL |
| --- | --- | --- | --- | --- | --- |
| 1999 | 564 / 2000/242 | 21/09/2026 06:04:00 | 08/10/2026 07:20:00 | 1 · ZC | NULL |
| 2001 | 564 / 2000/242 | 21/09/2026 07:47:59 | 08/10/2026 08:02:50 | 2 · ZF | 1999 |
| 2000 | 2217 / 2051/477 | 21/09/2026 06:05:00 | 28/09/2026 06:04:00 | 1 · ZC | NULL |
| 2002 | 2217 / 2051/477 | 21/09/2026 07:49:55 | 28/09/2026 07:29:44 | 2 · ZF | 2000 |
| 2003 | 859 / 2001/477 | 28/09/2026 06:04:00 | 14/10/2026 06:23:00 | 1 · ZC | NULL |
| 2004 | 859 / 2001/477 | 28/09/2026 07:29:44 | 14/10/2026 07:23:14 | 2 · ZF | 2003 |
| 2005 | 2200 / 2168/242 | 08/10/2026 07:20:00 | NULL | 1 · ZC | NULL |
| 2006 | 2200 / 2168/242 | 08/10/2026 08:02:50 | NULL | 2 · ZF | 2005 |
| 2007 | 2201 / 2169/477 | 14/10/2026 06:23:00 | NULL | 1 · ZC | NULL |
| 2008 | 2201 / 2169/477 | 14/10/2026 07:23:14 | NULL | 2 · ZF | 2007 |

Resumen de negocio que debe representar el catálogo, manteniendo también las fabricaciones anteriores al día 14:

| Referencia de producción en AV32 | Modelo | Primera entrada ZC | Última salida ZF | Situación según los valores observados | ID_ST1_REL |
| --- | --- | --- | --- | --- | --- |
| Raíz ZC 1999; ZF 2001 | 2000/242 | 21/09/2026 06:04:00 | 08/10/2026 08:02:50 | Cerrada antes del día 14 | 1999 |
| Raíz ZC 2000; ZF 2002 | 2051/477 | 21/09/2026 06:05:00 | 28/09/2026 07:29:44 | Cerrada antes del día 14 | 2000 |
| Raíz ZC 2003; ZF 2004 | 2001/477 | 28/09/2026 06:04:00 | 14/10/2026 07:23:14 | Cierra el día 14 | 2003 |
| Raíz ZC 2005; ZF 2006 | 2168/242 | 08/10/2026 07:20:00 | NULL | Sin cierre registrado | 2005 |
| Raíz ZC 2007; ZF 2008 | 2169/477 | 14/10/2026 06:23:00 | NULL | Entra el día 14 y queda sin cierre | 2007 |

Son **cinco fabricaciones: tres cerradas y dos abiertas** según las fechas y relaciones observadas, no cinco modelos simultáneamente activos. No eliminar las dos históricas porque sus cierres precedan a la fecha del archivo. Las referencias de esta tabla no fijan el formato físico del futuro `id_produccion`.

La automatización sigue sujeta a la aceptación de los registros fuente: `2007` contiene versiones `ZF_OK = 0` y `1`, cuya precedencia sigue pendiente en 4.5. No presentar este resumen de negocio como cinco filas ya publicables automáticamente, elegir el último indicador o eludir la revisión de fuente para obtener el resultado esperado.

Secuencia del día 14:

| Periodo | Modelos con presencia en ZC según MP9 | Modelos con presencia en ZF según MP9 |
| --- | --- | --- |
| Antes de 06:23:00 | 2168/242 y 2001/477 | 2168/242 y 2001/477 |
| Desde 06:23:00 hasta 07:23:14 | 2168/242 y 2169/477 | 2168/242 y 2001/477 |
| Desde 07:23:14, según la última información disponible | 2168/242 y 2169/477 | 2168/242 y 2169/477 |

Los periodos declarados MP9 se solapan durante **1 hora y 14 segundos**: reflejan la entrada nueva en ZC antes del cierre anterior en ZF, mientras `2168/242` permanece sin cierre. Ese solapamiento no demuestra flujo físico continuo, ausencia de preparación ni tres modelos fabricándose simultáneamente en ZC. La secuencia tampoco identifica por sí sola los ramales físicos concretos; las evidencias posteriores y la diferencia entre fechas declaradas y circulación efectiva se tratan en 17.

Los ID `2003` y `2004` aparecen catorce veces cada uno. Para `2003` hay siete copias abiertas y siete cerradas a las 06:23:00; para `2004`, ocho abiertas y seis cerradas a las 07:23:14. Se consolidan por las reglas de cierre, no tomando arbitrariamente la última fila ni contando cada copia como otra producción.

#### 16.5.1. Validación ampliada de las cinco líneas

Validación de solo lectura realizada sobre los archivos `20261013_MP9_CAMBIO_MODELO.csv` y `20261014_MP9_CAMBIO_MODELO.csv` y sus respectivos `SIP_MODELO` de `AV21`, `AV22`, `AV23`, `AV31` y `AV32`, bajo la raíz Fenix Aiala indicada en 16.5. Se examinaron íntegramente veinte CSV, sin alterar originales, contratos ni código. Los resultados describen esta muestra, no una garantía de cobertura histórica completa ni de actividad física actual.

| Origen | Filas MP9 del 13 | Filas MP9 del 14 | Producciones candidatas consolidadas | Cerradas | Sin cierre registrado |
| --- | --- | --- | --- | --- | --- |
| AV21 | 56 | 56 | 2 | 1 | 1 |
| AV22 | 28 | 30 | 1 | 0 | 1 |
| AV23 | 56 | 40 | 2 | 1 | 1 |
| AV31 | 392 | 392 | 14 | 12 | 2 |
| AV32 | 112 | 125 | 5 | 3 | 2 |
| Total | 644 | 643 | 24 | 17 | 7 |

Los 1287 registros físicos se explican mediante 48 ID MP9 y 24 parejas ZC–ZF. Cada raíz ZC tiene exactamente un registro ZF relacionado; todas las referencias existen en su ámbito, apuntan a ZC y comparten modelo. Todos los modelos se resuelven en sus diccionarios. No se encontraron cierres distintos no nulos para el mismo ID, cierres anteriores al inicio, vínculos incompatibles ni cambios contradictorios de modelo/inicio/zona. No se observó una fabricación que obligue a agrupar varias raíces ZC ni una raíz con varios registros ZF; esos casos generales requieren validación si aparecen posteriormente.

Entre ambos días, AV21, AV22, AV23 y AV31 mantienen el mismo contenido de negocio; las variaciones del número de repeticiones no crean producciones. Solo AV32 incorpora nuevos ID (`2007`, `2008`) y completa cierres sobre ID existentes (`2003`, `2004`). Ningún ID del día 13 desaparece del día 14. La única contradicción de contenido encontrada es `ZF_OK = 0/1` de AV32/2007, todavía sujeta al contrato de revisión de 4.5.

**Caso de identidad AV31, validación del 16/10 y aclaración del 19/10:** hay 14 fabricaciones para 11 modelos. `1359/477` tiene raíces distintas `1623` y `1635`; `2939/577`, `1631` y `1641`; `1358/242`, `1617` y `1643`, con inicios 03/07 y 24/09 y sin cierre en la muestra. Modelo o estado abierto no bastan para fusionarlas. Sus máquinas originales son 8 y 7. La revisión 31 incorpora expresamente ese campo y los catálogos físicos: 8 es AV31B/ramales 1–2 y 7 es AV31A/3–4. En la secuencia de la máquina 7 coinciden cierres/entradas sucesivos por zona, sin discontinuidades observadas; no cierran la fabricación de máquina 8. Esquemas/migración en 17.14; implementación sintética completada en 17.16, reconstrucción real pendiente.

La regla de catálogo queda respaldada en las cinco líneas, con separación explícita de identidad, fechas y calidad de la fuente. Una producción sin cierre desde julio no se cierra por antigüedad ni se confirma físicamente activa solo por su nulo. La investigación posterior de asignación se incorpora en 17. El usuario consultará personalmente la posible incidencia de las dos fabricaciones abiertas de AV31; conservar la información de origen y mantener esa interpretación pendiente hasta su respuesta.

### 16.6. Actualización, consulta e idempotencia del catálogo

- Insertar una producción nueva una sola vez cuando sus fuentes y relaciones exigidas sean válidas. Registrar como candidata pendiente lo que aún no pueda publicarse sin ambigüedad.
- Incorporar las producciones históricas detectadas y mantenerlas aunque desaparezcan de un volcado posterior. La carga no se limita a las producciones abiertas o iniciadas el día del archivo.
- Completar un cierre validado sobre el mismo `id_produccion`; no convertir la producción cerrada en una entidad nueva ni reabrirla al recibir una copia antigua con nulo.
- Repeticiones idénticas o derivaciones sin cambios no generan otra fila ni un commit de negocio. Registrar la comprobación en control, sin modificar `last_seen` de todas las producciones.
- Corregir inicios, modelos, composición, vínculos o cierres contradictorios solo mediante resolución y operación auditadas. No escoger el máximo entre dos cierres incompatibles de un mismo registro: el máximo de 16.2 se aplica entre registros distintos ya aceptados de la misma producción.
- Preparar operaciones deterministas y confirmar commits conforme a las secciones 5, 8 y 9. Un fallo entre Silver de origen y catálogo no presupone atomicidad entre tablas: reanudar la derivación desde snapshots identificados y no publicar una mezcla incoherente.
- Al cerrar o corregir una producción, invalidar/reconstruir de forma controlada solo sus salidas y asociaciones afectadas, incluyendo otras producciones que compartan observaciones cuando proceda. Conservar la versión de regla y la publicación anterior hasta confirmar la nueva.

La navegación habitual será `modelo → lista de producciones → selección de una o varias → medidas y score de esas producciones`. El usuario no tendrá que aportar fechas exactas. El catálogo proporciona los límites de búsqueda; las tablas consolidadas y los datos derivados proporcionan las observaciones. No convertir toda coincidencia con la envolvente temporal ZC–ZF en una asociación automática: cada fuente mide una zona, máquina o fenómeno concreto.

La entrega 06 implementa `catalog build --config ...` y `catalog search --config ... --model '<código botella>'`. Construir deriva exclusivamente desde snapshots Silver aceptados; buscar no ejecuta ingestas, derivaciones ni escrituras. El código de botella se resuelve mediante el snapshot fijado del diccionario. Se devuelve la última publicación confirmada y sus producciones, ordenadas por inicio, con composición y cobertura. Si avanzaron los snapshots, las revisiones de fuente o la configuración, la búsqueda indica `STALE`/`fresh=false`; frescura se refiere a Silver aceptado, no a CSV pendientes de ingesta. Los commits de catálogo aún sin acuse no sustituyen el snapshot publicado.

Las publicaciones fijan versiones y hashes de MP9/modelos y un estado de incidencias conocido. Se confirman junto con composición y control después de validar el commit Delta. La recuperación puede usar esas versiones aunque las fuentes hayan avanzado y, después, derivar una publicación posterior; no mezcla la tabla más reciente con metadatos de otra versión ni presupone atomicidad entre tablas. Deben conservarse las versiones/evidencias necesarias. Una actualización solo de cobertura puede producir nueva evidencia de publicación sin commit de negocio. Composiciones que alteren una producción ya cerrada y cambios incompatibles requieren revisión, manteniendo la última fila/composición aceptadas. No existe aún un comando de resolución humana ni se migra el histórico de la app. El escaneo inicial en memoria no acredita latencia o capacidad a gran escala; medir y optimizar consumo sigue pendiente.

### 16.7. Asociación de observaciones y rendimiento tándem compartido

«Asociar observaciones» significa determinar a qué producción corresponde un registro de defectos, rechazos u otra medida para consultarlo en la app. No constituye una nueva fuente ni exige una tabla universal. Preparar esa asociación cuando sea inequívoca y conservar su evidencia; la solución física se decidirá al integrar cada fuente.

La combinación de carpeta/línea y fecha no es suficiente cuando existen modelos concurrentes. Usar los campos y relaciones disponibles que realmente identifiquen el contexto. Si no discriminan las producciones, registrar la ambigüedad o la información concreta que falta; no elegir una por proximidad ni forzar un `id_produccion` único. Se acuerda la asociación temporal producción–ramal de 17; las fuentes excluidas no se reincorporan automáticamente.

**Excepción de negocio expresamente acordada para `CONT_ZC`:** algunas máquinas son tándem; diferentes grupos de moldes fabrican modelos distintos y ambos pasan por una rueda común. Sin medida separada por modelo, no se puede conocer el rendimiento ZC individual real de cada producción. Se calculará el rendimiento observado de la máquina/periodo y se asociará ese mismo valor a todas las producciones coincidentes que compartan esa rueda.

1. Conservar en Silver una sola observación física por `K_ZC = (origen, MAQ_IS, FECHA)`, con sus 17 campos y sin columna de producción añadida al contrato.
2. Calcular el rendimiento con rueda y teóricos sobre el conjunto elegible de la máquina y periodo, conforme a 4.9.3. No sustituirlo por un cálculo exclusivo de A o B ni inventar reparto de botellas.
3. Asociar el resultado en los datos derivados a las producciones que efectivamente compartan esa máquina/rueda en el periodo. Validar la correspondencia física autorizada; compartir carpeta o tener fechas solapadas no demuestra por sí solo compartir rueda.
4. Identificar el alcance de la métrica como **rendimiento ZC de máquina compartido**. Puede contribuir a cada producción según la regla acordada, sin presentarlo como una medición individual de botella/modelo.
5. Al consultar conjuntamente varias producciones, agregar las observaciones físicas únicas de máquina/periodo, no las copias de sus asociaciones. No sumar porcentajes ni duplicar rueda/teóricos por el número de producciones seleccionadas. Para ventanas mayores, sumar numeradores y denominadores del conjunto elegible y calcular el cociente; no promediar porcentajes de ventanas distintas.

Ejemplo real de `AV32/20261014_CONT_ZC.csv`, primera fila: `MAQ_IS = 32`, `FECHA = 14/10/2026 00:00:00`, `RUEDA = 2862`, `TEORICO_A = 1458`, `TEORICO_B = 1458`. El porcentaje de esa observación es aproximadamente **98,15 %**. Si se confirma que las dos producciones concurrentes comparten esa máquina/rueda y periodo, ambas reciben el 98,15 %, pero la base física sigue siendo una sola observación de 2862 y 2916 botellas teóricas, no 5724 y 5832 por seleccionar ambas.

La aclaración de negocio del usuario valida el tratamiento compartido de máquinas tándem; no convierte automáticamente las letras A/B del CSV en identificadores de modelo o ramal ni demuestra todas las correspondencias de cada instalación.

Queda confirmado que `FECHA` marca el final del periodo de diez minutos, según 17.5. Sigue siendo necesario validar qué fechas ZC representan la presencia efectiva en la rueda; no asociar datos ZC usando indiscriminadamente toda la duración global hasta el cierre ZF ni las vigencias ZF de la tabla de ramales. Aplicar el tratamiento de preparación/transición, cobertura parcial y límites de 17.6–17.7. No prorratear, interpolar o repartir contadores sin una regla de negocio aprobada; conservar la cobertura pendiente cuando no pueda resolverse.

Los tramos de `LISTA_AR` pueden atravesar límites de producción. Se conservan íntegros en Silver conforme a 4.8; la atribución temporal y el cómputo de minutos-cavidad siguen pendientes. El acuerdo tándem de rendimiento ZC no autoriza a duplicar defectos, rechazos, eventos o minutos-cavidad entre producciones.

### 16.8. Criterios de aceptación de catálogo y consumo

- [ ] Los diez ID MP9 del ejemplo se explican como cinco fabricaciones, conservando las históricas cerradas; las incidencias de fuente se mantienen visibles y no se eluden para publicar automáticamente.
- [ ] Los cierres ZC/ZF de `2001/477` son distintos; su final global es 14/10/2026 07:23:14, no 06:23:00.
- [ ] La entrada de `2169/477` a las 06:23:00 no cierra `2168/242` ni elimina la presencia del modelo anterior en ZF hasta las 07:23:14.
- [ ] Dos fabricaciones del mismo modelo en fechas diferentes tienen identidades distintas. Turnos, archivos repetidos y paradas no crean producciones adicionales.
- [ ] Un cierre tardío completa la misma producción; reprocesar o recibir copias abiertas después no duplica ni reabre. Si otro registro ZF correspondiente sigue abierto, no declarar un final parcial como definitivo.
- [ ] `ID_ST1_REL` se resuelve en MP9 y por ámbito/modelo; ningún cierre de producción depende de que se cierre un AR.
- [ ] `ID_ST1_REL` figura a la derecha del catálogo como referencia ZF–ZC de la misma producción, coherente con su raíz; nunca se interpreta como identificador de la producción anterior.
- [ ] Dependencias pendientes se reevalúan y escalan sin bloquear ingestas independientes ni generar cierres por timeout. La publicación incompleta no se presenta como íntegra.
- [ ] El catálogo permite descubrir producciones anteriores por modelo y comparar sus datos con una producción reciente sin introducir fechas manualmente.
- [ ] Las nueve tablas de origen mantienen sus contratos. La relación temporal producción–ramal se mantiene en Silver derivada conforme a 17, con procedencia y regla versionada; no se confunde con otra fuente Fenix o un catálogo maestro independiente.
- [ ] Una rueda compartida produce el mismo rendimiento máquina/periodo asociado a sus producciones concurrentes, con contadores físicos únicos y sin doble conteo en una consulta conjunta.
- [ ] Cambios dentro de una muestra, límites temporales y cobertura parcial se prueban según una regla explícita antes de asignar automáticamente el rendimiento.
- [ ] Correcciones de fuente/catálogo reconstruyen únicamente salidas afectadas, conservando identidad, auditoría y publicaciones coherentes ante fallos y reintentos.

## 17. Asociación temporal producción–ramal y muestras de medida

Estado actual: asociación v2 implementada con propuestas/revisión/atribución/recuperación y derivación por vínculo físico máquina–ramales; ver 17.16. Las publicaciones y conteos reales de entrega 11 v1 se conservan, sin recalcularlos. La nueva conciliación real y el consumo productivo están pendientes. Se mantienen evaluación de periodos completos y exclusiones restantes, incorporando la convención aprobada de atribuir la muestra de transición a la saliente. El conflicto ZF_OK de AV32 sigue pendiente; identificar la máquina no lo resuelve ni prueba límites físicos efectivos de circulación.

### 17.1. Finalidad y encaje en el modelo de datos

Crear en Silver derivada una tabla lógica Delta `asignacion_produccion_ramal`. Una fila expresa el hecho: **una producción ocupaba un ramal de una línea, en una zona y durante un periodo de vigencia aceptado**. El alcance inicial es ZF para asignar observaciones MIA. Una producción puede ocupar varios ramales y un ramal participa en diferentes producciones a lo largo del tiempo; la relación es temporal y no se resuelve con una jerarquía fija de carpetas.

Esta entidad tiene atributos propios de vigencia y procedencia, por lo que su separación del catálogo y de las medidas responde a un modelo relacional de asociación. No es una réplica de `MP9_MOD_LINEA_ZF`, un catálogo maestro de ramales ni una fuente Fenix adicional. Tampoco obliga a copiar todas las observaciones a otra tabla de relaciones.

Referenciar `id_produccion` del catálogo de 16. No crear otro diccionario independiente modelo–producción en esta tabla. Los códigos de botella y atributos de producción se resuelven desde su entidad autorizada; si se materializan en una salida para consulta, serán valores derivados, no otra fuente editable de verdad. Si línea y origen se resuelven mediante configuración común, validar su coherencia en lugar de admitir correspondencias independientes contradictorias.

La normalización organiza los hechos y reduce inconsistencias, pero no garantiza por sí sola la menor latencia de consulta. Tampoco basta con declarar tipos para obtener unicidad, integridad referencial o ausencia de solapamientos: la implementación Delta debe validarlas explícitamente y publicar snapshots coherentes. La elección de vistas, salidas materializadas y organización física se basará en medidas, conforme a 17.10.

### 17.2. Esquema lógico, identidad y reglas de integridad

Esquema base propuesto para concretar en la implementación, manteniendo este significado de negocio:

| Campo | Tipo propuesto | Nulos en una asociación aceptada | Finalidad |
| --- | --- | --- | --- |
| `id_asignacion` | `STRING` | No | Identidad estable del episodio de asociación. No cambia al corregir sus fechas mediante una operación aprobada. |
| `planta` | `STRING` | No | Ámbito de instalación. |
| `origen_fenix` | `STRING` | No | Procedencia estable que discrimina identificadores y numeración local de ramales. |
| `linea` | `STRING` | No | Línea autorizada en ese ámbito. |
| `ramal` | `BIGINT` | No | Código de ramal de `LISTA_DEFECTOS.LINEA` / `LISTA_RECH.LINEA`, sin confundirlo con la línea completa ni con IDs de catálogos excluidos. |
| `zona` | `STRING` | No | Inicialmente `ZF`. Evita aplicar esa vigencia a medidas de otro punto físico. |
| `id_produccion` | `STRING` | No | Referencia al catálogo aceptado del mismo ámbito. |
| `vigente_desde` | `TIMESTAMP` | No | Comienzo efectivo o convencional expresamente validado de la asociación en la zona. |
| `vigente_hasta` | `TIMESTAMP` | Sí | Final aceptado; nulo solo cuando el episodio continúe sin final confirmado. |
| `evidencia_origen` | `STRING` | No | Referencia estable a la decisión/evidencias persistentes que justifican producción, ramal y límites. No es solo un mensaje de consola o una ruta a un CSV temporal. |

Conservar después los metadatos técnicos necesarios de contrato, versión de regla, lote/operación y hash. La resolución física de evidencias múltiples se apoyará en control/auditoría o estructura explícita; el nombre de este campo no exige guardar todos los payloads duplicados en cada fila de asociación.

Definir el ámbito de exclusividad `G_R = (planta, origen_fenix, linea, ramal, zona)`. Para cada `G_R`, los episodios aceptados deben cumplir:

- `id_asignacion` único y referencia a una producción existente y compatible con planta/origen/línea.
- `vigente_hasta > vigente_desde` cuando exista final; fechas tipadas con la política temporal de planta. Una observación AR de duración cero no implica una asociación de ramal de duración cero.
- Periodos `[vigente_desde, vigente_hasta)`; un episodio sin final se interpreta abierto al comprobar solapamientos, no como ausencia de restricción.
- Ningún solapamiento entre dos asociaciones aceptadas del mismo ramal, aunque las producciones tengan el mismo código de botella. Dos episodios pueden encontrarse exactamente en el límite sin solaparse.
- Varios ramales pueden apuntar simultáneamente a la misma producción. No imponer exclusividad por modelo ni por producción en toda la línea.
- Los huecos entre episodios son válidos; no exigir continuidad ni rellenarlos con una asignación ficticia.

Dos periodos se solapan cuando el inicio de cada uno es anterior al final del otro, interpretando un final nulo como abierto. Comprobarlo dentro del lote y frente al estado aceptado, incluyendo las correcciones y cierres propuestos en la misma unidad de publicación. No comprobar únicamente la igualdad de fechas o la duplicidad de clave.

La identidad debe distinguir sucesivos episodios del mismo ramal/producción si llegaran a existir. No generar IDs de nuevo con la hora de ejecución, el orden del CSV, un número aleatorio en cada intento ni un hash de fechas que puedan corregirse. Fijar una referencia canónica de episodio o correspondencia persistente al aprobarlo y reutilizarla en reintentos y revisiones. La elección técnica concreta se cerrará antes de escribir datos.

### 17.3. Obtención de la relación y evidencias modelo–molde–cavidad

`LISTA_DEFECTOS` y `LISTA_RECH` conservan ramal y fecha, pero no incluyen modelo de botella ni una referencia MP9 directa. `COD_MAQ_ZF` identifica una máquina de inspección en su contexto; no se convierte por coincidencia numérica en el identificador de fabricación de MP9.

`LISTA_AR` sí contiene modelo de botella, molde y cavidad. Cruzar sus evidencias con las listas MIA permite proponer qué modelos circulan por determinados ramales, y resolverlos contra las fabricaciones del catálogo. Aplicar este proceso con las siguientes garantías:

1. Trabajar con fuentes Silver aceptadas, sus snapshots y los ámbitos de planta/origen; conservar claves de filas y procedencia de los cruces.
2. Resolver el modelo de AR mediante el catálogo/diccionario y comprobar su compatibilidad temporal con la fabricación candidata. No confundir `MOLDE` con el modelo de botella ni interpretar `LISTA_AR.STATUS` como zona.
3. Comparar molde/cavidad en un contexto temporal compatible, con evidencia de la zona y del ramal. Un molde o cavidad puede reutilizarse: no convertir una coincidencia observada hoy en un diccionario global válido para todos los años.
4. No utilizar cavidad vacía, marcadores como `99Z` o molde 0 como prueba discriminante de fabricación. Esas observaciones se conservan; una relación producción–ramal ya validada permitirá asignarlas después sin exigirles una cavidad identificable.
5. Generar una asociación candidata con su intervalo de cobertura, evidencias y posibles contradicciones. Si la evidencia aún no justifica la producción o los límites, mantenerla pendiente de validación, sin publicarla como relación aceptada.
6. Aceptar automáticamente únicamente los casos cubiertos por una regla explícita y probada; en los demás, registrar una decisión revisada y su responsable/motivo. La cantidad de coincidencias o la última llegada no resuelven por sí solas una contradicción.

Los AR son observaciones esporádicas, no una relación completa de todos los modelos y ramales en cada instante. La ausencia de un aviso no demuestra ausencia de producción. Una primera coincidencia confirma presencia en su contexto, pero no fija por sí sola el primer instante de circulación ni toda la vigencia anterior o futura. Una evidencia posterior puede ayudar a revisar el pasado, con decisión y alcance registrados; no aplicar información futura silenciosamente a todo el histórico.

Cuando exista una asociación aceptada, la asignación MIA se resolverá por origen, ramal y periodo de la muestra, sin reconstruir el cruce molde/cavidad para cada fila. Eso permite tratar registros con molde 0 o cavidad vacía. Un cambio de modelo detectado exige revisar la vigencia correspondiente: no prolongar indefinidamente una asociación antigua ni trasladarla a todos los cambios futuros por semejanza.

### 17.4. Evidencia de la investigación de asignación en Aiala

Se revisaron íntegramente los archivos de los días 13 y 14 de octubre de 2026 de `AV21`, `AV22`, `AV23`, `AV31` y `AV32`: cuatro familias por origen y fecha, cuarenta CSV, bajo `C:/Users/iker.villen/Desktop/AI_tool/Datos/1.App_septiembre/Fenix_Aiala/Fenix`. Se contrastaron con las producciones de 16.5.1, sin modificar los originales.

| Familia | Filas examinadas |
| --- | --- |
| `LISTA_DEFECTOS` | 33937 |
| `LISTA_RECH` | 202931 |
| `LISTA_AR` | 177 |
| `CONT_ZC` | 827 |
| Total | 237872 |

Estos son conteos de filas de origen, no cantidades de botellas ni minutos-cavidad. No se encontraron repeticiones exactas de filas dentro de estas familias entre los dos archivos examinados de cada origen; ese resultado no elimina los controles de duplicados/conflictos de los contratos ni prueba ausencia de revisiones futuras.

| Origen | Filas defectos / rechazos MIA | Resultado por fecha de ZF | Filas AR y resultado por modelo/fecha | Filas CONT_ZC |
| --- | --- | --- | --- | --- |
| AV21 | 5440 / 43173 | Una fabricación candidata por fila | 50; una candidata | 170; una candidata temporal ZC |
| AV22 | 5928 / 34801 | Una fabricación candidata por fila | 53; una candidata | 169; una candidata temporal ZC |
| AV23 | 6780 / 37908 | Una fabricación candidata por fila | 34; una candidata | 148; una candidata temporal ZC |
| AV31 | 6678 / 37208 | Dos fabricaciones candidatas simultáneas | 20; dos candidatas incluso con modelo | 170; dos candidatas temporales ZC |
| AV32 | 9111 / 49841 | Dos fabricaciones candidatas simultáneas | 20; una candidata por modelo/fecha | 170; dos candidatas temporales ZC |

El recuento de candidatas se hizo utilizando las fechas de registro y los periodos declarados MP9, no demuestra por sí solo circulación física continua ni resuelve toda muestra de transición. La regla final trabaja con el periodo completo y las asociaciones aceptadas de este apartado.

**AV32: correspondencia observada mediante modelo, molde y cavidad.**

| Modelo | Raíz ZC candidata | Ramales con coincidencias observadas | Cambio declarado en ZF |
| --- | --- | --- | --- |
| 2168/242 | 2005 | 3–4 | Continúa sin cierre registrado en la muestra |
| 2001/477 | 2003 | 1–2 | Sale el 14/10/2026 a las 07:23:14 |
| 2169/477 | 2007 | 1–2 | Entrada declarada el 14/10/2026 a las 07:23:14 |

La evidencia respalda un cambio en 1–2 mientras 3–4 continúan con otro modelo. Las dos asignaciones del grupo se representarán como una fila por ramal para comprobar exclusividad y consultar las listas sin expandir grupos ambiguos. La hora MP9 de las 07:23:14 no acredita que la primera botella nueva llegase efectivamente a inspección en ese segundo; aceptar sus límites efectivos requiere las comprobaciones de 17.5–17.6. La candidata asociada a raíz 2007 mantiene además su dependencia del conflicto de `ZF_OK` de 4.5.

**AV31: interpretación física resuelta el 19/10/2026; incorporación al ejecutable completada en 17.16 y reconstrucción real pendiente.** La confirmación del usuario y los catálogos físicos vinculan 1617–1618 con máquina 8/AV31B/ramales 1–2, y 1643–1644 con máquina 7/AV31A/ramales 3–4. Ambas fabrican 1358/242 en la muestra. El análisis v1 basado solo en modelo/origen las mantenía ambiguas porque omitía ese vínculo. No es necesario inventar un cierre o fusionar fabricaciones. Esto resuelve la identidad física y las candidatas MIA, no la atribución automática de todos los AR: un AR sin discriminante físico suficiente sigue pendiente. Evidencia, reglas y migración en 17.14.

### 17.5. Tiempo declarado, tiempo efectivo y preparación

Separar tres referencias temporales y conservar su procedencia:

| Referencia | Significado | Tratamiento |
| --- | --- | --- |
| Fechas MP9 | Entrada/salida declaradas por Fenix en cada zona | Se conservan en la tabla fuente y justifican el catálogo de 16. No sobrescribirlas para ajustar una asociación. |
| Vigencia producción–ramal | Presencia efectiva o convencional validada de una fabricación por un ramal ZF | Se registra en `asignacion_produccion_ramal` con evidencia y regla. Puede diferir de MP9. |
| Recepción/proceso | Momento en que se recibe el archivo y se ejecuta la ingesta | Control y auditoría. No sustituye a ninguna fecha de negocio. |

El usuario confirma que los cambios no son instantáneos: puede haber preparación de máquinas sin fabricación entre la salida del modelo antiguo y la llegada del nuevo. Una producción como entidad de negocio puede incluir preparación o paradas; sus límites declarados no equivalen a flujo continuo de botellas.

Ejemplo de negocio, **hipotético en sus horas efectivas y no una medición demostrada del cambio real de AV32**:

| Periodo efectivo en un ramal | Situación |
| --- | --- |
| Hasta 07:23 | Circula el modelo antiguo |
| De 07:23 a 08:00 | Preparación sin circulación de ninguna de las dos producciones |
| Desde 08:00 | Comienza a circular el modelo nuevo |

La antigua asociación terminaría a las 07:23 y la nueva comenzaría a las 08:00, dejando un hueco válido. No prolongar la antigua hasta las 08:00 ni adelantar la nueva para cubrir la preparación. No crear una producción ficticia o una asociación con producción nula para ocultar el hueco. El motivo y su evidencia pueden conservarse en control operativo; no exige otra tabla Delta de negocio en esta fase.

Un hueco confirmado de preparación no es lo mismo que falta de datos o falta de una asociación. La ausencia de filas CSV, de AR o de inspecciones no demuestra por sí sola preparación, parada, teórico cero ni cierre de producción. Conservar y reportar esas diferencias. Un episodio sin cierre tampoco prueba circulación indefinida: una evidencia de cambio o preparación incompatible debe revisarse antes de seguir atribuyendo datos nuevos a la producción anterior.

Si los cruces muestran presencia nueva posterior a la fecha MP9, conservar las evidencias y proponer la revisión de vigencia con motivo y alcance. La primera observación del nuevo modelo solo limita cuándo fue observado; no prueba el instante físico exacto del arranque dentro de una muestra. No desplazar fechas de origen ni retroceder el comienzo efectivo a una hora supuesta.

### 17.6. Semántica confirmada de las muestras de diez minutos

**Acuerdo de origen:** la fecha de registro marca el final del periodo contabilizado. Una fila marcada a las 07:30 representa lo sucedido entre las 07:20 y las 07:30. Adoptar como convención de consulta el periodo `[T - 10 minutos, T)`, una vez interpretado `T` según la política temporal del origen. La recepción del archivo puede ocurrir después y no cambia el periodo.

- Conservar `FECHA` original en Silver. Calcular inicio y final de muestra en la preparación derivada, sin añadir o reordenar columnas de las tablas fuente.
- Un registro de las 00:00 puede contener hechos del día anterior. No asignar por el prefijo diario del archivo ni recortar la muestra a medianoche.
- Si falta una muestra, no extender la siguiente desde el último timestamp recibido, no crear ceros ni suponer que acumula el hueco. El intervalo de diez minutos se deriva del contrato de origen, no de la distancia entre filas disponibles.
- Una primera fila del modelo nuevo marcada a las 08:00 corresponde a 07:50–08:00; no acredita primera botella física a las 08:00. Si se conoce por otra evidencia que el comienzo físico fue a las 08:00, la muestra completa posterior sería la que termina a las 08:10.
- Interpretar y restar periodos sobre instantes con la política de zona horaria/cambio de hora validada; no resolver una hora local ambigua mediante la configuración del servidor. La resolución temporal física definitiva sigue siendo un requisito previo para datos productivos.

Esta semántica de intervalos se aplica a las muestras periódicas confirmadas, no a los tramos de `LISTA_AR`, que conservan sus propios `FECHA_ON`/`FECHA_OFF`, ni a las fechas de cambios MP9.

### 17.7. Asignación de MIA y tratamiento de muestras de transición

Para cada fila aceptada de `LISTA_DEFECTOS` o `LISTA_RECH`, localizar las asociaciones del mismo ámbito/ramal/zona ZF que intersectan el periodo de muestra. Evaluar las vigencias aceptadas, la cobertura y las evidencias del cambio. **No buscar únicamente la producción vigente en la hora final del registro.**

| Situación comprobada del periodo | Decisión de asignación |
| --- | --- |
| Periodo completo dentro de una asociación aceptada | Asociar a esa producción. |
| Botellas antiguas seguidas de preparación sin botellas nuevas | Asociar la muestra completa a la saliente, con motivo de transición registrado. No dividir por minutos. |
| Preparación seguida de primeras botellas nuevas, sin botellas antiguas en esa muestra | Asociar la muestra completa a la entrante, con motivo y evidencia. |
| Periodo íntegro de preparación confirmada, sin producción | No adjudicarlo automáticamente a ninguna fabricación; conservar observaciones de origen y motivo de no asignación. |
| Dos modelos con cantidades posiblemente mezcladas, evidencias incompatibles o relación insuficiente | Mantener pendiente/revisión, sin escoger por hora final, última llegada o mayoría. |

El criterio de atribución a la saliente recoge la explicación operativa del usuario para la muestra del cambio. Se trata de una regla expresa de asignación de la muestra completa, no de una prueba de trazabilidad botella por botella. No habilita a asignar a la antigua todas las muestras de una hora de preparación ni a ignorar evidencia de botellas nuevas dentro de la misma muestra. Cuando solo exista la fecha administrativa MP9 y no se pueda distinguir la situación, conservar la candidata y su incertidumbre hasta aplicar una regla/decisión aprobada; no presentar una asignación dudosa como inequívoca.

Para el ejemplo hipotético de 17.5, si las únicas botellas de 07:20–07:30 son antiguas, la muestra de las 07:30 pertenece a la antigua aunque la referencia de cambio sea 07:23. Los periodos íntegramente de preparación siguientes no pertenecen automáticamente a la nueva. El primer periodo con botellas nuevas y sin antiguas corresponde a la nueva. No inventar un reparto 3/10 frente a 7/10 a partir de los minutos del cambio.

Conservar una fila física fuente por su clave acordada y una única asignación exclusiva MIA aceptada por versión de derivación. Una revisión puede cambiar esa referencia, con auditoría, pero no debe dejar dos versiones activas que dupliquen cantidades. Los rechazos y las bases inspeccionadas mantienen sus reglas independientes de deduplicación/agregación: asignar a producción no convierte todas las filas de defectos en nuevas bases inspeccionadas.

Los resultados de asignación distinguirán al menos: asociación aceptada, transición atribuida por regla, preparación confirmada sin producción, falta de cobertura/vínculo y conflicto. Son estados de preparación/control, no cambios silenciosos a los campos originales. Reportar filas y cobertura por motivo, sin descartar evidencias ni contar sus cantidades en otra producción para ocultar pendientes. Conciliar el total de filas fuente con asignadas y no asignadas, separando los contadores técnicos de las métricas de botellas.

### 17.8. Avisos/rechazos y rendimiento de máquina compartido

**`LISTA_AR`:** utilizar modelo, contexto físico y periodo propio del tramo para resolver candidatas. En AV21/AV22/AV23 y AV32 se obtiene una candidata por modelo/fecha en la muestra examinada; en AV31 siguen existiendo dos candidatas del mismo modelo. No aplicar a AR una ventana artificial de diez minutos ni duplicar un tramo entre producciones por ambigüedad. Si cruza límites de fabricación, conservar el original y dejar pendiente su atribución y cómputo de minutos-cavidad hasta definir esa regla. Un AR es evidencia útil, pero no un registro obligatorio de toda botella o de toda vigencia.

**`CONT_ZC`:** mantener la regla compartida de 16.7. La relación producción–ramal ZF no determina la circulación en la rueda ZC ni sustituye a la correspondencia máquina–producciones. Validar los periodos efectivos de esa correspondencia con el mismo cuidado, sin crear otra tabla física por defecto. La métrica se calcula una sola vez por máquina/periodo y se asocia a las producciones coincidentes que compartan rueda, con alcance explícito de máquina.

No aplicar la exclusividad MIA de una producción por observación al porcentaje compartido tándem. Tampoco extender esa excepción para duplicar defectos, rechazos o AR. En una consulta conjunta, consolidar las observaciones físicas únicas de máquina/periodo antes de sumar rueda y teóricos; no sumar las copias de la asociación. En una transición, usar presencia efectiva ZC y la regla de la muestra, sin repartir artificialmente el contador entre modelos ni adjudicar producción durante una preparación confirmada sin flujo.

### 17.9. Escritura, revisión histórica y recuperación

La tabla temporal derivada y las salidas de consulta aplicarán las garantías de las secciones 5, 8 y 9, además de sus restricciones propias:

1. Preparar propuestas desde snapshots fuente identificados, con composición de evidencias, ámbito, periodos y versión de regla. Una candidata no aceptada se conserva en revisión; no aparece como una segunda asociación activa.
2. Antes de escribir, validar claves, referencias, límites y no solapamiento contra el estado que quedará tras toda la operación propuesta, no contra fragmentos incompatibles de lote.
3. Publicar inserciones, cierres o correcciones aprobadas mediante identidades estables e intención/confirmación persistentes. Distinguir un nuevo episodio de la corrección de los límites de uno existente.
4. Comparar contenido normalizado. Una repetición sin cambio no genera commit de negocio ni modifica `last_seen`; sí registra el resultado operativo. La desaparición de una evidencia de un volcado posterior no borra ni cierra automáticamente una asociación.
5. Auditar los valores anteriores/nuevos, fechas efectivas, momento en que conocimos la información, regla/decisión, responsable si hubo revisión, fuentes/snapshots y operación. El historial técnico de Delta no sustituye esa evidencia permanente.
6. Resolver resultados inciertos de commit antes de repetir. No duplicar episodios, casos de revisión, asignaciones activas o contadores de auditoría por reintentos.
7. Al aprobar una corrección de vigencia, determinar qué observaciones y ventanas pueden cambiar. Incluir muestras que intersecten los límites anteriores o nuevos, no solo las fechas máximas; recomputar el ámbito afectado con regla versionada y conservar trazabilidad de la asignación anterior.
8. La publicación del catálogo, relación temporal y salidas no es una transacción global automática. Usar el descriptor de snapshots y recuperación de la sección 8; mantener la última publicación coherente hasta que la siguiente esté validada.

Las unidades independientes pueden seguir procesándose mientras hay referencias o revisiones pendientes. No convertir la aclaración de AV31, el despliegue remoto o una evidencia tardía en un bloqueo general de la ingesta de origen. Tampoco ocultar pendientes de una unidad requerida declarándola completa.

### 17.10. Caminos de consulta, explicabilidad y latencia

Se utilizarán dos caminos complementarios:

| Uso | Recorrido |
| --- | --- |
| Resolver, verificar o explicar una asignación | Clave y fila de origen → ámbito/ramal/periodo → asociación aceptada y evidencias → producción del catálogo. |
| Consulta habitual de la aplicación | Modelo → producciones disponibles → producción seleccionada → observaciones/agregados preparados con `id_produccion`. |

Preparar las asociaciones aceptadas antes de servir las consultas habituales evita repetir el cruce temporal completo en cada petición. Una salida podrá contener `id_produccion`, `id_asignacion`/referencia de decisión y la clave fuente, con los campos de consulta necesarios. Es una materialización derivada reproducible, no otra fuente de verdad ni una alteración de los nueve contratos Silver de origen.

Las explicaciones deben poder responder: qué producción recibió el registro, qué ramal y periodo se evaluaron, qué evidencia y regla se aplicaron y si fue asociación directa o transición convencional. Las correcciones de esa decisión deben ser reconstruibles después de expirar una versión técnica Delta o un CSV original conforme a su retención.

El esquema relacional no garantiza una latencia mínima absoluta. Definir y medir objetivos de latencia —incluido p95—, concurrencia, volumen histórico y frescura con el motor de consulta elegido. Probar búsqueda por modelo sin fechas, detalle de una producción antigua, comparación con otra actual y últimas ocho horas de varias líneas. Medir preparación y lectura por separado. Seleccionar filtros, organización física, compactación, cachés y materializaciones según resultados; no presuponer índices relacionales clásicos, particionar cada producción o reescribir todas las capas para resolver un problema todavía no medido.

La preparación de datos se ejecuta con la ingesta/derivación autorizada. Una petición de lectura de la app no crea tablas, corrige asociaciones ni modifica Silver, conforme al aislamiento del plan.

### 17.11. Criterios de aceptación de asociación y tiempo

- [ ] La revisión 31 conserva once fuentes —nueve existentes y dos catálogos físicos autorizados— y dos entidades derivadas, sin reincorporar el resto de fuentes excluidas; referencias/versiones coherentes.
- [ ] Un ramal con dos asociaciones aceptadas solapadas se detecta antes de publicar, incluidos episodios abiertos y correcciones propuestas; producciones iguales por código no evitan el conflicto.
- [ ] Una producción puede ocupar varios ramales y un ramal cambiar de producción, conservando sus episodios históricos e identidad tras correcciones auditadas.
- [ ] Una muestra con molde 0/cavidad vacía se asigna mediante una relación ya aceptada; esos valores no se usan para inventar un cruce discriminante.
- [ ] La evidencia de AV32 respalda 2168/242 en 3–4 y el cambio 2001/477 → 2169/477 en 1–2, sin afirmar que la fecha administrativa sea la llegada física de la primera botella nueva.
- [ ] AV31 distingue máquina 8/AV31B/raíz 1617/ramales 1–2 de máquina 7/AV31A/raíz 1643/ramales 3–4; no fusiona por modelo, conserva sus vínculos y no duplica AR/MIA. Un AR sin discriminante físico sigue pendiente.
- [ ] La fila marcada a las 07:30 se interpreta como 07:20–07:30; el prefijo del archivo, recepción, medianoche y huecos de muestreo no cambian ese periodo.
- [ ] El caso hipotético antigua hasta 07:23 / preparación hasta 08:00 / nueva desde 08:00 deja un hueco de asociación, sin prolongaciones, adelantos o producciones ficticias.
- [ ] Una muestra de antiguas más preparación se atribuye a la saliente y una de nuevas más preparación a la entrante, con motivo y sin prorrateo. Una posible mezcla real o evidencia incompatible permanece en revisión.
- [ ] Un periodo íntegro de preparación confirmada se distingue de falta de cobertura, ausencia de asociación y cero recibido; ninguna de esas situaciones se transforma en datos sintéticos.
- [ ] Un primer registro a las 08:00 no fija primera botella a esa hora. MP9, vigencia efectiva y recepción permanecen separados y trazables.
- [ ] Filas fuente = asignadas + no asignadas por motivo, sin multiplicar bases inspeccionadas, rechazos ni episodios. Los tramos AR originales se mantienen íntegros.
- [ ] Una rueda tándem mantiene observación física única y porcentaje compartido con sus producciones efectivas; las consultas conjuntas no duplican cantidades y no utilizan la vigencia ZF como si fuese ZC.
- [ ] Una corrección retroactiva reevalúa muestras en los límites, mantiene auditoría y publicación coherente y se recupera tras un fallo sin duplicar efectos.
- [ ] Las consultas habituales usan referencias preparadas y los recorridos de explicación reproducen la decisión. Los objetivos de latencia se verifican mediante medidas y no se declaran cumplidos por diseño.

### 17.12. Siguiente paso y entregas pequeñas de implementación

La implementación ha comenzado por autorización del usuario en `C:/Users/iker.villen/Desktop/AI_tool/Visualizador/Ingesta_Delta_Lake`, como proyecto independiente. Este plan conserva las decisiones de arquitectura y los contratos completos; la ejecución se divide en el [índice de entregas](../../Ingesta_Delta_Lake/docs/ENTREGAS.md) y sus doce fichas. No mantener dos copias divergentes del plan completo. Las fichas enlazan el contrato correspondiente y acotan alcance, dependencias, aceptación y estado.

La primera entrega se valida en un entorno local separado. No hace falta esperar a SSH/Docker o a la aclaración de AV31 para implementar unidades independientes; la viabilidad del runtime/almacenamiento remoto sigue siendo obligatoria antes del despliegue productivo. La protección de rutas, auditoría e idempotencia forman parte de esta primera implementación.

| Entrega | Alcance | Evidencia de terminación |
| --- | --- | --- |
| [01. Base y SIP_MODELO](../../Ingesta_Delta_Lake/docs/entregas/01_base_sip_modelo.md) | Configuración, entradas estables, Bronze, contrato de modelos, control transaccional y escritor Delta; workflow CI desde el inicio. Implementación inicial local completada. | Pruebas sintéticas, conflictos, no escritura sin cambios y recuperación; muestra real con 2309 asociaciones y replay idéntico. CI remota/productivo pendientes. |
| [02. SIP_DEFECTO](../../Ingesta_Delta_Lake/docs/entregas/02_sip_defecto.md) | ID/códigos/descripciones, exclusión de MEDIDA y NO USAR. Implementación local completada. | 378 entradas válidas/86 excluidas verificadas; replay sin commits, exclusiones auditadas y recuperación probada. |
| [03. SIP_DEFECTO_STATUS](../../Ingesta_Delta_Lake/docs/entregas/03_sip_defecto_status.md) | Procedencias, ORIGEN/ZONA originales y exclusión de ACTIVO. Implementación local completada. | Ocho entradas verificadas, replay sin commits, etiquetas preservadas, conflictos y recuperación probados. |
| [04. SIP_DEFECTO_AR](../../Ingesta_Delta_Lake/docs/entregas/04_sip_defecto_ar.md) | Tipos de AR y agrupación original. Implementación local completada. | Nueve entradas verificadas, relaciones por CODIGO, conflictos auditados, recuperación y replay sin commits probados. |
| [05. MP9_CAMBIO_MODELO](../../Ingesta_Delta_Lake/docs/entregas/05_mp9_cambio_modelo.md) | Siete campos, cierres y relaciones ZF–ZC. Implementación local completada; publicación parcial real explícita. | 48 ID revisados, 46 publicados, dos cierres; AV32/2007 en revisión y 2008 pendiente. Recuperación y replay sin commits probados. |
| [06. Catálogo](../../Ingesta_Delta_Lake/docs/entregas/06_catalogo_producciones.md) | Entidad derivada y búsqueda por modelo. Implementación local completada. | 24 candidatas/23 publicadas/una pendiente, identidad estable, cierre ZF validado, búsquedas sin fechas y recuperación entre tablas probados. |
| [07. LISTA_DEFECTOS](../../Ingesta_Delta_Lake/docs/entregas/07_lista_defectos.md) | 18 columnas, clave con cavidad, base de inspección compartida y revisión de contadores. Implementación local completada. | 33.937 filas y 23.419 bases conciliadas, rechazos/canales originales, conflictos persistentes y reintentos/replay sin duplicados. Sin conexión al score. |
| [08. LISTA_RECH](../../Ingesta_Delta_Lake/docs/entregas/08_lista_rech.md) | Nueve columnas y fuente del denominador MIA preferente. Implementación local completada. | 202.931 filas conciliadas, contadores/fechas intactos, revisiones/recuperación probadas y replay sin commits. Integración del score y optimización del control pendientes. |
| [09. LISTA_AR](../../Ingesta_Delta_Lake/docs/entregas/09_lista_ar.md) | Doce columnas, tramos y cierres propios. Implementación local completada. | 177 tramos conciliados, agrupaciones repetidas/nulas conservadas, referencias por código, dependencias/cierres y recuperación probados. Minutos-cavidad pendientes. |
| [10. CONT_ZC](../../Ingesta_Delta_Lake/docs/entregas/10_cont_zc.md) | 17 columnas, decimales exactos y fórmula de rueda/teóricos. Implementación local completada. | 827 observaciones conciliadas, 135 puntos >100 % preservados/señalados, elegibilidad/ceros, revisión, recuperación y replay sin commits probados. |
| [11. Asociación temporal](../../Ingesta_Delta_Lake/docs/entregas/11_asociacion_temporal.md) | Implementación local: vigencias revisadas, propuestas, preparación, muestras completas y atribución auditable. | 237.872 observaciones conciliadas y 16 candidatas reales; límites efectivos pendientes de revisión. Publicación/correcciones/no solapamiento/recuperación y ZC compartida sin doble conteo probados con casos sintéticos. AV31 y conflicto AV32 conservados. |
| [12. Consumo y operación](../../Ingesta_Delta_Lake/docs/entregas/12_consumo_operacion.md) | Salidas para app, rendimiento, releases y validación/despliegue remoto. Pendiente. | Comparación histórica, consultas explicables, publicación coherente y despliegue sin mutar datos por instalar. |

Las entregas no constituyen un único PR grande ni autorizan refactorizar la app completa. Antes de escribir código, localizar las instrucciones del repositorio/unidad de ingesta y concretar su ubicación y dependencias independientes; no instalar paquetes en el entorno activo de la aplicación ni guardar datos Delta dentro de un checkout. Decidir las versiones compatibles mediante prueba del motor y fijarlas en el entorno/lockfile de la nueva unidad, sin imponerlas desde esta edición documental.

Preparar pruebas unitarias de contratos y pruebas de integración reales con Delta en directorios temporales aislados, incluyendo reintentos, conflictos y fallos alrededor del commit. Los casos de CI serán sintéticos o muestras expresamente autorizadas para ese canal; disponer de datos locales reales no autoriza subirlos a GitHub. La muestra Aiala se usará para conciliación local de solo lectura cuando corresponda.

Las entregas 01 a 11 usan un entorno externo al checkout y destinos `test`/`development` explícitos; rechazan producción, UNC/SMB y solapamientos de rutas. Transforman las nueve fuentes y derivan catálogo/asociaciones desde Silver aceptado; la integración de consumo permanece pendiente. Los diccionarios bloquean su unidad ante conflictos/inválidos. MP9, MIA, AR y ZC permiten unidades válidas independientes y comunican cobertura parcial mientras haya revisiones/dependencias; catálogo/asociaciones mantienen esa distinción. Las intenciones y evidencias se conservan para reconciliar antes de reintentar. No hay escrituras vacías, overwrite ni borrado por ausencia. Los controles se separan por contrato y comparten bloqueo Silver, conservando las ubicaciones anteriores. El MERGE de asociación admite exclusivamente correcciones explícitas versionadas, sin convertir esta capacidad en una corrección automática de fuentes.

La conciliación local del 16/10/2026 leyó 316333 filas de diez archivos reales, consolidó 2309 asociaciones y no detectó incidencias. El segundo procesamiento conservó la versión Delta 0 y los mismos archivos, con cero inserciones; dos intentos y veinte recepciones quedaron registrados, con 2309 intenciones confirmadas una sola vez. Las huellas de los diez CSV originales no cambiaron. Los datos/copias/control de ensayo permanecen fuera del proyecto, sin publicarlos ni incorporarlos a las muestras de CI.

La entrega 02 amplió la suite a 114 pruebas superadas en Windows, con cobertura de ramas/líneas del 93,97 %, manteniendo las 62 pruebas originales y añadiendo recuperación, convivencia y compatibilidad de controles anteriores. Su conciliación real procesa 44080 filas de revisiones únicas por origen/hash: 35910 válidas y 8170 excluidas, consolidadas en 378 entradas y 86 exclusiones distintas, sin incidencias. Reprocesarlas no cambia los archivos ni la versión Delta 0 y las huellas de los originales permanecen intactas.

La entrega 03 amplió la suite a 159 pruebas superadas, con cobertura del 94,70 %, manteniendo las 114 anteriores. Los diez CSV reales de procedencias contienen 760 filas en revisiones únicas por origen/hash: ocho entradas y 752 repeticiones consolidadas, sin inválidos ni conflictos. El replay conserva huellas, versión Delta 0 y primera evidencia, con ocho coincidencias y cero inserciones. Se auditan dos intentos, veinte recepciones y ocho intenciones confirmadas.

La entrega 04 amplió la suite a 196 pruebas superadas, con cobertura del 95,25 %, manteniendo las 159 anteriores. Los diez CSV reales de tipos de AR contienen 855 filas en revisiones únicas por origen/hash: nueve entradas y 846 repeticiones consolidadas, sin inválidos ni conflictos. El replay conserva versión Delta 0 y huellas de todos sus archivos, con nueve coincidencias y cero inserciones. Se auditan dos intentos, veinte recepciones y nueve intenciones confirmadas.

La entrega 05 amplía la suite a 242 pruebas superadas, con cobertura del 94,71 %, manteniendo las 196 anteriores. Ruff y formato pasan. Se ensaya el día 13 seguido del 14 mediante copias controladas de los diez MP9 reales y el diccionario de modelos aceptado: 46 registros en versión 0 y dos cierres válidos en versión 1, con 48 identidades revisadas. AV32/2003 se cierra a las 06:23:00 y AV32/2004 a las 07:23:14 del 14/10/2026. AV32/2007 no se publica por ZF_OK contradictorio; AV32/2008 queda pendiente de su ZC. El replay no escribe y conserva versión y huellas. Hay tres intentos, 25 recepciones y 46 INSERT/dos CLOSE confirmados una sola vez. Los veinte originales —modelos y MP9— conservan sus huellas; informes y datos quedan fuera del checkout.

La configuración de MP9/catálogo, LISTA_DEFECTOS, LISTA_RECH, LISTA_AR y CONT_ZC exige `local_naive_test_v1`: timestamps locales sin zona, almacenados como timestamp_ntz para ensayo, sin asumir UTC ni resolver DST implícitamente. La política productiva sigue pendiente. Las dependencias MP9 y AR conservan candidata y Bronze para reintento aunque cambie el CSV, con edad y eventos de resolución/escalado. El umbral de escalado es opcional/configurable; 24 h en el ejemplo es un valor de ensayo, no una decisión productiva. Los conflictos no se resuelven por omisión de alternativas y aún no existe un comando de corrección humana.

La entrega 06 amplía la suite a 268 pruebas superadas y cobertura del 93,67 %, manteniendo las 242 anteriores; Ruff y formato pasan. En la conciliación Aiala, el día 13 produce 23 fabricaciones —16 cerradas/siete sin cierre—. Tras actualizar MP9 con el día 14 hay 24 candidatas, 23 publicadas —17 cerradas/seis sin cierre— y una pendiente por AV32/2007. AV32/2003 conserva identidad y completa el final a las 07:23:14; las dos fabricaciones AV31/1617 y 1643 de 1358/242 siguen separadas. La búsqueda detecta el desfase entre actualización fuente y derivación, sin escribir. Replay sin commits adicionales y veinte CSV originales intactos; derivar/consultar no altera MP9. Informe y datos fuera del checkout. El workflow existente no se modifica ni ejecuta en GitHub; se continúa solo con código/pruebas locales, sin integración en la interfaz. GitHub y despliegue quedan para después.

La entrega 07 amplía la suite a 307 pruebas superadas y cobertura del 93,03 %, manteniendo las 268 anteriores; Ruff y formato pasan. Los diez LISTA_DEFECTOS de los días 13 y 14 de Aiala contienen 33.937 filas, comparadas campo por campo con el snapshot Delta. Se publican sus 18 campos originales más origen técnico, con cero incidencias en esta muestra. Se verifican 23.419 contextos de inspección con una base compatible: 2.462.774 inspecciones por contexto, frente a la suma directa incorrecta de 7.473.317 sobre filas de defectos. Estas cifras no representan botellas físicas únicas entre máquinas. El replay conserva la versión 0 y los hashes de todos los archivos Delta, con cero inserciones; veinte recepciones y 33.937 intenciones confirmadas quedan auditadas y los diez originales intactos. Informe y datos de ensayo fuera del checkout, en la ruta recogida en la ficha 07.

En LISTA_DEFECTOS, `observation_intents` fija clave completa, firma, grupo, candidata, evidencia y hash antes del append. `issues` conserva las contradicciones semánticas y `issue_evidence` las recepciones/lotes relacionados; una revisión no se resuelve por omisión ni permite sobrescribir datos aceptados. `inspection_groups` mantiene evidencia de las bases por lote confirmado, sin crear otra tabla Delta. La lectura `inspection-bases` comprueba control/versión y ausencia de operaciones técnicas pendientes; no modifica datos. Un grupo afectado por revisión no aparece como base resuelta; errores sin contexto identificable afectan la cobertura del origen. La integridad comunicada es global, incluso al filtrar el diagnóstico por origen. Las unidades válidas independientes pueden publicarse como parciales. No se ha implementado todavía resolución humana de correcciones ni optimización de escaneo para históricos masivos.

La revisión de compatibilidad del backend es de solo lectura: LISTA_RECH sigue siendo el denominador preferido y no se activa el fallback sobre defectos. La integración y comparación de tasas/score se harán en las entregas de consumo; los datos de origen aún no reciben ID de producción.

La entrega 08 incorpora LISTA_RECH con nueve campos originales y origen técnico. Conserva ceros, rechazos automáticos superiores a inspeccionadas y FECHA_R nula aunque RECH_AUTO sea positivo. No impone relaciones de subconjunto entre contadores ni rellena fechas. Cualquier diferencia en RECH, RECH_AUTO o FECHA_R para una clave aceptada requiere revisión; otra cantidad INSPECC para la misma firma no crea una segunda versión activa. Una ambigüedad inicial mantiene retenido su contexto aunque luego se omita una alternativa. Las intenciones, incidencias, recepción/evidencias y bases se auditan con el protocolo MIA reutilizado mediante adaptadores, con tabla y control propios y sin modificar identidades de operaciones anteriores de LISTA_DEFECTOS.

La consulta diagnóstica `inspection-bases` admite configuración LISTA_RECH y exige un snapshot técnicamente confirmado. Su suma por contextos no sustituye al denominador agregado del score ni representa botellas físicas únicas entre máquinas. La regla histórica filtra y toma máximo por timestamp/ramal de máquina antes de sumar periodos; nunca se aplica ese máximo para resolver revisiones contradictorias de origen. La verificación del núcleo numérico sobre origen/fecha/ramal y filtros disponibles en CSV no acredita aún los enriquecimientos, asignaciones, filtros de modelo/color/sección o tasas/score de producción. Esas comprobaciones siguen siendo condición previa a conectar Delta a la app.

La suite de la entrega 08 alcanza 347 pruebas superadas y cobertura del 93,12 %, conservando las 307 anteriores; Ruff y formato pasan. La conciliación independiente de los diez CSV reales verifica sus 202.931 filas, incluidos los 17.831 registros de AV23/día 14, con igualdad de los nueve campos y origen frente a Silver. Cero conflictos/revisiones en esta muestra. Se conservan 84 filas con RECH_AUTO positivo y FECHA_R nula y el caso de 5 inspeccionadas/0 rechazos/13 automáticos. El replay mantiene versión 0, archivos y hashes Delta, con cero inserciones; veinte recepciones y 202.931 intenciones confirmadas quedan auditadas. Los diez CSV y el LISTA_DEFECTOS usado para contrastar las cinco filas/base 96 permanecen intactos. El núcleo numérico de máximo por origen/fecha/ramal se contrasta en seis escenarios con filtros disponibles, sin afirmar integración del score o equivalencia de enriquecimientos. Informe en la ruta externa de la ficha 08.

La carga real más amplia ha hecho visible el coste de mantener JSON preparados e intenciones con evidencia repetida por fila: 794.202.112 bytes de control frente a 523.303 bytes Silver tras la prueba. Primera carga 330,17 s con suite paralela y replay 50,96 s en este equipo; no es un benchmark productivo. Antes de producción se necesita una entrega acotada de optimización: referencias normalizadas a revisiones/evidencias, comparación incremental sin escaneos completos innecesarios y medición de memoria, latencia por ciclo y crecimiento del control, preservando compatibilidad y recuperación de operaciones anteriores. No borrar auditorías o intenciones pendientes para reducir tamaño. La integridad local validada no equivale a capacidad acreditada para el histórico creciente.

La entrega 09 implementa LISTA_AR en una Delta y control propios, conservando los doce campos originales seguidos de origen técnico. La identidad incluye todos sus atributos salvo FECHA_OFF; agrupación, agrupación/STATUS o contigüidad temporal no identifican por sí solos una fila. El MERGE compara explícitamente cavidad/agrupación nulas e inserta tramos nuevos o completa exclusivamente un cierre único nulo→fecha compatible. Un cerrado no se reabre; dos cierres no nulos distintos quedan en revisión persistente. No se resuelve una ambigüedad por omitir una alternativa, ni se alteran otras columnas para corregir identidades automáticamente.

AR lee por código los snapshots confirmados de SIP_DEFECTO, SIP_DEFECTO_STATUS y SIP_DEFECTO_AR de la misma planta/raíces, sin lanzar sus ingestas. Se conservan los códigos originales y las etiquetas permanecen en sus diccionarios; STATUS es procedencia, no zona ni estado de apertura. No se exige SIP_MODELO para conservar el MODELO textual ni se reincorporan catálogos excluidos. Un código ausente genera candidata/evidencia Bronze persistente para reintento aunque ya no figure en el CSV vivo. Una revisión abierta en un diccionario retiene conservadoramente la interpretación de esa familia; un commit técnico pendiente obliga a recuperar el diccionario. También se reevalúan referencias de aceptados ausentes del CSV actual: permanecen conservados y se informa cobertura parcial si dejan de poder interpretarse con garantías.

`ar_intents` fija INSERT/CLOSE, clave completa, antes/después, hashes y evidencia antes de escribir, con confirmación de operación/versión posterior. `issues`/`issue_evidence` separan caso semántico de recepciones y `dependency_events` audita observación/escalado/resolución. El lote fija Bronze y versiones/hashes/controles/calidad de las tres referencias; una operación preparada se recupera contra esas versiones antes de leer novedades, aunque las fuentes hayan avanzado. Deben conservarse los snapshots fijados. No se presupone atomicidad entre tablas/SQLite ni se repite una operación incierta sin reconciliarla. La carga no calcula minutos-cavidad, fusiona intervalos, recorta fechas a turnos o producciones, ni equipara avisos/rechazos con paradas.

La conciliación Aiala de AR compara independientemente los doce campos/origen de diez CSV con 177 identidades y 177 tramos cerrados; cero conflictos o referencias pendientes en esta muestra. Se conservan AV21/39954 con sus dos procedencias, AV32/19607 con dos tramos STATUS=2 y cinco filas AV23/día 13 sin agrupación. Los tres diccionarios contienen 378/ocho/nueve entradas y resuelven todos los códigos. Replay sin inserciones/cierres/commits adicionales, versión 0 y hashes intactos; veinte recepciones y 177 intenciones confirmadas. Los cuarenta originales AR/diccionarios permanecen intactos y consumir referencias no altera sus Delta ni añade intentos a sus controles. Aperturas/cierres incrementales y contradicciones se validan con casos sintéticos, dado que los 177 tramos reales ya tienen cierre. Informe y datos fuera del checkout en la ruta recogida en la ficha 09.

La suite de la entrega 09 alcanza 395 pruebas superadas y cobertura del 92,85 %, manteniendo las 347 anteriores y añadiendo 48 casos de AR; Ruff y formato pasan. Incluye cierres únicos con nulos, publicación mixta INSERT/CLOSE, no reapertura, conflictos persistentes, referencias por código frente a ID diferentes, recuperación de dependencias aunque desaparezca el CSV y recuperación técnica contra versiones de diccionario anteriores a sus nuevas llegadas. La prueba de familia desconocida se actualiza a un nombre inexistente y comprueba el motivo de rechazo, porque LISTA_AR ya está implementada. Sin cambios de dependencias/lockfile ni integración en el entorno de la app.

La entrega 10 completa CONT_ZC con 17 campos originales y origen técnico. Todos son obligatorios; los contadores son int64 no negativos, las velocidades Arrow decimal128(12,2)/Delta DECIMAL(12,2) exactas y el indicador SALIDA_MAQ_IS se conserva como 0/1. La clave origen/MAQ_IS/FECHA reconoce duplicados completos; cambios de cualquier cantidad, velocidad o indicador quedan íntegros en revisión sin sumar, sobrescribir o crear otra versión activa. Se valida punto decimal, precisión/escala y rango sin float ni redondeo; representaciones equivalentes como 80.010 y 80.01 coinciden. Los campos no utilizados y los ceros se conservan. No se calculan diferencias entre contadores ni se exige que crezcan con el tiempo.

`measurement_intents` registra por identidad la intención, candidata/evidencia, hash y confirmación de versión; `issues`/`issue_evidence` separan casos de recepciones. No se generan commits sin novedades. Un fallo se reconcilia contra operación, snapshot y contenido antes de repetir, incluso si cambia el CSV vivo. Las unidades independientes pueden publicarse como parciales, sin declarar completos los casos requeridos pendientes. La tabla/control son propios; no se han modificado los CSV ni entornos/código de la app.

La consulta de solo lectura `zc-yield` exige control/snapshot confirmados. Admite filtros de origen, máquina e inicio/final locales ISO sin zona; selecciona finales de muestra en `(start, end]` y declara periodo nominal de diez minutos. Informa primer/último final, observaciones resueltas, elegibles, excluidas y resumen por máquina. La cobertura es solo de muestras observadas: no garantiza continuidad o ciclos esperados y no interpola, rellena huecos con ceros o extiende una muestra por una ausencia. `source_complete` se refiere a incidencias conocidas de la familia y sigue siendo global al filtrar un origen sano; no equivale a cobertura completa de una producción.

El rendimiento diagnóstico es 100×SUM(RUEDA)/SUM(TEORICO_A+TEORICO_B) sobre el mismo conjunto resuelto con teórico positivo. Se conservan teóricos cero, excluyendo su rueda y teóricos de ambos lados del cociente, con cobertura explícita; sin denominador elegible, porcentaje nulo/NOT_EVALUABLE. Se devuelven sumas enteras, fracción del porcentaje y presentación decimal a cuatro cifras con HALF_EVEN, sin guardar porcentajes en la tabla fuente ni conectar el score. No se promedian porcentajes, cambia rueda por expulsor/A+B según indicador, reparte rueda entre producciones o recorta a 100 %. Se señalan observaciones/ventanas superiores al 100 % conservando sus contadores. El agregador rechaza observaciones físicas duplicadas. Una revisión conocida excluye de la métrica su aceptado afectado; un error sin clave identificable afecta al origen, manteniendo el estado parcial.

La conciliación de diez CONT_ZC reales de Aiala compara independientemente los 17 campos/origen de 827 observaciones con Delta y verifica DECIMAL(12,2) físico; cero incidencias de ingesta en esta muestra. El punto AV23/día 14 conserva rueda 1583, teóricos 1614+0, expulsor 1613 e indicador 1, con 98,0793 % para ese periodo. Se conservan y señalan 135 puntos reales superiores al 100 %: AV21 4, AV22 39, AV23 39, AV31 11 y AV32 42. Los cinco agregados por origen coinciden con las sumas y cocientes calculados independientemente. No hay teóricos cero en esta muestra; su elegibilidad se verifica con casos sintéticos. Replay sin inserciones o commits, versión 0 y hashes Delta intactos; dos intentos, veinte recepciones y 827 intenciones confirmadas. Los diez originales conservan sus hashes. Informe/datos en la ruta externa recogida en la ficha 10.

La entrega 10 amplía la suite a 455 pruebas superadas y cobertura del 92,81 %, manteniendo las 395 anteriores y añadiendo 60 casos de CONT_ZC; Ruff y formato pasan. Incluye precisión/escala y equivalencia decimal, fórmula ponderada y conjunto elegible, teóricos cero sin porcentaje inventado, valores >100 % no recortados, identidad/conflictos persistentes, fallos alrededor del commit/acuse y consulta de solo lectura por finales de muestra. Sin cambios de dependencias/lockfile o entorno de la app; validación local Windows con Python 3.12.14, deltalake 1.6.6 y Arrow 23.0.1, sin ejecución de CI remota.

Las nueve fuentes están implementadas en ensayo, junto con el catálogo derivado. La entrega 11 se documenta a continuación. Integración/cálculo de consumo, validación de score, política horaria productiva y optimización de control/escaneo siguen pendientes; completar contratos de origen no acredita operación productiva ni despliegue.

### 17.13. Entrega 11: implementación de asociación temporal y validación local

El contrato `asignacion_produccion_ramal_v1` implementa los diez campos de 17.2, en su orden, seguidos de `version_regla` STRING y `revision_decision` BIGINT. Los campos de texto son STRING; ramal/revisión BIGINT; fechas TIMESTAMP_NTZ con política explícita de ensayo; únicamente `vigente_hasta` admite nulo. La identidad es `fenix-association-v1:<planta>:<origen>:<id de episodio revisado>`, independiente de fechas, ejecución y orden de CSV. La tabla solo contiene episodios ZF aceptados, sin crear una Delta vacía al no haber ninguno. Se validan referencias, ámbito, intervalos semiabiertos y no solapamiento sobre todo el resultado propuesto antes del MERGE.

Se derivan candidatas cruzando MIA y AR por origen/molde/cavidad y coincidencia temporal, resolviendo modelo/fabricación con catálogo válido. Se conservan candidatos múltiples y ausencia de fabricación publicable. Molde 0, cavidad nula y 99Z no discriminan; una relación revisada sí permite asignar esas observaciones. La zona AR se interpreta por la descripción aceptada de TIPO terminada en ZC/ZF; otros tipos permanecen pendientes, sin convertir STATUS en zona. Revisiones de diccionarios AR retienen su interpretación. Primer/último final de muestra observado son extremos de evidencia, no límites efectivos ni prueba de continuidad. El resumen ofrece hasta ocho cruces por candidata, pero archiva el snapshot completo. El contador de pares de evidencia no es número de botellas ni filas físicas.

Las vigencias se declaran mediante JSON operativo opcional dentro de `control_root`, con ID estable, clase, origen, ramal o máquina según zona, producción cuando corresponda, fechas, revisión, actor, motivo y referencias verificables o evidencia operativa externa declarada. La ficha 11 documenta el formato. No se fabrican decisiones reales para completar la muestra. Actor es metadato declarado de auditoría, no una autenticación/firma corporativa. Se admiten asociación/preparación ZF y presencia/preparación ZC revisadas; preparación requiere final finito y no producción. Las presencias ZC se conservan en control persistente sin crear otra tabla maestra ni usar fechas ZF como si fueran ZC.

La regla `temporal_assignment_v1` evalúa cada muestra MIA/ZC completa `[T−10min,T)`. Distingue asociación directa, saliente+preparación, preparación+entrante, preparación completa, cobertura pendiente, mezcla y evidencia incompatible, sin prorratear ni duplicar observaciones. Flujo positivo en preparación completa es contradicción explícita. AR mantiene sus periodos originales, sin forzarlos a diez minutos ni dividirlos: abiertos, cruces de límites, zona desconocida o fabricación ambigua permanecen pendientes; no se calculan minutos-cavidad. CONT_ZC permite varias presencias ZC coincidentes para compartir el porcentaje máquina/periodo. Una consulta conjunta deduplica las claves físicas antes de sumar rueda/teóricos y mantiene la fórmula/elegibilidad de la entrega 10.

El control específico conserva intenciones por episodio, versiones antes/después, hashes, revisiones y publicaciones. Artefactos JSON comprimidos e inmutables guardan snapshots aceptados completos, composición, decisiones, propuestas y un registro de atribución por clave física con producción(es), episodios, regla y motivo; hashes de contenido/archivo se verifican antes de recuperar o leer. Los controles fuente conservan procedencia a Bronze/archivo/fila. Estos artefactos de auditoría no añaden una décima fuente Fenix ni modifican los nueve contratos de origen. Permiten reconstruir una publicación sin depender de la permanencia del CSV vivo o de una versión técnica Delta.

Una corrección exige mismo ID/ámbito/tipo, revisión consecutiva y hash de la decisión aceptada anterior, con actor/motivo/evidencia. Conserva antes/después y reevalúa atribuciones. Omitir decisiones no elimina, revoca o cierra episodios ni resuelve contradicciones. Un ámbito pendiente retiene sus atribuciones, manteniendo el dato aceptado y su historial; otros ámbitos avanzan. La resolución explícita se audita con su publicación. Intención/commit/acuse se recuperan antes de otro cálculo sin repetir efectos y sin fingir atomicidad SQLite/Delta. Un reintento verifica el snapshot congelado aunque cambie el CSV vivo.

La derivación exige snapshots confirmados y catálogo acorde a sus dependencias actuales bajo el bloqueo común Silver. `associations build` es la operación explícita; `associations inspect` solo lee la última publicación confirmada, explica por clave física o selecciona producciones y detecta cambios de fuentes/decisiones/mapeos y operaciones pendientes como STALE. No reconstruye desde una lectura ni mezcla una versión Delta nueva pendiente con el registro de atribuciones anterior. Todavía no constituye el camino optimizado de consulta de la app. En esta versión se recalcula todo el conjunto aceptado ante una revisión; recomputación incremental, escaneo/control eficiente, latencias y concurrencia se reservan para otra entrega.

La validación del 16/10/2026 reconstruyó las nueve fuentes y catálogo en ensayo externo. Noventa CSV de cinco orígenes y dos días conservaron sus hashes. Se conciliaron 237.872 observaciones físicas distintas: 33.937 defectos, 202.931 rechazos, 177 AR y 827 ZC, con igualdad fuente=atribuidas+pendientes. Se obtuvieron 16 candidatas: seis en AV21/22/23; cuatro ambiguas de AV31 entre raíces 1617/1643; seis de AV32, reproduciendo 2168/242 en 3–4 y 2001/477→2169/477 en 1–2. Las dos candidatas de 2169/477 siguen sin fabricación publicable por AV32/2007–2008. No se aprobaron límites de circulación a partir de esos cruces.

Sin decisiones reales de vigencia, se aceptaron cero episodios ZF y todas las filas MIA/ZC conservan cobertura pendiente. AR permite 118 atribuciones y mantiene 59 pendientes: 20 ambiguas, una sin producción publicable y 38 sin zona interpretable por la regla actual. No son pérdidas de datos ni errores de conteo de botellas. Publicación de episodios aceptados, transiciones, correcciones y ZC compartida se prueban con datos sintéticos e integración Delta. Replay sin inserciones/actualizaciones, artefactos idénticos y todas las tablas Silver fuente intactas. Evidencia externa al checkout en `LOCALAPPDATA/Ingesta_Delta_Lake/validation/20261016T124135Z-associations-25d4fc06/`, con `foundations.json`, `association-validation.json` y `association-candidates.json`.

Por petición expresa del usuario se termina aquí esta jornada: quedan para el siguiente día las decisiones reales pendientes, integración de la app/score, minutos-cavidad, optimización, horario productivo, GitHub y despliegue. La entrega 12 no se inicia automáticamente.

### 17.14. Revisión contractual del 19/10/2026: identidad física de tándem

**Estado: contrato aprobado; código implementado y probado con datos sintéticos en revisión 33; reconstrucción/migración real pendientes.** Este apartado prevalece sobre las exclusiones de ID_MAQUINA/SIP_MAQUINA_MOLDEO/SIP_LINEA_ZF de revisiones anteriores. Los resultados v1 y las decisiones históricas fechadas se conservan como evidencia del alcance entonces probado. La publicación real v2 no se produce al editar el plan o instalar el código. Detalle de implementación/aceptación en 17.16.

#### 17.14.1. Evidencia y resolución del caso AV31

El usuario confirma que 1358/242 permanece en AV31B desde el 03/07/2026 a las 06:00 y aún no tiene final confirmado. Se contrastaron los catálogos físicos de ambas fechas de la instantánea Aiala y todos sus registros de producción/ramal, incluyendo las fuentes anteriormente excluidas. La cadena verificable es:

`MP9_CAMBIO_MODELO.ID_MAQUINA` → `SIP_MAQUINA_MOLDEO` → denominación física. Para MIA: `(origen, código de ramal, periodo)` → `SIP_LINEA_ZF` vigente → ID_MAQUINA → registro ZF y raíz ZC de esa máquina/modelo/periodo.

| Origen | ID_MAQUINA | Denominación | Raíz ZC / registro ZF | Modelo | Inicio ZC | Inicio ZF | Ramales / ID internos ZF |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AV31 | 8 | AV31B | 1617 / 1618 | 1358/242 | 03/07/2026 06:00:00 | 03/07/2026 08:47:43 | 1 / 13 y 2 / 93 |
| AV31 | 7 | AV31A | 1643 / 1644 | 1358/242 | 24/09/2026 06:00:00 | 24/09/2026 07:36:40 | 3 / 14 y 4 / 15 |
| AV32 | 9 | AV32A | 2005 / 2006 | 2168/242 | 08/10/2026 07:20:00 | 08/10/2026 08:02:50 | 3 / 17 y 4 / 18 |
| AV32 | 10 | AV32B | 2003 / 2004 | 2001/477 | 28/09/2026 06:04:00 | 28/09/2026 07:29:44 | 1 / 16 y 2 / 256 |
| AV32 | 10 | AV32B | 2007 / 2008 | 2169/477 | 14/10/2026 06:23:00 | 14/10/2026 07:23:14 | 1 / 16 y 2 / 256 |

Los archivos de referencia están bajo `C:/Users/iker.villen/Desktop/AI_tool/Datos/1.App_septiembre/Fenix_Aiala/Fenix/<origen>/`, prefijos 20261013/20261014. En AV31/día 14: SIP_MAQUINA_MOLDEO filas 1–2; SIP_LINEA_ZF filas 1–4; MP9_CAMBIO_MODELO filas 11, 13, 14 y 16. MP9_MOD_LINEA_ZF filas 21–22, 25–26 y 31–34 corroboran modelo/máquina/ramales/fechas. Ese uso diagnóstico no lo convierte en otra fuente obligatoria.

En ambas instantáneas los catálogos físicos coinciden. La comprobación de cinco orígenes resuelve las máquinas de las 48 identidades MP9 sin contradicciones de ID_MAQUINA. Los 96 ID de MP9_MOD_LINEA_ZF y sus 102 variantes tienen correspondencia única con versiones MP9 por máquina/modelo/zona/fechas/indicador, y relaciones compatibles con los catálogos ZF. Es diagnóstico sobre originales, no una migración ni una aprobación de ZF_OK. Para AV31, las 37.208 filas LISTA_RECH y 6.678 LISTA_DEFECTOS tienen una sola candidata al incorporar máquina/ramal y comprobar el periodo completo declarado ZF. No equivale a aprobar automáticamente toda frontera física futura.

Las fechas fin de la pantalla adjunta corresponden, en los ejemplos contrastados, a ZC. El cierre ZC de AV31A mostrado el 19/10 no existía todavía en la copia del día 14; no se escribe una fecha extraída de una fotografía en Silver. En AV32/2001/477 se mantienen cierre ZC 14/10 06:23:00 y final global ZF 07:23:14. La fabricación de AV31B no se cierra por las entradas posteriores de AV31A.

#### 17.14.2. Dos catálogos mínimos y su contrato

Los CSV carecen de cabecera. Los nombres siguientes son **nombres normalizados acordados para los campos seleccionados**, no una supuesta cabecera original. Se conserva su orden relativo y se documenta la posición de origen; los campos restantes continúan en la copia Bronze, sin participar en el hash de negocio seleccionado.

Catálogo `SIP_MAQUINA_MOLDEO`, contrato objetivo `sip_maquina_moldeo_v1`, destino lógico `silver/<planta>/sip_maquina_moldeo`:

| Orden seleccionado | Posición CSV | Campo normalizado | Tipo Delta / Arrow | Nulo |
| --- | --- | --- | --- | --- |
| 1 | 1 | id_maquina_fenix | BIGINT / int64 | No |
| 2 | 2 | nombre_maquina | STRING / string | No |
| 3 | 8 | vigente_desde | TIMESTAMP_NTZ / timestamp(us) | No |
| 4 | 9 | vigente_hasta | TIMESTAMP_NTZ / timestamp(us) | Sí |
| 5 | 10 | codigo_linea_zc | BIGINT / int64 | No |

Identidad inicial `(planta, id_maquina_fenix, vigente_desde)`, tras verificar asociaciones entre copias de orígenes de planta; procedencias múltiples quedan en auditoría sin duplicar el catálogo. Un ID no puede tener dos denominaciones/líneas incompatibles con vigencias solapadas. No interpretar los otros números del CSV como IDs equivalentes, estado de marcha o modelo de botella. La denominación identifica la parte física; no se deduce por sufijos ni se obtiene del MODELO.

Relación `SIP_LINEA_ZF`, contrato objetivo `sip_linea_zf_v1`, destino lógico `silver/<planta>/sip_linea_zf`:

| Orden seleccionado | Posición CSV | Campo normalizado | Tipo Delta / Arrow | Nulo |
| --- | --- | --- | --- | --- |
| 1 | 1 | id_linea_zf_fenix | BIGINT / int64 | No |
| 2 | 2 | codigo_ramal | BIGINT / int64 | No |
| 3 | 5 | id_maquina_fenix | BIGINT / int64 | No |
| 4 | 6 | vigente_desde | TIMESTAMP_NTZ / timestamp(us) | No |
| 5 | 7 | vigente_hasta | TIMESTAMP_NTZ / timestamp(us) | Sí |

Origen_fenix se conserva como metadato técnico utilizable al consultar/escribir. Identidad `(planta, origen_fenix, id_linea_zf_fenix, vigente_desde)`; exclusividad de máquina por `(planta, origen_fenix, codigo_ramal, periodo)` y control de variantes incompatibles del mismo ID interno. `codigo_ramal=2` no es `id_linea_zf_fenix=93`: las listas MIA usan el primero. No confundir ninguno con COD_MAQ_ZF, ID_MAQUINA de moldeo o MAQ_IS de contadores. La máquina referenciada debe existir en la planta y ser compatible con el origen/línea y la vigencia del vínculo.

Reglas comunes: parsear enteros sin float; textos obligatorios no vacíos; fechas declaradas día/mes/año, con hora cuando venga informada, y día sin hora a 00:00 bajo la política de ensayo explícita. No asignar UTC ni resolver DST silenciosamente. La fecha 1900 de configuración se conserva como dato declarado, nunca como inicio de fabricación. Final nulo abierto; final informado posterior al inicio; vigencias semiabiertas. La configuración de hoy no se aplica retrospectivamente a periodos no respaldados por su vigencia/evidencia.

Duplicados completos dentro/entre archivos y orígenes compatibles: coincidencia sin escritura. Nuevo ID o nueva vigencia demostrada: insertar tras validar referencias, ámbitos y no solapamiento. Cierre único nulo→fecha compatible: completar misma identidad con antes/después e intención/confirmación. Copia abierta posterior no reabre. Cambio de máquina, código, nombre, línea o inicio para una identidad existente, cierres distintos o vigencias incompatibles: conservar aceptado y candidatas íntegras en revisión; no tomar última llegada. Un cambio de relación debe aportar vigencias compatibles o decisión explícita trazable; no fecharlo por recepción ni rellenar huecos.

Ausencia en un volcado no borra ni cierra. Un catálogo parcial por origen no es un inventario completo de planta. Falta de referencia genera dependencia persistente, reevaluada tras actualizar el catálogo, incluso si desaparece del CSV vivo. Clave, contenido seleccionado, fuente/fila/snapshot, contrato, hashes, antes/después, reintentos y estados se auditan fuera del código. El lote parcial no declara resueltos los ámbitos afectados. Sin cambios válidos no se invoca al escritor Delta ni se actualiza last_seen de negocio.

#### 17.14.3. Catálogo y asignación con máquina física

Mantener `id_produccion=fenix-production-v1:<planta>:<origen>:<ID_ZC>` y las claves MP9 existentes. El catálogo objetivo v2 añade ID_MAQUINA BIGINT obligatorio antes de ID_ST1_REL, que permanece a la derecha. Se resuelve desde la raíz y se comprueba contra todos sus componentes ZF; no es una columna editable independiente. Origen AV31 y denominación AV31B son conceptos distintos: la etiqueta de la máquina se obtiene del catálogo físico sin cambiar las identidades o reinterpretar la carpeta como una única parte de tándem.

Para MIA, resolver el código de ramal mediante una relación ramal–máquina válida durante la muestra; después buscar fabricaciones de esa máquina y su presencia ZF. Validar snapshots, referencias, unicidad y periodo completo `[T−10min,T)`, sin decidir solo por T ni por el modelo común. Una relación que cambia dentro de la muestra requiere evaluar la transición; no elegir una mitad ni repartir contadores. Las fechas de topología y las de MP9 no prueban por sí solas el instante físico de las primeras botellas ni preparación sin flujo. Las reglas de 17.5–17.9 se mantienen. Las evidencias modelo–molde–cavidad corroboran la asociación y detectan contradicciones; no son el sustituto del vínculo físico disponible.

Esta comprobación es obligatoria para **toda máquina tándem presente o futura**, incluida AV32. Enumerar los orígenes y máquinas del catálogo, agrupar por su línea ZC declarada e identificar sus partes por ID; no codificar una lista fija AV31/AV32 ni deducir que una letra A/B implica ciertos ramales. Resolver cada código de ramal mediante su registro vigente, comprobar exclusividad, referencias y compatibilidad con la máquina MP9, y repetir las comprobaciones con cada revisión estable nueva. Una tándem/topología nueva o modificada se valida por las mismas reglas; referencias ausentes o relaciones incompatibles quedan pendientes con evidencia, sin reutilizar la configuración de otra máquina.

Las dos fabricaciones 1358/242 permanecen separadas por sus raíces y máquinas, sin exigir un parón global entre ellas. Los cambios de una parte no cierran la otra. No extrapolar la topología observada a otras plantas o a configuraciones futuras sin validación. CONT_ZC.MAQ_IS=31 no se equipara con ID_MAQUINA=7/8: la rueda compartida y sus presencias efectivas ZC mantienen su contrato independiente. AR sin ramal/máquina de moldeo discriminante no se fuerza a una raíz por modelo coincidente; requiere cruce físico/temporal verificado o revisión, conservando íntegros los tramos y sin duplicar minutos-cavidad.

#### 17.14.4. Implementación y migración por incrementos

1. **11A, MP9 v2:** parser/esquema de ocho campos, hash v2 y validación de máquina como invariante y en vínculos ZF–ZC. Recuperar ID_MAQUINA de las copias Bronze/originales verificadas, por identidad completa; comprobar todas las versiones. Nunca rellenar a partir del modelo, raíz, orden o nombre de carpeta. Si las evidencias de una identidad discrepan, conservarla en revisión.
2. **11B, catálogos físicos:** capturar/normalizar ambas fuentes y aplicar sus claves, cierres, vigencias, referencias y control de conflictos/idempotencia/recuperación. Publicar en un destino de ensayo independiente y conciliar códigos/IDs sin asumir equivalencias numéricas.
3. **11C, derivados v2:** incorporar máquina al catálogo y resolución física a asociaciones, preservando IDs de producción/episodio, auditoría y restricciones temporales. Revalidar candidatas MIA reales y los casos de AR/ZC que tengan evidencia suficiente. Conciliar y explicar los pendientes restantes, incluido ZF_OK de AV32.

Se trata de cambios versionados incompatibles con el hash/esquema MP9 v1; no habilitar evolución automática, mutar controles/bindings ni permitir al nuevo lector adoptar una tabla antigua como v2. Reconstruir en raíces/control de ensayo separadas a partir de evidencia estable, conservando contenidos v1, cierres, IDs y todas sus incidencias. Probar coincidencia de las siete columnas originales, añadir únicamente la máquina verificada y documentar diferencias legítimas; no eludir por esta migración el caso ZF_OK 0/1 de 2007. Una decisión autorizada sobre ese indicador será otro cambio explícito.

Antes de una promoción operativa, reconciliar pendientes del escritor anterior, validar las dependencias/derivados v2 y cambiar el descriptor de release/snapshots de forma controlada. No mezclar MP9 v1/v2, catálogos ausentes o derivados de versiones incompatibles; una publicación anterior puede conservarse pero debe declarar su contrato/frescura. Instalación/actualización del código no ejecutan esta migración ni modifican Delta. No borrar los controles previos ni reutilizar IDs de operación de v1 para escrituras v2. Probar recuperación antes/después del commit y del acuse, replay sin escrituras y conservación de originales. La ampliación se incorporará a GitHub/CI antes del piloto remoto siguiendo la secuencia ya acordada: validación local → GitHub/CI → piloto de ingesta en servidor → validación continua e integración de app.

#### 17.14.5. Producción abierta frente a fabricación en marcha

No se ha identificado en las fuentes seleccionadas un flag fiable que signifique «esta producción está fabricando ahora». La identidad ID/id_produccion identifica una entidad, no su actividad instantánea. Conservar MP9 original y derivar el estado en lectura/preparación; no inventar otra columna booleana Fenix ni escribir en Silver cada vez que se consulte.

**Regla operativa acordada por el usuario el 19/10/2026:** una producción conocida en una línea o parte de tándem continúa **abierta mientras no se reciba un cierre válido**. No se exige un indicador de actividad adicional, consulta SQL en tiempo real ni un plazo máximo de duración. La ausencia de novedades, una pausa de contadores, el paso del tiempo o un cambio en la otra parte de la tándem no la cierran. El estado abierto no supone flujo continuo de botellas; las relaciones pendientes o contradictorias se señalan como calidad/cobertura separada y no provocan un cierre ficticio.

| Evidencia válida de la producción | Estado de negocio derivable | Límite |
| --- | --- | --- |
| Raíz ZC válida con FECHA_OFF nula y relaciones conocidas | ABIERTA_EN_ZC | No hay salida ZC informada. Puede existir parada o preparación sin flujo. |
| ZC cerrada y algún componente ZF conocido abierto, con vínculos válidos | PENDIENTE_CIERRE_ZF | La salida ZC no cierra globalmente la producción; no demuestra flujo actual en ZF. |
| Todos los componentes ZF correspondientes cerrados y composición completa/validada | CERRADA | Final global: última FECHA_OFF ZF; no fecha de cierre ZC. |
| Falta ZF/modelo/máquina/topología, contradicción de fuente o composición incierta | PENDIENTE_VALIDACION | Un final global nulo no se traduce a «en producción». Conservar motivo y cobertura. |

La actualidad de la fuente es un eje separado: informar fecha del último dato observado, recepción/comprobación y si existen revisiones/entregas pendientes. No interpretar una copia local antigua o una fuente atrasada como estado actual de planta. Los umbrales de frescura siguen pendientes de acuerdo operativo; no cerrar por timeout. Una repetición de un catálogo estático no es nueva evidencia de flujo.

La falta de señal adicional o el aviso de fuente atrasada no cambian ABIERTA a CERRADA ni obligan a confirmar manualmente cada continuidad. Una futura consulta SQL u otra señal operativa podrá reforzar la comprobación mediante un cambio versionado de contrato; **no es una dependencia de la solución actual ni un motivo para posponer la ingesta/piloto**. Conservar el estado abierto según la última información conocida e informar su fecha de referencia.

STATUS=1/2 distingue ZC/ZF; no significa ejecutándose/parada. ZF_OK conserva su significado de confirmación aún por aclarar y no sustituye cierres ni mide actividad. Para afirmar fabricación efectiva actual haría falta evidencia operativa reciente y atribuible con garantías a esa producción. Rueda positiva de CONT_ZC compartida por una tándem prueba flujo de la máquina, pero por sí sola no demuestra que ambas fabricaciones estén produciendo botellas en ese instante. No inferir un estado individual a partir de esa cantidad compartida ni del ACTIVO de un diccionario.

#### 17.14.6. Aceptación de la ampliación

- [ ] MP9 v2 conserva ocho campos en orden, máquina obligatoria y hash versionado; preserva IDs y cierres v1 al reconstruir desde evidencia.
- [ ] Catálogos mínimos tipados/vigentes, referencias auditables, repeticiones sin escritura, cierres únicos y contradicciones persistentes; sin bajas por ausencia.
- [ ] ZF no puede enlazar a ZC de otra máquina/modelo/ámbito. Un cambio de máquina para el mismo ID genera revisión sin sobrescribir ni duplicar.
- [ ] AV31B/8/1617–1618 se resuelve a ramales 1–2 y AV31A/7/1643–1644 a 3–4, manteniendo dos fabricaciones del mismo modelo y sus fechas independientes.
- [ ] Se verifican AV32A/9/2005–2006 en 3–4 y AV32B/10/2003–2004→2007–2008 en 1–2; la resolución física no da por resuelto ZF_OK ni la frontera de muestra.
- [ ] La misma validación descubre y comprueba todas las tándem de los catálogos vigentes sin listas o ramales fijados por nombre/letra, y se repite con cada revisión de sus fuentes.
- [ ] Periodos completos, cambios de topología, mezcla, preparación, nulos/códigos genéricos y AR/ZC insuficientemente discriminados no generan atribuciones ficticias o duplicados.
- [ ] Abierta, pendiente de cierre ZF, cerrada, pendiente de validación y frescura se distinguen; ningún flag de actividad instantánea se inventa.
- [ ] Una producción sin cierre válido continúa abierta sin señal extra/SQL, sin cierre por tiempo o por cambio en otra parte; calidad/frescura no se confunden con cierre.
- [ ] Migración aislada, comparación de campos originales/IDs, no adopción silenciosa de v1, publicación coherente, recuperación y replay probados antes de promoción.
- [ ] Las pruebas anteriores siguen pasando; los conteos nuevos, diferencias y pendientes se documentan por separado de las evidencias v1. No se declara implementado este contrato al actualizar únicamente el plan.

### 17.15. Comprobación general de las tándem de Aiala, 19/10/2026

**Alcance:** nueva lectura de los archivos disponibles, no nueva captura del servidor. Se enumeraron AV21, AV22, AV23, AV31 y AV32 y se contrastaron las dos fechas de sus catálogos físicos, MP9 y modelos; se comprobaron también ambas listas MIA de AV31/AV32. Se leyeron 48 archivos, cuyas huellas antes/después permanecieron idénticas. Los datos continúan siendo la instantánea del 13–14/10/2026; no se han inferido cierres posteriores ni escrito datos desde las capturas de pantalla del día 19.

Las tándem identificadas en esos orígenes son AV31 y AV32. Ambas tienen dos ID de máquina distintos para la misma línea ZC, con sus nombres declarados y relaciones ZF explícitas. En las 48 identidades MP9 revisadas no se detectaron cambios de ID_MAQUINA para un mismo ID ni referencias de máquina ausentes; todos los vínculos ZF–ZC resuelven igual máquina/modelo. Los catálogos coinciden entre los días 13 y 14. Este alcance no afirma inventario completo de cualquier máquina que no exporte datos a los orígenes disponibles.

| Origen | Parte | ID_MAQUINA | Código de ramales MIA | ID internos SIP_LINEA_ZF |
| --- | --- | --- | --- | --- |
| AV31 | AV31A | 7 | 3–4 | 14 y 15 |
| AV31 | AV31B | 8 | 1–2 | 13 y 93 |
| AV32 | AV32A | 9 | 3–4 | 17 y 18 |
| AV32 | AV32B | 10 | 1–2 | 16 y 256 |

Las fuentes de AV32 son `AV32/20261013_SIP_MAQUINA_MOLDEO.csv` y `AV32/20261014_SIP_MAQUINA_MOLDEO.csv`, filas 1–2, y los dos `SIP_LINEA_ZF.csv`, filas 1–4, bajo la raíz Aiala indicada en 17.14.1. La cadena resuelve ID 9→AV32A y 10→AV32B; códigos 1–2→10 y 3–4→9. No depende de inferir el significado de ZF_OK, de las letras de nombre ni de reincorporar MP9_MOD_LINEA_ZF como fuente obligatoria.

En la instantánea AV32A mantiene la fabricación 2168/242, raíz 2005/registro ZF 2006, iniciada en ZC el 08/10 a las 07:20 y sin cierre. AV32B registra la salida de 2001/477, 2003/2004: cierre ZC 14/10 06:23:00 y cierre global ZF 07:23:14. En esa misma parte entra 2169/477, 2007/2008, con entrada ZC 06:23:00 y ZF 07:23:14, sin cierre. Este último sigue pendiente de publicación en v1 por sus variantes ZF_OK=0/1; su identidad física está resuelta, pero esa comprobación no autoriza escoger una variante del indicador.

La prueba diagnóstica aplica primero catálogo vigente de ramal–máquina y después presencia ZF declarada MP9 al periodo completo `[T−10min,T)`, sin utilizar solo la hora final:

| Origen / familia | Filas físicas únicas | Una candidata para el periodo completo | Frontera sin asignación automática |
| --- | --- | --- | --- |
| AV31 / LISTA_RECH | 37.208 | 37.208 | 0 |
| AV31 / LISTA_DEFECTOS | 6.678 | 6.678 | 0 |
| AV32 / LISTA_RECH | 49.841 | 49.840 | 1 |
| AV32 / LISTA_DEFECTOS | 9.111 | 9.107 | 4 |

Las cinco filas de AV32 pertenecen al ramal 1, fecha 14/10/2026 07:30:00, periodo 07:20–07:30, que cruza la frontera declarada ZF de las 07:23:14. Son LISTA_RECH/día 14 fila 13604 y LISTA_DEFECTOS/día 14 filas 2445–2448. Todas repiten una base de 13 inspeccionadas; no se suman como 65 ni se declara que sean cinco bases diferentes. La fila de rechazos y las cuatro filas por defecto mantienen sus contadores originales. La identidad física ramal/máquina está resuelta; la muestra requiere la regla/evidencia de transición de 17.7 para elegir producción sin inventar reparto o preparación. Este diagnóstico precede a la confirmación operativa posterior del usuario: atribuir estas cinco filas íntegramente a la saliente, implementado y probado sintéticamente en 17.16.

Los conteos incluyen diagnóstico sobre los originales de 2007/2008, no escrituras de registros todavía bloqueados en Silver. No se han publicado asociaciones v2, migrado tablas, borrado incidencias o resuelto AR/ZC por esta comprobación. El ajuste de contrato exige implementar la validación general de tándem, no una excepción para AV31. ZF_OK permanece pendiente de la aclaración del proveedor sobre significado, actualizaciones y orden/revisión de entregas; no se establece precedencia 0→1 sin esa confirmación.

### 17.16. Ejecución de puntos 2/3: catálogos físicos y derivados v2

**Estado al 19/10/2026:** implementados en `Ingesta_Delta_Lake`, con pruebas automatizadas sintéticas. No se ha reconstruido la copia real, migrado/adoptado ninguna tabla v1 ni cambiado la aplicación. Esta entrega no configura GitHub/CI/CD o el servidor. Documentación operativa revisable en `Ingesta_Delta_Lake/docs/entregas/11bc_catalogos_derivados.md`.

#### 17.16.1. Catálogos físicos implementados

Se incorporan las dos fuentes mínimas de 17.14.2, hasta once familias Fenix implementadas, sin reincorporar otras tablas descartadas. Captura estable Bronze y descubrimiento mixto de nombres fijos/sobrescritos y versiones con fecha/hora reutilizan las garantías existentes. Las columnas originales no seleccionadas permanecen en Bronze; no forman parte del hash de negocio.

- `sip_maquina_moldeo_v1`: identidad por máquina/vigencia dentro de planta; verifica denominación, código de línea y vigencias no solapadas. Las copias equivalentes de distintos orígenes comparten registro y conservan procedencias en auditoría.
- `sip_linea_zf_v1`: identidad por origen/ID interno ZF/vigencia; distingue el ID del código de ramal usado por MIA. Comprueba exclusividad de máquina por origen/ramal/periodo y variantes incompatibles del mismo ID.
- Claves/códigos int64 sin conversión float, nombres obligatorios y fechas tipadas con la política de ensayo. Solo el final de vigencia admite nulo. Un vínculo requiere máquina aceptada que cubra la vigencia completa y corresponda al código de línea configurado.
- Coincidencia exacta: sin nueva fila ni commit Delta. Cierre único compatible: únicamente final nulo → fecha validada, con antes/después. Nueva vigencia compatible y no solapada: INSERT. Ausencia en un archivo: no baja, cierre o reapertura.
- Contradicción de contenido/cierre, solapamiento o valor inválido: evidencia íntegra persistente para revisión; no elegir por orden/última recepción. Que desaparezca una variante del archivo vivo no resuelve la incidencia.
- Máquina pendiente: conservar candidato y sus referencias Bronze, reintentar al incorporarse la dependencia; el CSV vivo puede haber desaparecido. Confirmar huellas al recuperarlo. Las incidencias afectan a las relaciones dependientes y no se ocultan al derivar.
- Intenciones duraderas por clave, hashes antes/después, operación, versión Delta y confirmación. Recuperación previa a nuevas escrituras, incluidos commit ya aplicado y acuse pendiente. No afirmar atomicidad global entre SQLite, ambos catálogos y los derivados.

#### 17.16.2. Máquina física, producciones y asociación

Prerrequisito implementado: `mp9_cambio_modelo_v2` conserva ocho campos de negocio en orden original relativo: `ID`, `ID_MODELO`, `FECHA_ON`, `FECHA_OFF`, `ID_MAQUINA`, `STATUS`, `ID_ST1_REL`, `ZF_OK`, seguidos del metadato `origen_fenix`. ID_MAQUINA es obligatorio e invariante; toda referencia ZF–ZC exige máquina, modelo y origen coincidentes. Permanecen excluidos ID_PREPARACION/N_MOD. No se altera la clave MP9 ni la interpretación de ZF_OK.

`catalogo_producciones_v2` contiene la máquina antes de ID_ST1_REL. Con topología activada contrasta catálogo físico/código de línea/vigencia de la raíz ZC; la consulta resuelve denominación y estado de negocio desde los snapshots, manteniendo aparte cobertura y frescura. Los identificadores `fenix-production-v1:...` permanecen estables; dos raíces del mismo modelo en máquinas distintas continúan siendo dos fabricaciones. La producción conocida sin cierre válido permanece abierta; no se exige SQL, otra señal o un timeout.

`asignacion_produccion_ramal_v2`, regla `temporal_assignment_v2`, genera episodios ZF por intersección de las vigencias declaradas de MP9, máquina y ramal. Aplica el mismo algoritmo a todas las máquinas configuradas, incluidas futuras tándem; no fija nombres A/B o números de ramal por origen. Los códigos de línea se declaran explícitamente y se verifican contra las fuentes, sin deducirlos de nombres.

El episodio automático tiene ID estable derivado de producción, registro ZF y claves de vigencia física. Un cierre tardío conserva ese ID y crea una revisión consecutiva con hash previo/evidencia; replay sin cambios no produce otra escritura Delta. Una corrección explícita del episodio requiere mismo ID/ámbito/tipo, revisión consecutiva, hash anterior, actor/motivo/evidencia y se conserva al volver a derivar. No se crea una asociación solapada para corregir otra. Actor sigue siendo metadato de auditoría, no autenticación.

**Convención de transición confirmada por el usuario:** la muestra `[07:20,07:30)` que cruza el cierre ZF de las 07:23:14 se atribuye íntegramente a la producción saliente. La regla general implementada requiere que la muestra comience cubierta por un único episodio saliente válido y cruce su final dentro del periodo, con topología física válida para toda la muestra. Si aparece el siguiente episodio, debe pertenecer a otra producción de la misma máquina, sin solapamiento, y cubrir el final de la muestra. No se exige que la entrante esté publicable cuando la saliente sí permite resolver esa transición; un ZF_OK pendiente de la entrante no invalida el cierre aceptado de la saliente. Se registra `TRANSITION_TO_OUTGOING`, producción/episodios, versión de regla y motivo en el registro de atribuciones.

Así se tratan la fila LISTA_RECH y las cuatro LISTA_DEFECTOS de AV32/ramal 1/14-10 07:30 señaladas en 17.15, sin prorratear, sumar las 13 inspeccionadas cinco veces ni alterar contadores fuente. Esta convención no prolonga la saliente a muestras posteriores, inventa preparación ni corrige fechas originales; una contradicción/cambio de topología que impida verificar el periodo permanece pendiente. Es una decisión operativa trazable, no una medición física de la primera botella entrante.

Los AR sin discriminante suficiente pueden seguir ambiguos aunque MIA se resuelva por ramal. CONT_ZC mantiene presencia ZC revisada y rendimiento compartido sin duplicar contadores físicos; los catálogos ZF no prueban presencia ZC ni fabricación efectiva de cada producción. Minutos-cavidad y cálculo/consumo de score no forman parte de esta entrega.

**ZF_OK permanece separado:** sus variantes contradictorias 0/1 por identidad quedan en revisión, sin preferir 1, último archivo o última fila. Se conservan las candidatas completas y no se publica una producción no validada. Las otras máquinas/producciones y las muestras válidas de transición pueden avanzar. La futura aclaración del proveedor dará lugar a otro cambio de contrato/pruebas explícito.

#### 17.16.3. Configuración, compatibilidad y siguiente validación

Los ejemplos de catálogos físicos y derivados incluyen `topology_enabled = true` y `[line_code_by_origin]` completo por origen; los derivados mantienen además `[line_by_origin]`. Cambios en estos mapeos invalidan la frescura de la publicación anterior. El modo explícito `topology_enabled = false` mantiene el flujo manual de ensayo, sin afirmar resolución física; catálogo y asociaciones deben usar modos coherentes. MP9/catálogo tienen contrato v2 en ambos modos.

El nuevo escritor/lector rechaza controles/esquemas v1 incompatibles; no ofrece una adopción o migración silenciosa. Instalación y actualización del código no ejecutan ingesta/reconstrucción. Para el ensayo posterior, configurar todas las familias con las mismas raíces nuevas y separadas de Bronze/Silver/control, conservando tablas y auditorías v1. Los ejemplos antiguos de otras familias necesitan adaptar esas rutas; no mezclar raíces de ejemplos distintos.

Orden explícito: diccionarios y máquinas → ramales/MP9 tras sus dependencias → catálogo y observaciones → construcción de asociaciones → consulta/conciliación. Cada fuente y derivado conserva su operación de publicación independiente y sus dependencias confirmadas. Los comandos existentes admiten las dos familias nuevas; ningún scheduler se inicia en esta entrega.

Verificación local: regresión completa de **525 pruebas superadas**, cobertura **90,95 %** con ramas y umbral 85 %. Tres pruebas adicionales pasan para el mismo modelo concurrente en ambas partes, cierre tardío con revisiones/ID estables y frescura tras cambios de configuración. Se comprueban también tipado, referencias, vigencias, conflictos persistentes, recuperación antes/después de commit/acuse, idempotencia, cinco filas de transición a saliente y conservación del conflicto ZF_OK. Son datos sintéticos en destinos temporales. No se recalculan ni sustituyen los conteos reales v1 de secciones anteriores.

Por instrucción del usuario: **código/pruebas locales → configuración de GitHub/CI → reconstrucción y conciliación real en entorno separado del servidor → piloto de llegadas reales → validación continua e integración con la app**. La reconstrucción real queda pendiente hasta disponer de ese entorno; la copia local no acredita llegadas sucesivas. Antes de promoción deben comprobarse igualdad de campos/identidades aceptadas, cierres, origen=atribuidas+pendientes, comportamiento de las tándem, exclusividad, replay, recuperación y explicación de diferencias v1/v2. No presentar estas pruebas sintéticas como aceptación productiva.

### 17.17. GitHub Actions y despliegue automático main/develop

Decisión aprobada por el usuario el 19/10/2026: reutilizar el repositorio privado `ikervillen/test-runner-deploy` y su runner autohospedado, sustituyendo los archivos de prueba por la ingesta. La convención definitiva es **develop**, no development/dev como ramas adicionales. Esto sustituye el mecanismo inicial de activación manual de 15.1/15.2, manteniendo sus garantías de datos y la posibilidad de invocar el mismo procedimiento desde PowerShell.

Comprobación remota de solo lectura: runner `servidor-prueba`, Windows/X64, etiquetas self-hosted/Windows/X64; no dispone de pwsh, por lo que los jobs del servidor usan Windows PowerShell 5.1. Docker usa `desktop-linux`, motor Linux/x86_64. El montaje `C:/DataSharing/Fenix` en solo lectura resuelve los directorios AV21/AV22/AV23/AV31/AV32. Evidencia: Actions, ejecución 39234290980. Se comprueba acceso a directorios, sin leer registros ni iniciar ingesta.

Flujo implementado: PR → comprobaciones Windows/Linux → construir/probar imagen Linux/amd64. Push/merge a main o develop → publicar la imagen exacta en GHCR → ejecutar el despliegue de esa rama mediante el runner. Un PR no despliega en el servidor. Un push directo también dispara el circuito; exigir merge mediante PR corresponde a protección de ramas, no a inferirlo del mensaje de commit.

Se crean entornos GitHub main/develop, cada uno restringido a la rama homónima y sin revisión manual obligatoria para conservar la automatización solicitada. Variables verificadas: INGESTA_INPUT_BIND=C:/DataSharing/Fenix e INGESTA_DOCKER_CONTEXT=desktop-linux. Las etiquetas son configurables, con las comprobadas como valor inicial. El GITHUB_TOKEN efímero publica/descarga imágenes con permisos por job; no se requiere SSH ni PAT persistente. No se entrega acceso al motor Docker dentro del contenedor de ingesta.

Limitación real comprobada: el intento de configurar protección de main/develop devuelve HTTP 403, solicitando GitHub Pro para el repositorio privado. Se mantiene privado; no contratar ni cambiar visibilidad automáticamente. La exigencia de PR/checks obligatorios/ausencia de force-push queda pendiente de esa capacidad. Los filtros y validaciones de CI/CD funcionan independientemente, pero no sustituyen protección de ramas ni impiden un push directo autorizado.

Los dos canales son persistentes e independientes: volúmenes nombrados fenix-aiala-main y fenix-aiala-develop, en el motor Linux/WSL2; CSV compartidos exclusivamente en lectura. Cada volumen conserva Bronze/Silver/control/runtime/logs y marcador de identidad. Main no sustituye las validaciones pendientes del contrato de piloto: se mantiene local_naive_test_v1, sin activar silenciosamente una política horaria productiva ni resolver ZF_OK. No se adoptan las tablas v1 anteriores.

La imagen fija Python/base por digest y dependencias del lockfile. BuildKit reutiliza capas separadas de dependencias/código y caché de Actions. La imagen final se prueba sin mounts operativos: creación, replay, cierre, transición y recuperación de acuse perdido. La CI añade un relevo real de workers Docker sobre un volumen efímero exclusivo y CSV sintéticos para comprobar permisos, READY sin escrituras, activación, SIGTERM, persistencia y actualización. Su limpieza solo afecta a recursos CI con identidad/propiedad comprobadas; nunca a los volúmenes main/develop.

Antes de activar: verificar digest/commit/origen/rama, contratos/protocolo, identidad del motor/volumen y HEAD actual. Rechazar despliegues obsoletos o almacenamiento perdido/cambiado; no recrear un histórico vacío. Una prueba aislada fallida no detiene la ingesta activa. El despliegue por entorno se serializa y no se cancela por nuevos pushes.

La actualización solicita SIGTERM y espera al final de la unidad en curso, sin forzar SIGKILL. Se verifica en lectura que no queden lotes PREPARED/WRITING/UNKNOWN antes de cambiar ejecutor. La nueva instancia alcanza READY sin escribir; el descriptor activo se confirma antes de habilitar su trabajo. Un journal/recibo persistente distingue fases y permite reconciliar interrupciones. Un lote incierto iniciado por la candidata permanece vinculado a esa imagen para recuperación, antes de cualquier vuelta atrás.

El rollback explícito reactiva una release previa compatible tras comprobar operaciones pendientes y exclusión de escritor. No revierte datos confirmados, elimina tablas o cambia volúmenes. Repetir la misma imagen/configuración activa comprueba su estado sin reiniciarla. Cambios incompatibles de esquema/protocolo o traslado de almacenamiento siguen siendo operaciones independientes.

El worker ejecuta fuentes/derivados en orden, registra resultados por familia y espera el intervalo tras el ciclo, evitando solapamientos. Conserva logs Docker rotados y copia JSONL diaria persistente. El heartbeat acredita vida del proceso, no integridad/completitud de todos los datos. Los locks del volumen abarcan el worker y las ejecuciones manuales del CLI actualizado; retirar cualquier escritor antiguo no cooperante antes del piloto.

Documentación operativa: Ingesta_Delta_Lake/docs/GITHUB_CICD.md. El plan maestro permanece aquí; una copia de publicación identificada se exporta al repositorio independiente para que sus contratos sean revisables desde GitHub. La primera integración se prepara mediante PR hacia develop; no fusionar automáticamente esta preparación ni iniciar la ingesta real como parte del diagnóstico.

La siguiente aceptación sigue siendo desplegar develop en el servidor, conciliar datos reales y llegadas sucesivas, revisar tándem/ZF_OK, probar replay/recuperación/reinicio sin RDP y medir recursos. Posteriormente se promueve a main. La integración de la app y el cálculo de score permanecen separados. Un diagnóstico de mounts o CI sintética satisfactoria no sustituye esa aceptación real.

### 17.18. Carga histórica continua, equidad y recursos — 20/10/2026

El usuario autoriza implementar, validar y desplegar las mejoras en entregas pequeñas,
sin confirmaciones entre ellas. La secuencia de PR separa comprobaciones MIA,
planificación de turnos, procesos/recursos y artefactos de asociaciones. Main y los CSV
originales permanecen fuera de estos despliegues del piloto develop. La ingesta debe
consumir el histórico pendiente mientras siguen llegando versiones nuevas; no requiere
que se detenga la exportación Fenix ni un modo distinto al terminar la carga inicial.

**Continuidad y recuperación.** Se conservan las tablas, capturas y controles existentes.
Antes de la primera actualización se solicitó SIGTERM, se alcanzó una frontera sin
intenciones pendientes y se verificó una copia independiente de todo el volumen:
`fenix-aiala-develop-backup-39400530708`, archivo `lake.tar.gz`, recibo `receipt.json`.
El recibo acredita 1.399 archivos, 11.358.234.361 bytes originales y 2.527.764.381 bytes
comprimidos; SHA-256 del archivo
`4818c85e03b57791d9aa9e44f24be93bfd04cff63e9f1a65a5b9f2addc5a2878`.
La copia no se elimina por actualizar código. No se vacían auditorías antiguas para
disimular su tamaño: el objetivo es evitar que cada lote vuelva a ampliarlas con
copias de todos los grupos históricos.

**MIA incremental.** Cada unidad admite hasta ocho versiones y 4 MiB de CSV; un
archivo individual mayor se atiende solo, con máximo 8 MiB. Una entrada mayor provoca
un error explícito y requiere soporte de división, sin omitirla o marcarla consumida.
Se compara la fila completa dentro de los contextos de inspección afectados, conservando
las claves y reglas de conflicto originales. Las intenciones guardan alcance, versión,
huellas de contexto y manifiesto de archivos Delta; antes/después del commit se verifican
esas evidencias y la cobertura global. Las intenciones antiguas se recuperan con su
protocolo original. El índice actual de grupos se inicializa una sola vez desde la última
publicación completa y se actualiza únicamente para los grupos afectados; las revisiones
siguen excluyendo las bases dudosas y se mantienen las consultas explícitas de histórico.

**Turnos y entradas.** El runtime procesa un lote MIA por turno, FIFO dentro de cada origen
y rotación entre orígenes con cursor confirmado junto al acuse. El CLI conserva el modo
de drenar toda la cola, con cuota opcional entre 1 y 100. Las recepciones y versiones
pendientes son duraderas: no se pierden al ceder turno, reiniciar o desaparecer el CSV
original. Se recuperan capturas Bronze huérfanas de una interrupción entre fsync y recibo.
Las fuentes no MIA reutilizan su caché de capturas estables y recuperan revisiones
capturadas en intentos fallidos. Se vuelve a calcular el hash del archivo vivo aunque
nombre, tamaño y mtime coincidan. Se soportan simultáneamente nombres fijos sobrescritos
y nombres con fecha/hora; la marca del nombre no sustituye las fechas de negocio.

CONT_ZC y LISTA_AR reciben servicio en cada vuelta, sin esperar a agotar ambas fuentes
MIA. Con backlog se inicia otra vuelta tras una pausa mínima de un segundo; al estar
al día se mide el intervalo desde el inicio del ciclo. Los resultados separan calidad
y trabajo pendiente e incluyen versiones por origen, bytes, recepción pendiente más
antigua, consumidas, velocidad entre visitas y ETA orientativa. La ETA no cuenta archivos
aún no descubiertos ni supone que todas las versiones tengan el mismo coste. La primera
visita o una visita sin avance pueden carecer de estimación; nunca se interpreta como cero.

**Procesos independientes.** Hasta dos hijos, con un pesado simultáneo. Un proceso posee
el bloqueo de su tabla y control SQLite; el supervisor posee el bloqueo global del
entorno. Las dependencias se ordenan por fases: diccionarios/máquinas → ramales/MP9 →
catálogo → hechos → asociaciones. CONT_ZC/AR pueden acompañar un lote MIA; no se habilitan
escritores concurrentes sobre una misma tabla ni conexiones SQLite compartidas. La memoria
de cada unidad se libera al salir su proceso. Los resultados atómicos se incorporan al
log duradero; una salida sin resultado válido se registra como fallo y el siguiente
intento reconcilia el control antes de reescribir. SIGTERM no inicia nuevas unidades y
espera las activas en su frontera segura, sin escalado a SIGKILL. El cambio de imagen
mantiene el preflight de operaciones pendientes y los recibos de despliegue.

**Presupuesto efectivo.** Windows dispone de 16 GiB, pero el motor observado expone
cuatro CPU y unos 7,76 GiB. El techo propuesto de 8 GiB consumiría prácticamente todo
el presupuesto de WSL2. Se fija un máximo de cuatro CPU y 6 GiB, reservando al menos
1,5 GiB dentro del motor, redondeando hacia abajo en pasos de 256 MiB. Por debajo de
4 GiB de presupuesto o de dos CPU se usa un solo proceso; por debajo de 2 GiB se rechaza
el despliegue. Memory y memory-swap coinciden: no se habilita intercambio. Los pools
Arrow/Rayon de cada hijo se limitan a dos hilos y BLAS/OpenMP a uno. El descriptor registra
los límites; un rollback conserva el presupuesto original de su release. Son límites
máximos, no reservas: activar varios entornos exige dimensionar su demanda agregada.

**Asociaciones.** Se aplaza su construcción mientras las colas MIA inventariadas tengan
trabajo pendiente. Su fase no comparte recursos con otros trabajadores. La publicación
serializa/comprime JSON canónico y verifica archivos/payloads por bloques, sin copias
completas adicionales ni deserialización para comprobar hashes. Se conserva el registro
pequeño de decisiones aceptadas junto con su huella; las publicaciones anteriores
siguen siendo legibles. Una publicación sin cambios de fuentes, calidad, decisiones,
reglas, políticas o revisiones se reutiliza tras verificar sus evidencias, sin volver a
cargar/atribuir el histórico ni generar otra versión Delta. La primera construcción
sigue necesitando los hechos aceptados en memoria: no se declara un algoritmo de
atribución de memoria constante ni capacidad ilimitada.

**Evidencia y aceptación.** PR 4 (contextos) y PR 5 (turnos) han superado CI Windows/Linux,
imagen, ciclo Docker y despliegue develop. Durante el segundo relevo, LISTA_RECH terminó
en versión 54 con 756.222 filas, partiendo de 603.027; LISTA_AR incorporó cuatro filas.
Son puntos de observación, no conciliación completa de toda la carga. Los procesos y
artefactos se validan en sus propias entregas antes de desplegarlos. Se deben comprobar
varias vueltas, llegada a CONT_ZC/defectos, avance de colas, consumo real, ausencia de
duplicados y recuperación. Los conflictos de MP9/ZF_OK, topología y candidatos de
producción permanecen visibles y separados de fallos técnicos. No se promueve a main
ni se conecta la app por el mero hecho de que un contenedor esté healthy.

Este archivo será el punto de referencia para refinamientos y decisiones posteriores. Las propuestas de arquitectura y los asuntos pendientes no deben confundirse con capacidades ya implementadas o comprobadas en el servidor.

# GitHub Actions y Docker en Windows/WSL2

**Documento histórico del despliegue anterior.** Para la candidata de `develop`,
consulta la [guía de los procesos independientes](OPERACION_PIPELINES_INDEPENDIENTES.md).
Los pasos de activación y mantenimiento descritos aquí ya no se ofrecen desde
los workflows de la candidata.

Repositorio: `ikervillen/test-runner-deploy`. Runner comprobado: `servidor-prueba`,
etiquetas `self-hosted`, `Windows`, `X64`. La antigua prueba de Nginx se sustituye por
`.github/workflows/deploy.yml`; se elimina su `docker-compose.yml`. Eliminar esos archivos
de prueba no elimina contenedores o volúmenes existentes del servidor.

## Circuito automático

Un PR ejecuta formato, análisis estático y pruebas en Windows/Linux, construye la imagen
Linux/amd64 y prueba su comportamiento real. El PR no ejecuta despliegues en el runner.
Un push a `main` o `develop` —incluido el producido por un merge— sigue el mismo circuito,
publica la imagen probada en `ghcr.io/ikervillen/test-runner-deploy` y despliega por digest.
La publicación/activación solo ocurre después de CI satisfactoria para ese commit.

| Rama | Volumen persistente Linux | Worker y descriptor |
| --- | --- | --- |
| develop | fenix-aiala-develop | Independientes de main |
| main | fenix-aiala-main | Independientes de develop |

Ambos canales permanecen ejecutándose, ingiriendo periódicamente los CSV de origen en sus
propias tablas. `develop` no sustituye la ingesta `main`. Comparten únicamente la entrada
de **solo lectura**. Las pruebas de cada imagen usan `/tmp` aislado, sin montar esos datos.

El Dockerfile fija Python 3.12.14/base por digest y dependencias de `uv.lock`. Dependencias
y código tienen capas separadas; Actions utiliza caché de BuildKit. El contenedor operativo
no instala paquetes ni actualiza código. La release conserva commit, digest, contratos,
inventario de paquetes y resultados de imagen en los artefactos del workflow.

## Configuración única de GitHub y del runner

1. Publicar el contenido de este directorio como raíz del repositorio. No publicar sus
   directorios vecinos ni CSV, Delta, SQLite, `.venv`, credenciales o logs reales.
2. Mantener el runner existente como servicio con acceso a `docker` y Windows PowerShell 5.1.
   Su cuenta debe tener acceso al mismo contexto/motor Linux que funcionó en la prueba.
   Se ha comprobado que no dispone de `pwsh`; los jobs del servidor usan `powershell` y
   scripts compatibles con 5.1. Las acciones fijadas requieren una versión actual del runner.
3. Crear los entornos GitHub `develop` y `main`, limitados a la rama homónima. El workflow
   los referencia por nombre. Si se añaden revisores obligatorios, el despliegue esperará
   su aprobación; para activación automática no configurar ese bloqueo manual.
4. Configurar la variable de repositorio **`INGESTA_INPUT_BIND`** con la ruta de origen
   aceptada por el contexto Docker: inicialmente `C:/DataSharing/Fenix`. Debe contener
   AV21, AV22, AV23, AV31 y AV32. Docker Desktop admite rutas Windows; un Docker Engine
   directo en WSL puede requerir `/mnt/c/DataSharing/Fenix`. No son intercambiables por
   definición: ejecutar `diagnose` para comprobar cuál acepta el motor real.
5. `INGESTA_DOCKER_CONTEXT` es opcional: si se omite se usa `docker context show` de la
   cuenta del runner. `INGESTA_RUNNER_LABELS` es opcional, JSON de etiquetas; por defecto
   usa las tres verificadas arriba. No inventar una etiqueta adicional que deje jobs en cola.
6. Opcional: `INGESTA_SITE_CONFIG` apunta a un JSON externo previamente preparado según
   `deployment/site.example.json`. Permite cambiar orígenes, intervalos, namespace y volúmenes.
   Sin esa variable, `prepare_site.ps1` genera un perfil por canal en
   `%LOCALAPPDATA%/FenixIngesta/`, fuera del checkout, con identidades estables por repo/canal.
7. GHCR debe permitir al repositorio publicar y descargar su paquete. Se utiliza el
   `GITHUB_TOKEN` efímero del job, con escritura solo en publicación y lectura en despliegue.
   No hace falta guardar un PAT en el servidor. El login Docker se realiza en un directorio
   temporal que conserva los endpoints/TLS del contexto, sin copiar credenciales del usuario;
   se elimina al terminar. No se monta el socket Docker en el worker.
   El login entrega el token como bytes UTF-8 sin BOM desde Python, sin pasar por la
   tubería de Windows PowerShell 5.1: un BOM altera la contraseña y provoca `denied`
   aunque los permisos sean correctos. La codificación del llamante se conserva.
8. Proteger `main` y `develop`: exigir PR y las comprobaciones de CI aplicables, impedir
   force-push y restringir quién puede modificar workflows/entornos/variables. Un push directo
   también dispara CD: la exigencia de merge se aplica mediante protección de ramas.

**Limitación comprobada del repositorio actual:** la API de protección de ramas devuelve
403 y exige GitHub Pro para este repositorio privado. No se ha cambiado su visibilidad.
La protección anterior es un requisito pendiente, no una garantía aplicada. CI/CD mantiene
sus comprobaciones, pero un colaborador con permiso de push podría evitar pasar por PR.

No se precisa SSH. El runner se conecta a GitHub y GHCR con salida autorizada. El motor
Docker y WSL2 deben persistir/arrancar sin una sesión RDP interactiva. Verificar este punto
reiniciando el servidor en el piloto; que funcionara en una consola no lo garantiza.

## Datos persistentes y primer despliegue

Los volúmenes **nombrados** residen en el motor Linux de WSL2, fuera de la capa del contenedor.
Dentro de cada uno: `bronze/`, `silver/`, `control/`, `runtime/`, `logs/` y un marcador de
identidad del entorno. El nombre/ID de motor, volumen y marcador quedan en el descriptor.
Los volúmenes se crean solo durante la preparación inicial; bootstrap crea directorios y
marcador, nunca Delta ni SQLite. El arranque operativo posterior inicia la primera ingesta.

Una actualización no recrea un volumen perdido ni adopta datos sin identidad. Cambiar motor,
volumen, contrato o protocolo exige una operación explícita independiente. El instalador
no elimina volúmenes ni ejecuta `compose down`, `down -v`, `prune` o borrados de tablas.
Respaldar volúmenes/almacenamiento WSL y estado de despliegue fuera de Git; la persistencia
de un volumen no sustituye un backup ni protege de eliminar/reinstalar la distribución WSL.

Los dos canales usan los contratos vigentes **de piloto**, política `local_naive_test_v1`.
`main` designa el canal operativo estable, pero no elimina los pendientes de zona horaria,
ZF_OK, capacidad, almacenamiento y aceptación productiva del plan. No se habilita
silenciosamente `Config.environment=production`. Las tablas v1 anteriores no se montan
ni migran automáticamente a estos volúmenes v2 nuevos.

La cuenta 10001:10001 escribe los directorios del volumen. Solo bootstrap usa root y capacidad
CHOWN, para directorios nuevos; no modifica recursivamente propietarios de un histórico.
Contenedores sin red de aplicación, filesystem raíz de solo lectura, sin capacidades, sin
puertos y con límites CPU/memoria. Administrar Docker permite otros montajes: esta separación
operativa no constituye una frontera de seguridad contra un administrador del motor.

## Actualización, interrupción y rollback

El controlador comprueba procedencia/digest/commit, contratos, configuración y HEAD actual
de rama, rechazando un despliegue que haya quedado obsoleto. Un bloqueo de despliegue por
entorno y la concurrencia de Actions serializan activaciones; no se cancelan por otro push.
Repetir la misma imagen/configuración ya activa comprueba el worker y no lo reinicia.

Después de la prueba aislada, desactiva reinicio automático del contenedor anterior y envía
SIGTERM. El worker termina la unidad en curso y sale; no se fuerza con SIGKILL. Si se agota
el plazo, se aborta/restaura la programación de la versión anterior. Antes de cambiar imagen,
se comprueban contratos, esquemas, propiedad y operaciones PREPARED/WRITING/UNKNOWN en lectura.
Con escritor detenido y sin WAL, SQLite se consulta en modo readonly/immutable, sin crear
SHM ni copiar controles grandes a memoria. Un WAL pendiente bloquea el cambio de imagen:
no se ignora. El worker propietario lo lee/recupera bajo lock desde su montaje operativo,
donde puede reconstruir los archivos de coordinación necesarios. La comprobación previa
no equivale a un escaneo completo de integridad del histórico.
Un lote incierto se reconcilia con su release; no se entrega a otra por el mero despliegue.

La nueva instancia empieza en READY sin escribir datos. Se confirma el descriptor activo
y después se habilita el trabajo. Un journal persistente permite recuperar una interrupción
antes/después de ese cambio; se registra un recibo por operación. Si la candidata comenzó
una operación incierta, queda vinculada a esa imagen para recuperarla antes de cualquier
vuelta atrás. El despliegue no presenta las revisiones de negocio —como ZF_OK— como errores
técnicos resueltos; la ingesta puede avanzar parcialmente y conserva sus incidencias.

Los locks del volumen impiden dos workers del entorno y rechazan ingestas manuales que
usen el CLI actualizado mientras el worker está activo. **Desactivar antes cualquier tarea
antigua/escritor no cooperante**; un programa ajeno a estos locks no queda protegido por ellos.

Desde Actions → workflow → Run workflow:

- `validate`: CI y prueba de imagen, sin publicar/activar por ser una ejecución manual.
- `diagnose`: consulta contexto/motor y comprueba directorios de entrada con montaje de solo
  lectura. No lee filas ni ejecuta ingesta, no monta volúmenes de datos.
- `docker-inventory`: inventario de recursos retirables de `develop` en el runner de planta.
- `docker-cleanup`: recalcula el inventario bajo el bloqueo de despliegue de `develop`,
  retira únicamente sus candidatos y registra los IDs retirados y cualquier conflicto.
  Conserva la release activa, el rollback y los volúmenes.
- `rollback`: elegir `main` o `develop` y reactivar la release anterior compatible con la
  misma frontera segura. Conserva todos los commits de datos. No es una restauración de BD.

El rollback reutiliza el contenedor/imagen retenidos y verifica compatibilidad y ausencia
de operaciones pendientes. Tras activar o revertir `develop`, el resumen del job de servidor
publica el inventario Docker de esa instancia. `keep` identifica la release activa y el
destino de rollback. `retire_containers` contiene los workers detenidos de la instancia
que ya no son necesarios. `retire_images` lista ID, referencias, commit y los contenedores
que hay que retirar primero. Una imagen usada por cualquier otro contenedor queda excluida.
El inventario es de solo lectura y se aplaza mientras haya un despliegue sin reconciliar.

Para retirar recursos, actualizar primero el inventario con
`python -m deployment.inventory --site SITIO.json --branch develop` en el servidor.
Retirar únicamente los IDs indicados, primero los contenedores y luego las imágenes,
sin forzar la eliminación. Conservar los volúmenes y evitar limpiezas globales.

Un fallo previo a activar
mantiene/restaura la versión anterior; un estado incierto conserva journal y falla de forma
explícita. El siguiente intento lo reconcilia antes de otra activación.

## Observación y aceptación en servidor

El descriptor y los recibos se guardan por canal bajo
`%LOCALAPPDATA%/FenixIngesta/deployments/`. Los logs de Docker están rotados; cada resultado
de familia tiene además una copia JSONL diaria persistente en el volumen, con estado,
duración y referencia al control fuente. El heartbeat indica vida del proceso, **no** que
todos los datos estén completos: revisar los resultados PARTIAL/BLOCKED/FAILED y sus auditorías.
El ciclo atiende las once fuentes y dos derivados por fases de dependencias, con hasta
dos procesos y un pesado simultáneo (entrega 15). Las fuentes MIA ceden tras un lote,
rotando entre orígenes; CONT_ZC/AR reciben servicio en cada vuelta. Con backlog se vuelve
a recorrer el ciclo tras un segundo. Al estar al día, el intervalo se mide desde el
inicio del ciclo, sin acumular supervisores solapados. Las asociaciones se aplazan
explícitamente mientras quedan versiones MIA inventariadas.

La CI local no sustituye el ensayo real: primero diagnosticar acceso; después desplegar
`develop`, conciliar fuente/atribuidas/pendientes, revisar las tándem y ZF_OK, comprobar replay,
recuperación y reinicio del host; finalmente promover a `main`. Medir rendimiento/consumo
con las llegadas reales. Integración de la app y cálculo de score continúan separados.

## Primera carga MIA con muchas versiones acumuladas

El piloto del 19/10/2026 confirmó que analizar todas las versiones MIA simultáneamente
superaba el límite de memoria del worker. `LISTA_RECH` y `LISTA_DEFECTOS` se procesan
por grupos de hasta ocho versiones únicas y 4 MiB de CSV; un archivo individual mayor
se procesa solo, con un máximo MIA de 8 MiB. Un archivo superior produce un error
explícito y permanece pendiente de resolver: nunca se consume ni se omite silenciosamente. Los descriptores
de captura son pequeños y las evidencias CSV permanecen en Bronze.

Cada grupo conserva su intención, transacción Delta, confirmación y auditoría propias.
Solo después de confirmar un lote `COMPLETED` o `PARTIAL` se registra `(origen, SHA-256)`
en `consumed_inputs`, con referencia al lote. Una interrupción antes de registrar esa
marca reevalúa la versión frente al destino ya confirmado, sin duplicar filas. Una
interrupción posterior continúa con las versiones restantes. Un resultado `PARTIAL`
significa que las filas aceptadas y las contradicciones están conservadas; no resuelve
los casos de negocio pendientes ni habilita sus grupos para sumar inspeccionadas.

Los archivos se siguen capturando/verificando y cada intento conserva sus recepciones,
incluidos renombrados. Una versión ya consumida no se vuelve a analizar ni replica su
evidencia de incidencia. Un contenido nuevo bajo el mismo nombre tiene otro hash y se
procesa normalmente. Las columnas y claves de Silver se mantienen. Los contadores de
transformación se suman por grupos procesados (`stats_scope`); las bases de inspección
se calculan globalmente sobre registros aceptados y casos abiertos, sin sumar repetidos.

SIGTERM permite terminar el grupo en curso y salir antes del siguiente. El resultado
`STOPPED_AFTER_BATCH` indica cobertura incompleta y registra cuántas versiones restan.
El siguiente arranque retoma esos grupos. Desde la entrega 15, el presupuesto se calcula
con la capacidad real del motor: máximo cuatro CPU y 6 GiB, dejando al menos 1,5 GiB
de margen en WSL2 y redondeando hacia abajo en pasos de 256 MiB. Con menos de 4 GiB
de presupuesto o dos CPU se usa un proceso; con menos de 2 GiB se rechaza el despliegue.
Los límites de memory y memory-swap coinciden para evitar paginación. El descriptor
registra los valores; los rollback de releases previas conservan 2 CPU / 4 GiB.
El 20/10 el motor WSL2 exponía 8.328.400.896 bytes (unos 7,8 GiB), aunque Windows
disponga de 16 GB. El archivo MIA mayor medido ocupaba 1.454.719 bytes. Estos límites
son de entrada y contenedor: no prometen un factor fijo de expansión en Python ni
un tiempo máximo universal. La lectura MIA por contexto y el índice incremental de
grupos evitan repetir el histórico por lote. Los procesos liberan su memoria al salir.
El historial de asociaciones aún se materializa en su construcción inicial, por lo
que se sigue midiendo. Los techos no son reservas: varios entornos activos requieren
dimensionar su consumo agregado. El ajuste de recursos no sustituye estas correcciones.


La cola `input_versions` se identifica por `(origen, SHA-256)`. Su creación migra los
recibos anteriores una sola vez, de forma transaccional; no relee toda la auditoría
histórica en cada ciclo. La captura y su alta en cola se confirman juntas. Los CSV
capturados pendientes se recuperan desde Bronze aunque ya no estén en la entrada.
Dentro de cada origen, las versiones sin hora se ordenan primero, después por marca
de exportación, mtime, ruta y hash. Entre orígenes se rota con un cursor persistente
confirmado junto al acuse. Este orden no modifica fechas de negocio.

Para versiones ya capturadas se verifica siempre el hash del archivo de entrada y
de su evidencia Bronze. Si coinciden con una captura estable anterior, se reutiliza
esta sin esperar otra ventana de estabilización ni generar una nueva copia diaria.
Un cambio que conserve nombre, tamaño y mtime también se detecta por contenido.
Las versiones nuevas mantienen la doble lectura de estabilidad y los reintentos.
Los grupos emiten `INPUT_BATCH_CONFIRMED` con duración, bytes, versión Delta y
versiones pendientes. Los resultados de cada turno añaden pendiente por origen/bytes,
velocidad entre visitas y ETA orientativa. Las llegadas posteriores al descubrimiento
se incorporan en el siguiente ciclo, sin esperar a vaciar el histórico de otra fuente.

Desde la entrega 17, la revisión del directorio es independiente del consumo: cada
fuente vuelve a descubrir y verificar sus CSV cada 600 s desde el inicio de la última
revisión completa. Entre revisiones sigue drenando la cola, sin repetir las lecturas
de todos los archivos conocidos por cada lote. Un fallo de revisión reintenta sin
esperar; un fallo de transformación no retrasa las capturas pendientes. Una nueva
activación o cambio de orígenes fuerza revisión inmediata. El CLI usa intervalo cero
por defecto. Los resultados muestran `input_scan_performed`, el intervalo y
`next_input_scan_at` (Unix, operación). Un NO_INPUTS sin revisión significa que esa
unidad no ha tratado capturas nuevas; no borra los datos ni resuelve incidencias.
Al vaciar la cola el supervisor espera al plazo de revisión más cercano, sin añadir
otro intervalo completo. Las versiones MIA nuevas respetan la cola FIFO de su origen.

### Reinicio explícito del almacenamiento de pruebas

Tras la autorización expresa del usuario para reconstruir el piloto desde los CSV,
se utiliza `python -m deployment.reset_develop --site <site-develop.json>
--confirm-volume fenix-aiala-develop --run-url <URL-del-job>`. Esta operación destructiva
es independiente del despliegue ordinario y solo admite el volumen confirmado de
develop. Los despliegues habituales continúan rechazando un volumen desaparecido;
nunca lo recrean silenciosamente para ocultar una pérdida de histórico.

El reinicio verifica motor, identidad y etiquetas, exige que ningún contenedor
esté adjunto al volumen, registra un recibo y recrea el volumen vacío mediante Docker.
Los descriptores antiguos de activación se archivan como ficheros individuales bajo
`state_root/develop/resets/<id>/`, sin mover ni borrar carpetas del host. `main` y
el montaje CSV permanecen fuera del alcance de la operación. El siguiente despliegue
hace bootstrap del volumen vacío e inicia la carga con lotes acotados y controles nuevos.

Los fallos de escritura futuros se recuperan por su intención y `operation_id`,
conciliados con la historia Delta. El reinicio no sustituye esa recuperación ordinaria.

También se reconcilian capturas huérfanas: si el proceso cae después de sincronizar
Bronze y antes de confirmar su recepción SQLite, el siguiente intento verifica el
CSV por su hash y lo incorpora a la cola. Solo se admite el espacio de nombres
controlado planta/origen/familia/hash. Cuando ya no existe la entrada original,
se conserva la ruta Bronze como procedencia y se deja desconocida la marca de
exportación, sin inventar un nombre o fecha de negocio. En Linux se sincronizan
los directorios de captura antes de confirmar su recepción.

La interpretación consumida queda vinculada a contrato, codificación y política
temporal. Cambiarla exige una migración explícita; no permite reutilizar marcas
anteriores y omitir silenciosamente una nueva interpretación de los mismos bytes.

Prueba local aislada del 20/10: veinte versiones solapadas de un CSV real de
1.172.329 bytes, 23,4 MB acumulados, siete lotes y 28.837 registros únicos.
La primera carga tardó 78,9 s y el replay 6,4 s, sin otra versión Delta.
El pico de working set medido en Windows fue 980.705.280 bytes (935,3 MiB).
Es una referencia del ensayo, no una garantía de consumo o latencia para cualquier
histórico; la comprobación del conjunto del servidor sigue siendo obligatoria.

La reconciliación de una intención técnica pendiente se ejecuta antes de descubrir
y capturar nuevas entregas. Un fallo de acceso a un CSV nuevo no puede impedir
confirmar o reconciliar una operación anterior que ya tenga evidencia durable.

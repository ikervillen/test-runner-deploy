# GitHub Actions y Docker en Windows/WSL2

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
- `rollback`: elegir `main` o `develop` y reactivar la release anterior compatible con la
  misma frontera segura. Conserva todos los commits de datos. No es una restauración de BD.

El rollback reutiliza el contenedor/imagen retenidos y verifica compatibilidad y ausencia
de operaciones pendientes. No borrar contenedores/imágenes anteriores necesarios para este
camino; su retención/mantenimiento se gestionan por separado. Un fallo previo a activar
mantiene/restaura la versión anterior; un estado incierto conserva journal y falla de forma
explícita. El siguiente intento lo reconcilia antes de otra activación.

## Observación y aceptación en servidor

El descriptor y los recibos se guardan por canal bajo
`%LOCALAPPDATA%/FenixIngesta/deployments/`. Los logs de Docker están rotados; cada resultado
de familia tiene además una copia JSONL diaria persistente en el volumen, con estado,
duración y referencia al control fuente. El heartbeat indica vida del proceso, **no** que
todos los datos estén completos: revisar los resultados PARTIAL/BLOCKED/FAILED y sus auditorías.
El ciclo procesa las once fuentes y dos derivados en orden de dependencias; espera el
intervalo configurado tras cada ciclo, sin acumular ejecuciones solapadas.

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
El siguiente arranque retoma esos grupos. El presupuesto del worker continúa siendo
4 GiB, con `--memory-swap=4g` para impedir que la paginación oculte presión de memoria.
El 20/10 el motor WSL2 exponía 8.328.400.896 bytes (unos 7,8 GiB), aunque Windows
disponga de 16 GB. El archivo MIA mayor medido ocupaba 1.454.719 bytes. Estos límites
son de entrada y contenedor: no prometen un factor fijo de expansión en Python ni
un tiempo máximo universal. El historial aceptado también consume memoria y debe
seguirse midiendo. El ajuste diagnóstico temporal a 6 GiB de la primera imagen
no sustituye esta corrección.


La cola `input_versions` se identifica por `(origen, SHA-256)`. Su creación migra los
recibos anteriores una sola vez, de forma transaccional; no relee toda la auditoría
histórica en cada ciclo. La captura y su alta en cola se confirman juntas. Los CSV
capturados pendientes se recuperan desde Bronze aunque ya no estén en la entrada.
Las versiones sin hora se ordenan primero, después por marca de exportación,
mtime, origen, ruta y hash. Este orden no modifica fechas de negocio.

Para versiones ya capturadas se verifica siempre el hash del archivo de entrada y
de su evidencia Bronze. Si coinciden con una captura estable anterior, se reutiliza
esta sin esperar otra ventana de estabilización ni generar una nueva copia diaria.
Un cambio que conserve nombre, tamaño y mtime también se detecta por contenido.
Las versiones nuevas mantienen la doble lectura de estabilidad y los reintentos.
Los grupos emiten `INPUT_BATCH_CONFIRMED` con duración, bytes, versión Delta y
versiones pendientes. Las llegadas posteriores al descubrimiento se incorporan en
el siguiente ciclo; el intervalo comienza tras terminar el ciclo en curso.

### Recuperación explícita de la carga inicial interrumpida

`python -m deployment.maintenance --site <site-develop.json> --family LISTA_RECH
--run-url <URL-del-job>` es una operación **manual y offline**, separada del despliegue
automático. Requiere el volumen existente y la imagen propietaria exacta, retenida
localmente. Funciona aunque se haya eliminado su contenedor. Nunca crea un volumen,
arranca un worker ni procesa CSV como parte de la reparación.

El controlador bloquea despliegues simultáneos, verifica motor, identidad, volumen,
configuración e imagen; exige que no exista ningún contenedor activo con ese volumen.
Ejecuta el script montado de solo lectura dentro de la imagen propietaria, sin red,
con las raíces originales y el volumen persistente en escritura. El script adquiere
los bloqueos de runtime y escritor y abre una transacción SQLite inmediata.

Solo se permite retirar una intención inicial en `PREPARED`, sin versión Delta,
sin contenido alguno en el destino y sin intenciones de filas que puedan haberse
escrito. `WRITING`, `UNKNOWN`, un Parquet huérfano, un hash alterado o cualquier duda
bloquean la operación y requieren reconciliación. La inexistencia del contenedor
no se utiliza como prueba de que no hubo commit.

Se verifican los recibos Bronze y se copia el JSON preparado íntegro mediante bloques
de 1 MiB, sin cargarlo completo en Python. El archivo se sincroniza y se verifica
con SHA-256; se comprueba espacio libre antes de copiar. La evidencia queda bajo
`/lake/control/recovery/<batch_id>.prepared.json`. Después se confirma atómicamente:

- El lote y sus intenciones pasan a `ABORTED_UNWRITTEN`; no se marca ningún CSV como consumido.
- El campo JSON grande se sustituye por la referencia al archivo íntegro archivado.
- Se conserva el resto de intenciones, incidencias y recibos sin borrar ni sumar datos.
- Se registra un evento de mantenimiento y una generación nueva para el reprocesamiento.

Una interrupción antes del commit deja el lote original pendiente; el archivo ya
archivado se verifica y reutiliza en el reintento. Una interrupción posterior deja
la resolución persistida y el reintento no la duplica. El checkpoint WAL se exige
antes de permitir el preflight de actualización. El controlador conserva también
un recibo externo al checkout con imagen propietaria, hash del script y resultado.

Tras validar esta reparación se despliega la imagen con lotes acotados. No se debe
reanudar la imagen antigua para reconstruir la carga acumulada: volvería a agruparla
entera. No ejecutar VACUUM, borrar SQLite, recrear el volumen ni eliminar Bronze
para resolver este caso. Los commits inciertos ordinarios siguen reconciliándose
por su `operation_id` y la historia Delta antes de escribir otra vez.


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

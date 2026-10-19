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
2. Mantener el runner existente como servicio con acceso a `docker` y PowerShell 7 (`pwsh`).
   Su cuenta debe tener acceso al mismo contexto/motor Linux que funcionó en la prueba.
   Las acciones fijadas requieren una versión actual del runner; habilitar su actualización.
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
8. Proteger `main` y `develop`: exigir PR y las comprobaciones de CI aplicables, impedir
   force-push y restringir quién puede modificar workflows/entornos/variables. Un push directo
   también dispara CD: la exigencia de merge se aplica mediante protección de ramas.

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

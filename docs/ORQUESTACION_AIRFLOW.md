# Orquestación de Gold con Airflow

Airflow 3.4 sustituye al bucle `watch` como único escritor del almacén Gold.
`watch` se conserva como respaldo y vuelve con un solo cambio de comando.

## Qué cambia

| | `watch` | Airflow |
|---|---|---|
| Disparo | Bucle cada `poll_seconds`, carril `live` | `gold_publish` cada 10 min, solo si Silver avanzó; `gold_history` en el minuto 0 y en el 30 |
| Techo del corte | `attempt_cutoff(datetime.now())` | `attempt_cutoff(hora programada de la ejecución)` |
| Comprobación previa | No | Sensor `coverage_ready`: espera hasta 8 min si ninguna producción puede avanzar |
| Reintentos | Siguiente vuelta del bucle | 3 con espera exponencial (máx. 5 min), solo en fallos transitorios |
| Historial | `runs/*.json` en Gold | Además, cada ejecución con logs en la UI |
| Repetir un corte | No | *Clear* de la ejecución: mismo corte, resultado `UNCHANGED` si nada cambió |
| Backup y mantenimiento | Job externo `ops/backup-gold.ps1` | DAG `gold_nightly` 02:30: backup verificado y después compactación y VACUUM |
| Ensayo de restauración | No | DAG `gold_restore_drill`, domingos 04:30 |

La regla industrial (escalón de 10 minutos, fuente continua obligatoria,
corte por producción según la cobertura acreditada) vive en `app.gold.schedule`
y `app.gold.coverage` y la comparten `watch` y `publish`. Los DAG solo pasan la
hora programada, que es el techo del intento: cada producción se calcula hasta
el último corte que Silver acredita (`docs/DECISION_CORTE_POR_COBERTURA.md`).

Hay una sola definición de score, partida por producción y por hora de reloj.
La ventana de ocho horas es la lectura de esas ocho franjas. El turno de las
06:00, 14:00 y 22:00 es otra ventana y no se recalcula al cerrar cada hora.

`gold_publish` es el carril en vivo. Cuando Silver cierra una publicación y el
corte acreditado avanza, reescribe solo la hora abierta. El cron de diez
minutos es la red de seguridad: si esa publicación ya está calculada, sale sin
leer el histórico de hechos. Si la hora abierta ya está al día y dentro de las
ocho horas falta alguna, rellena una sola. `coverage_ready` ejecuta `coverage`
cada minuto, fuera del pool de mantenimiento, hasta que alguna producción
pueda avanzar o no haya nada nuevo; si todo sigue bloqueado a los 8 minutos,
la espera se salta y `publish` se ejecuta igualmente para dejar el bloqueo
registrado. Límite de 20 minutos.

`gold_history` mira la cola cada media hora. Si no hay ninguna hora cerrada
fuera de la ventana de ocho horas, sale. Si la hay, escribe una: la más
reciente de la producción más reciente. Una hora ya guardada no se vuelve a
calcular cuando envejece y cambia de dueño. Si el trozo falla, el siguiente
intento repite solo esa hora. Los totales de producción, de turno y las tasas
de referencia se leen de las horas ya guardadas; no forman parte del
micro-lote en vivo. En este servidor los trozos históricos van de uno en uno.
El cálculo de los dos carriles no comparte el hueco `gold_store`: solo el
commit, de unos segundos, espera al otro escritor. Backup y mantenimiento
siguen en ese hueco.

## Arquitectura

`compose.airflow.yaml` se superpone a `compose.gold.yaml`:

- `airflow-postgres`: base de metadatos de Airflow (volumen `airflow-db`).
- `airflow-init`: tarea única; migra la base, crea el administrador y el pool
  `gold_store` (1 hueco) que serializa backup y mantenimiento. La publicación
  en vivo y la histórica calculan a la vez y solo serializan el commit.
- `airflow-scheduler`: planifica y **ejecuta** las tareas (LocalExecutor). Es el
  único que monta `/silver` (ro), `/gold`, `/config` (ro) y `/backup`.
- `airflow-dag-processor`: lee los DAG incluidos en la imagen.
- `airflow-apiserver`: UI y API en `127.0.0.1:${AIRFLOW_PORT:-8085}`.
- `publisher` (`watch`) pasa al perfil `watch` y no arranca.

Sin Celery, Redis ni triggerer: un host, sin operadores diferibles. La imagen
`orchestration/airflow/Dockerfile` contiene los DAG y el CLI Gold en un
virtualenv propio (`/opt/calidad/.venv`, desde `uv.lock`), aislado de las
dependencias de Airflow; no se monta el socket de Docker.

Recursos por defecto: scheduler 3 GB (incluye los 2 GB del publicador), API
1 GB, procesador de DAG 1 GB, Postgres 512 MB. Ajustables con
`AIRFLOW_*_MEMORY_LIMIT` / `AIRFLOW_*_CPU_LIMIT`.

## Activación

Si el productor de `develop` vive en el mismo motor Docker y publica Silver en
su volumen nombrado, añadir `-f compose.ingesta-develop.yaml` tras los otros dos
ficheros. Fijar `INGESTA_DEVELOP_VOLUME` al volumen del descriptor activo de
`develop` y comprobar sus etiquetas antes de arrancar. El override sustituye el
montaje de `/silver` por ese volumen externo en solo lectura. Compose sigue
exigiendo `HOST_CONFIRMED_SILVER_ROOT` al interpolar el fichero base, aunque
ese montaje quede sustituido.

Usar el mismo nombre de proyecto al sustituir un publicador existente. Detener
su servicio `publisher` y comprobar que ha salido antes de arrancar el
scheduler. En un Airflow existente, pausar también `gold_nightly` antes del
relevo. Comprobar una primera publicación Silver continua y verificar su lectura
desde la API antes de habilitar el DAG nocturno. La retención debe conservar las
publicaciones que siguen siendo base de scores históricos.

1. Copiar `.env.airflow.example` a `.env.airflow` (fuera de Git) y generar los
   secretos con los comandos del propio fichero.
2. Montar el destino de backup independiente y fijar `HOST_GOLD_BACKUP_ROOT`.
3. Asignar los permisos de Gold y del destino de backup al UID `AIRFLOW_UID`
   (10001, el mismo que el publicador).
4. Validar y arrancar:

   ```text
   docker compose -f compose.gold.yaml -f compose.airflow.yaml --env-file .env --env-file .env.airflow config --quiet
   docker compose -f compose.gold.yaml -f compose.airflow.yaml --env-file .env --env-file .env.airflow up -d --build
   ```

`gold_publish`, `gold_history` y `gold_restore_drill` se crean activos.
`gold_nightly` se crea en pausa para habilitarlo después de validar el backup
y la política de retención.

## Operación

El CLI Gold termina con un código de salida que indica si reintentar sirve
(`app.gold.failures`, valores de `sysexits.h`):

| Código | Causa | Airflow |
|---|---|---|
| 65 | Contrato o entrada no verificable (`IntegrityError`) | Falla al primer intento |
| 2 | Argumentos inválidos | Falla al primer intento |
| 75 | Almacén bloqueado por otro escritor | Reintenta |
| 74 | Error de sistema de ficheros (montaje, red) | Reintenta |
| 70 | Error inesperado | Reintenta |

- **Una ejecución falla:** si es transitorio, se reintenta sola. Si agota los
  reintentos o es un error de datos, la siguiente ejecución publica el corte
  vigente; Gold conserva la publicación anterior. Las alertas de
  `ops/gold-alerts.yaml` leen el estado de Gold, no del orquestador.
- **Repetir un corte concreto:** *Clear* sobre la ejecución en la UI.
- **Cola histórica:** `gold_history`, una hora cerrada por ejecución. No lanzar
  `publish` sin `--lane` (el valor por defecto es `live`) ni un `build` completo
  para vaciar esa cola.
- **Recálculo de producciones ya cerradas:** sigue siendo `python -m app.gold build
  --recalculate-closed ... --reason ...`, manual y fuera de Airflow.
- **Pausar la publicación:** pausar `gold_publish` y `gold_history` en la UI.

## Volver a `watch`

```text
docker compose -f compose.gold.yaml -f compose.airflow.yaml --env-file .env --env-file .env.airflow stop airflow-scheduler
docker compose -f compose.gold.yaml --env-file .env up -d publisher
```

Ambos escritores nunca deben convivir: el bloqueo del pipeline rechazaría al
segundo, pero sus fallos ensuciarían el historial y las alertas.

## Backups, mantenimiento y ensayo

`gold_nightly` ejecuta `backup` y, solo si termina verificado, `maintain`: el
VACUUM borra ficheros y nunca debe hacerlo sin una copia reciente.

- **Backup:** destino `/backup/<UTC>-try<intento>`. `backup` rechaza destinos
  existentes y cada reintento escribe uno nuevo, verificado por SHA-256. Los
  intentos fallidos quedan para su revisión. No hay retención automática de
  copias: borrarlas requiere decidir RPO y retención.
- **Mantenimiento:** compacta y limpia las tablas conservando legibles todas
  las publicaciones más recientes que `GOLD_PUBLICATION_RETENTION_DAYS` (30 por
  defecto): esa es la ventana de vuelta atrás. Ver `OPERACION_GOLD.md`.
- **Ensayo de restauración:** cada domingo restaura la última copia en
  `/backup/.restore-drill`, la abre con el contrato Gold, lee todas las tablas y
  la borra. No usa el pool: la publicación continúa durante el ensayo.

## Zona horaria

Los cortes son hora local industrial. Los DAG usan `GOLD_PLANT_TIMEZONE`
(`Europe/Madrid` por defecto), con cambio de hora incluido. `watch` usa la hora
del contenedor, que es UTC si no se define `TZ`: en ese caso su corte queda una
o dos horas más atrás que el de Airflow. Confirmar la zona de los datos Silver
antes de activar.

## Validación

- `orchestration/airflow/tests` (CI, job `airflow-dags`): importación de los
  DAG, pools conocidos, planificación en hora de planta, backup antes del
  mantenimiento y ejecución real con el *task runner* de Airflow contra un CLI
  simulado, incluido que la salida 65 no se reintenta, la 75 sí, y que un corte
  bloqueado publica tras agotar la espera.
- `compose-contract`: la combinación de ficheros es válida y `publisher` queda
  fuera.
- `container-build`: construye la imagen.

Pendiente en servidor: arranque real de la pila, permisos de los montajes y
primera publicación continua.

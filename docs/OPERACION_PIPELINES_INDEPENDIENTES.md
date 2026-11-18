# Funcionamiento y preparación de los caminos independientes

Esta entrega prepara código, pruebas y configuración. No ejecuta la activación,
no migra los volúmenes del servidor y no sustituye los servicios existentes.
Los PR de ingesta y Gold están encadenados. La rama
`release/independent-pipelines-candidate` reúne la cadena de ingesta y retira los
workflows anteriores de despliegue y mantenimiento. Se integrará cuando se dé
la orden de fusionar. Las ramas intermedias conservan los workflows anteriores.

## Procesos y propiedad de los datos

```text
Fenix, solo lectura
  ├─ ingesta-live    → Bronze live    → Silver live    → gold-live    → Gold live
  └─ ingesta-history → Bronze history → Silver history → gold-history → Gold history

Bronze live ──capturas inmutables──→ Bronze history
Silver live ──8 h + copia/recibo───→ archivo Silver history
Gold live + Gold history ──publicaciones fijadas──→ API de consulta
```

Son cuatro procesos propietarios en cuatro contenedores, más una API de lectura.
Cada camino tiene seis volúmenes distintos: Bronze, Silver, Gold, control,
checkpoint e intercambio. Cada escritor modifica exclusivamente sus destinos.
Los consumidores reciben montajes ajenos de solo lectura; los procesos Gold no
montan SQLite privados ni Fenix. La API solo monta los dos almacenes Gold.

`PipelineWorker` aplica la misma implementación de contratos, transformación,
asociaciones y revisiones a ambos caminos. `pipeline_intake` descubre antes de leer:
live fija una frontera global en el comienzo del último bloque de diez minutos,
aunque sea antiguo. Histórico recupera los bloques anteriores a esa frontera fija;
los posteriores pertenecen a live. Puede conservar capturas del feed Bronze sin
transformar de nuevo los lotes correctos. Un bloqueo en una tabla
histórica no requiere liberar ninguna tabla live para confirmar datos recientes.

El planificador live reserva capacidad para entrada y transformación de fuentes;
los derivados, revisiones pendientes y retiradas tienen turnos independientes y
acotados. Se conservan los pendientes reales si falta una dependencia industrial:
la separación no permite inventar catálogos, aprobaciones ni cobertura.

## Envejecimiento, correcciones y recuperación

Las ocho horas empiezan en la primera publicación confirmada en Silver live; para
lotes completamente fallidos, en su primer intento de procesamiento. Live concede
un único reintento al cierre de la hora del lote (09:20–09:30 → 10:00), recapturando
archivos corregidos. Si sigue fallando, espera su transferencia al histórico para
revisión prioritaria. El plazo no se reinicia al reintentar ni al reiniciar servicios.
Los estados «transferido para revisión» y «archivado y confirmado» son distintos.
Recuperación de Fenix y
envejecimiento son colas diferentes. Histórico copia cambios confirmados con su
identidad, revisión, versión Delta y hashes, verifica el resultado y publica un
recibo recuperable. Solo el propietario live puede retirar su copia después del
recibo y del plazo. Un fallo entre etapas se reanuda de forma idempotente.

Un recibo de una versión anterior no autoriza retirar una corrección posterior.
Los cambios de clave, rechazos acreditados y eliminaciones conservan su evidencia.
Durante el solapamiento, `SilverView` fija publicaciones y reconcilia por claves y
revisiones; una contradicción necesita revisión. No se decide por orden de llegada
ni por pertenecer a live. No ejecutar VACUUM o limpieza de evidencias referenciadas:
esta entrega conserva los logs y no incluye una política automática de purgado.

Los checkpoints pertenecen al almacén, no al token de activación ni al commit.
Reiniciar o actualizar un trabajador conserva su progreso. Una raíz existente sin
marca de propietario se rechaza: no se adopta automáticamente un volumen antiguo.

## Gold y consultas

`app.gold.independent_runtime.Worker` tiene su propio bloqueo, entrada inmutable,
checkpoint y publicación. Reutiliza el motor Gold existente. Live puede usar la
última referencia histórica confirmada; no espera a una nueva publicación histórica.
Si Silver ya archivó un dato pero Gold histórico todavía no acredita su incorporación,
Gold live conserva su evidencia. Cumplir ocho horas permite archivar; no autoriza
perder datos que un consumidor aún necesita.

Cada publicación Gold copia y verifica sus entradas. `CombinedQuery` fija una
publicación de cada almacén y reutiliza el motor de cálculo sobre hechos aceptados
y deduplicados para producciones/ventanas que cruzan la frontera. No suma scores ni
promedia porcentajes. Configuraciones incompatibles y contradicciones se explicitan.
Las lecturas y resultados tienen presupuestos configurados; superar un presupuesto
produce un error explícito y requiere ajustar capacidad/selección, no truncar datos.

## Archivos y configuración

La validación de la candidata y de `develop` usa
`.github/workflows/independent.yml` en runners hospedados. El contexto Gold y el
código fuente Gold quedan fijados por SHA, y el workflow verifica `GOLD_REVISION`
antes de construir `Dockerfile.independent`. La prueba Compose usa directorios
sintéticos, un proyecto temporal y la imagen local del job. El arranque del
servidor queda como operación manual separada.

| Pieza | Responsabilidad |
| --- | --- |
| `pipeline_policy.py`, `deployment/pipelines.example.json` | Rutas y política explícita: ventana, retención, latencia, turnos y presupuestos |
| `pipeline_intake.py`, `pipeline_bronze.py` | Descubrimiento, captura estable y feed Bronze verificable |
| `pipeline_lots.py` | Frontera global fija, propiedad, reintento horario y revisiones transferidas |
| `pipeline_runtime.py`, `workers.py` | Propietarios y programación por camino |
| `pipeline_publications.py`, `pipeline_view.py` | Silver confirmado y reconciliación sin leer controles ajenos |
| `pipeline_archive.py`, `pipeline_retirement.py` | Copia, recibos y retirada por el propietario live |
| Gold: `independent_inputs.py`, `evidence.py` | Puente desde publicaciones, linaje y evidencia autocontenida |
| Gold: `independent_runtime.py`, `combined_query.py` | Propietarios Gold y consulta conjunta |
| `deployment/independent.py` | Prevalidación y generación de Compose; nunca inicia contenedores |
| `deployment/independent_permissions.py` | Permisos de raíces nuevas; nunca chown recursivo |

Los ejemplos no son una configuración aprobada del servidor. Copiar y revisar:

- `pipelines.example.json` como `pipelines.json`: doce rutas internas disjuntas;
  política de 600 s, 28 800 s y 300 s conforme al acuerdo. Todos los campos se exigen.
- Configuración nativa TOML como `source.toml`: planta, orígenes, líneas, codificación,
  topología y reglas actuales. Sus rutas son rutas internas del contenedor. El runtime
  sustituye los destinos por los del camino y conserva las reglas de negocio.
- `customer_score.json` y el bundle acreditado de catálogos como `catalogs.json`,
  con el mismo formato usado por el publicador Gold vigente. No se generan defaults
  industriales ni decisiones manuales a partir de ausencia de ficheros.
- `api.env`: configuración vigente de autenticación y límites de consulta. Compose
  establece los dos destinos Gold. Los secretos permanecen fuera del repositorio.
- `independent.example.json` como `site.json`: binds reales, nombres nuevos de
  volúmenes, UID/GID, CPU/RAM/PID/scratch por proceso, puerto y digest real de imagen.
  El digest de ceros es un marcador de ejemplo, no una imagen desplegable.

Las CPUs reservadas a ingesta live no se asignan a ningún otro servicio del plan.
La prevalidación comprueba CPU y suma de memoria frente a `docker info`. El disco,
el motor Docker y otros programas del host siguen siendo recursos compartidos:
separar contenedores elimina los bloqueos entre escritores, pero no acredita por
sí solo el plazo de cinco minutos en cualquier hardware.

Preparación de solo lectura/generación, sin arranque:

```powershell
$hostInfo = docker --context desktop-linux info --format '{{json .}}'
[System.IO.File]::WriteAllText("$PWD/host.json", $hostInfo, (New-Object System.Text.UTF8Encoding $false))
uv run --locked python -m deployment.independent --site site.json --pipelines pipelines.json --host-info host.json --output compose.independent.json
docker --context desktop-linux compose -f compose.independent.json config --quiet
```

Usar UTF-8 sin BOM para JSON cuando se escriba desde Windows PowerShell 5.1.
No se han ejecutado comandos `up`, activación, sustitución ni migración del servidor.

## Migración y aceptación que pertenecen a la activación posterior

1. Inventariar y preservar el volumen actual y sus publicaciones, decisiones,
   registros de asociaciones, revisiones abiertas y configuración. Obtener una copia
   consistente mediante el procedimiento de backup vigente; no copiar SQLite activos
   a mano ni habilitar CDF retroactivamente sobre las tablas antiguas.
2. Revisar un manifiesto de migración por familia/origen: claves, filas, versiones,
   revisiones, cobertura y casos abiertos. El traslado de decisiones a los dos controles
   privados requiere una semilla verificada; no copiar ciegamente un control entero.
   No se ha ejecutado este paso ni existe una adopción implícita en el arranque nuevo.
3. Usar volúmenes nuevos. La reconstrucción histórica desde capturas conservadas usa
   los contratos actuales; cualquier evidencia/decisión que no pueda reconstruirse
   debe figurar en el manifiesto y trasladarse de forma explícita antes del corte.
4. En entorno de aceptación, mantener histórico activo y después bloquear/detener
   solo su escritor. Introducir ficheros estables; medir externamente hasta la publicación
   Silver confirmada. Exigir <=300 s y comprobar datos, omisiones justificadas y recursos.
5. Reiniciar cada camino por separado; comprobar recibos tras fallos entre etapas y
   correcciones durante el solapamiento. Contrastar Gold cruzando la frontera contra
   el cálculo completo con idéntica configuración, incluidos cambios de clave y DST.
6. Cambiar consumidores únicamente después del recibo de migración y aceptación
   conforme. Conservar el almacén anterior para rollback. No ejecutar los DAG antiguos
   sobre los nuevos destinos: los propietarios son los cuatro trabajadores nuevos.

Las pruebas sintéticas incluidas ejercitan bloqueo histórico, publicaciones reales
Delta y métricas cruzadas. No equivalen a certificar capacidad y latencia en el host
operativo. Esa medición y la activación están deliberadamente pendientes.

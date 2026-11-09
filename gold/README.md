# Publicador Gold: validación local

Código en `backend/app/gold`, con lector API opcional mediante
`APP_DATA_BACKEND=gold`. El modo histórico sigue siendo el predeterminado. No
se han desplegado servicios ni modificado la ingesta activa del servidor.
El único cambio del recorrido histórico extrae sus funciones escalares a
`app/domain/score_math.py` y fija el orden MIA para evitar empates y redondeos
dependientes del orden de grupos de Polars. No mantiene otra copia de las fórmulas.

## Preparación

Usar Python 3.11 para el publicador y las comparaciones, como la app histórica
validada. Instalar el extra `gold` del proyecto mediante el gestor habitual;
Delta está fijado a 1.6.6. El productor auxiliar se ejecuta con Python 3.12 y el
paquete de ingesta existente, que requiere esa versión. Cambiar el runtime de
cálculo requiere repetir la equivalencia: el orden/suma de flotantes importa.

Ejecutar desde el proyecto con `PYTHONPATH=backend` (en PowerShell:
`$env:PYTHONPATH=(Resolve-Path backend).Path`). No definir destinos productivos
implícitos. Los datos y resultados se guardan en `var/`, excluido de Git.

## Entregas y comandos

1. Referencia histórica real, inmutable:

   ```text
   python -m app.gold freeze-legacy --data-root RUTA_DATOS --config-root config --output RUTA_NUEVA --production AV23_PROD_001 --production AV32_PROD_001
   python -m app.gold verify-legacy --reference RUTA_REFERENCIA
   python -m app.gold verify-mia-parity --reference RUTA_REFERENCIA
   ```

   El primer comando rechaza sobrescribir un corpus existente. Guarda hashes,
   configuración, cantidades MIA y resultados de cuatro consultas, incluyendo
   filtros de máquinas presentes en los datos. El comparador MIA reconstruye
   cantidades y cálculos con el código Gold; no usa como entrada el score esperado.
   Conserva explícitamente la población LEGACY solo para esa prueba; no es un
   fallback de producción para modelo/segmento desconocidos.

2. Publicación de entrada confirmada, en una réplica aislada:

   ```text
   python -m app.gold local-replay --ingestion-config CONFIG_TEST.toml --publication-root RAIZ_REPLICA
   ```

   Este comando necesita Python 3.12 y ambos paquetes en `PYTHONPATH`. El TOML es
   el de la ingesta, con environment=test, topología activa y raíces nuevas para
   Bronze/Silver/control, todas dentro de RAIZ_REPLICA y fuera de los CSV/código de
   ingesta. Utiliza sus contratos y decisiones existentes; el consumidor Gold no
   abre sus SQLite. La proyección auxiliar mínima captura parámetros, gotas y
   evidencia ZC, rechazando contradicciones; no registra esas fuentes en el worker
   activo del servidor. La exportación tiene un presupuesto explícito de filas y
   se identifica como `offline_snapshot`.

3. Catálogos controlados y publicación Gold:

   Preparar el bundle con `app.gold.catalogs.prepare_bundle`, los libros de
   referencia versionados, perfiles existentes y reglas de zona aprobadas.
   `model_segments=[]` conserva P01 pendiente. Para el Excel recibido, ejecutar
   `import-segments` según `docs/CATALOGO_SEGMENTOS.md`: clasificación acreditada,
   DAR específicos pendientes y perfil común únicamente mediante selección
   explícita, con estado provisional conforme a D18.
   El bundle usa `quality-catalog-bundle-v1`; `physical_links` es una ampliación
   opcional para correspondencias físicas aceptadas con vigencia. La falta de
   prueba física se declara no concluyente y conserva la aproximación autorizada,
   sin deducir una parada por ausencia de rechazos.

   ```text
   python -m app.gold build --settings gold/settings.example.json --cutoff 2026-10-14T09:00:00
   python -m app.gold inspect --root var/gold_validation/gold-application-v3-release
   python -m app.gold validate-gold --root var/gold_validation/gold-application-v3-release
   ```

   Las rutas del JSON se resuelven respecto al propio JSON. Adaptar las rutas y
   el corte a la réplica concreta. `cutoff` es el corte industrial efectivo local;
   no se convierte UTC implícitamente. Un nuevo dato/corte relevante genera otra
   publicación, no otra fila visible por cada actualización del mismo punto.

   **Contratos dimensionales v3:** `python -m app.gold contracts` expone las
   36 tablas con tipos, nulabilidad, grano, claves y dependencias. Se añade
   `zc_production_links`, evitando inferir la relación de una rueda compartida
   por coincidencia de códigos. El artefacto es `gold/contracts-v3.json`;
   `gold/TABLES.md` resume las tablas. Los scores fijan su publicación base.
   El publicador valida referencias y conciliación antes de escribir y fija
   versión/huella de contrato en el manifiesto. Una revisión de padres exige
   incluir sus derivadas. El lector puede exigir el contrato completo mediante
   `Snapshot.open(..., require_gold_contract=True)`.

   Usar un destino v3 nuevo: no se migra ni sobrescribe automáticamente un
   almacén de 29/35 tablas o con otra huella. Las publicaciones anteriores siguen
   legibles con su lector anterior. Las dimensiones de máquina, modelo y defecto
   conservan versiones de atributos y tiempo de registro; no se inventan fechas
   de vigencia del catálogo. Un resultado cerrado conserva sus claves previas.

4. Pruebas:

   ```text
   uv run --extra gold pytest backend/tests -q
   ```

   Incluyen MAX antes de SUM, filtro antes del máximo, A/R abierto, unión temporal,
   capacidad, rueda compartida, población histórica, score completo y ausencia
   de total parcial. El publicador se prueba con fallos antes/después del commit
   y de la promoción, retirada acotada, lector fijado y restauración independiente.
   La suite completa exige 80 % de cobertura combinada de líneas/ramas. La CI
   instala el extra `gold` con uv 0.11.6 y conserva JUnit/coverage como artefactos.
   `gold/Dockerfile` prepara el ejecutable del publicador como usuario sin
   privilegios; requiere montar por separado entradas, salida y configuración.
   No incorpora los datos industriales al contexto de imagen.

## Garantías y límites actuales

- Entradas fijadas por UUID de tabla, versión, esquema, operación y justificante.
  Manifiestos/punteros con hash; dependencias incompatibles y archivos ausentes
  se rechazan. Una versión Delta alta no demuestra aceptación del negocio.
- Escritor único con bloqueo del sistema operativo. Intentos guardan hashes y
  justificantes pequeños; no duplican filas ni lotes como JSON de control.
- MERGE actualiza filas distintas, inserta nuevas y retira derivadas obsoletas
  dentro del ámbito declarado. Las versiones anteriores siguen disponibles.
  No se ejecuta VACUUM ni expiración automática de logs ni limpieza temporal.
- Contexto, hechos, cantidades y disponibilidad pueden publicarse aunque el score
  no sea calculable. Total y desglose oficiales requieren todas las bases,
  configuración y referencia compatibles. Un pendiente no se convierte en cero.
- Los resultados publicados de una fabricación cerrada no adoptan referencias
  nuevas automáticamente si sus datos propios no cambian. Las correcciones de
  datos aceptadas permiten una revisión trazada.
- MIA se lee por lotes, se indexa por producción/periodo y reutiliza ventanas
  solamente cuando coinciden sus entradas físicas. Los cambios de capacidad,
  atribución o datos invalidan la reutilización; cambiar parámetros no altera
  las cantidades físicas aceptadas. Se conservan límites explícitos de filas y
  memoria: esta entrega no promete procesamiento distribuido ni volumen ilimitado.
- `watch` rechaza una instantánea offline. El hook opcional de ingesta publica
  después de ciclos confirmados, con metadatos de descubrimiento efectivos.
  La exportación del ledger usa lotes y lectura JSON/CAS en streaming. Ver el
  procedimiento de activación y sus dependencias en `docs/OPERACION_GOLD.md`.
- Backup, verificación y restauración están implementados; no se ha configurado
  un destino independiente real. La restauración local prueba consistencia,
  sin acreditar pérdida cero ni recuperación ante pérdida física del servidor.

## Aplicación y operación

La API Gold está implementada y se activa explícitamente. Catálogo, score,
turnos, escenarios, disponibilidad y métricas leen una publicación fija por
consulta; los escenarios no escriben resultados. La caché incluye la publicación.
Los pendientes no generan ceros y la interfaz identifica DAR provisional.
Las reclamaciones sin fuente actualizada se muestran como desconocidas.

P01 y la aceptación industrial integral continúan pendientes de datos/Calidad.
El lector permite validar y mostrar disponibilidad sin autorizar por sí solo
scores oficiales de nuevas series. Ver `docs/OPERACION_GOLD.md` para ejecutar,
desplegar, supervisar, respaldar y recuperar, y para los bloqueos externos.

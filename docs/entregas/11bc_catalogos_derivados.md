# Entregas 11B/11C — Catálogos físicos y asociaciones v2

Implementación del **19/10/2026**, autorizada independientemente de la aclaración pendiente
de `ZF_OK`. El contrato completo permanece en el plan, 17.14–17.16. Esta entrega implementa
código y pruebas sintéticas; no reconstruye las tablas reales, no ejecuta una migración y
no configura GitHub o el servidor.

## 11B: dos catálogos temporales mínimos

Se incorporan `sip_maquina_moldeo_v1` y `sip_linea_zf_v1`, con captura Bronze estable,
soporte de nombres versionados/sobrescritos y tablas Delta independientes de planta.
Los campos no seleccionados permanecen en Bronze y no alteran el hash de negocio.

| Fuente | Campos Silver, en orden | Clave dentro de la planta |
| --- | --- | --- |
| SIP_MAQUINA_MOLDEO | id_maquina_fenix, nombre_maquina, vigente_desde, vigente_hasta, codigo_linea_zc | id_maquina_fenix + vigente_desde |
| SIP_LINEA_ZF | id_linea_zf_fenix, codigo_ramal, id_maquina_fenix, vigente_desde, vigente_hasta, origen_fenix | origen_fenix + id_linea_zf_fenix + vigente_desde |

IDs y códigos: int64; nombre/origen: string; vigencias: timestamp(us)/TIMESTAMP_NTZ.
Solo `vigente_hasta` admite nulo. Posiciones CSV y significado en plan 17.14.2. La política
horaria sigue siendo la explícita de ensayo `local_naive_test_v1`, sin asumir UTC.

Un mismo ID/vigencia idéntico no produce otro registro ni commit Delta. Un cierre único
compatible actualiza únicamente el final nulo y conserva antes/después. Una nueva vigencia
no solapada se inserta; ausencia en el siguiente archivo no da de baja ni reabre registros.
Variantes contradictorias, cierres distintos, valores inválidos y solapamientos conservan
su evidencia para revisión; retirar una alternativa del CSV no resuelve el conflicto.

Cada ramal requiere una máquina aceptada que cubra su vigencia y corresponda al código
de línea configurado. Si falta, queda pendiente, con candidatos íntegros y referencia a
Bronze; puede reintentarse tras recibir el catálogo de máquinas aunque haya desaparecido
el archivo vivo. Las incidencias de calidad se propagan a los derivados afectados.

El control específico registra intenciones por clave, hashes antes/después y operación,
versiones Delta, evidencia y confirmaciones. Recupera antes de procesar otra actualización;
reconoce el commit ya escrito si faltó su acuse. No adopta tablas ajenas ni actualiza
registros al consultar. Los dos catálogos se publican por separado; no se presume una
transacción global entre ellos y los derivados.

## 11C: catálogo y asociación por máquina física

Prerrequisito incorporado: `mp9_cambio_modelo_v2` recupera `ID_MAQUINA` como quinto campo
de negocio. Es obligatorio/invariante por identidad; ZF solo enlaza con ZC de igual máquina,
modelo y origen. Los ID de producción conservan el formato `fenix-production-v1:...`;
la versión del contrato no cambia su identidad.

`catalogo_producciones_v2` añade `ID_MAQUINA` antes del último campo `ID_ST1_REL`.
Con topología activada, valida la máquina vigente para el periodo ZC y su código de línea.
La búsqueda devuelve su denominación y estado derivado: abierta en ZC, pendiente de cierre
ZF o cerrada. Cobertura pendiente/frescura se informan aparte. La ausencia de cierre válido
mantiene abierta la producción; no requiere consulta SQL ni otra señal de marcha.

`asignacion_produccion_ramal_v2`, regla `temporal_assignment_v2`, deriva episodios desde
la intersección de la presencia ZF declarada por MP9, vigencia de máquina y relación del
ramal. Conserva evidencia de snapshots y decisiones; no deduce correspondencias por letras,
modelo, orden de filas o coincidencia de fechas. El algoritmo se aplica a todas las máquinas
configuradas, incluidas tándem con dos producciones concurrentes del mismo modelo.

Los episodios automáticos tienen un ID estable derivado de producción, registro ZF y claves
de ambas vigencias físicas. Un cierre posterior actualiza el episodio conservando ID,
revisión consecutiva y hash de la decisión anterior; replay sin cambios no escribe Delta.
Una corrección operativa explícita usa ese mismo ID, revisión consecutiva, `previous_hash`,
actor/motivo/evidencia y queda preservada en posteriores derivaciones. No añadir una segunda
asociación solapada para corregir la primera. El actor declarado no es autenticación.

Se evalúa el periodo completo de muestra. **Convención acordada:** una muestra de diez
minutos que comienza en la producción saliente y cruza su cierre ZF se atribuye íntegramente
a esa producción; estado `TRANSITION_TO_OUTGOING`. Ejemplo: 07:20–07:30, cierre 07:23:14,
se asigna a la saliente. También se permite cuando la entrante todavía está bloqueada por
calidad: no exige publicar esa candidata. No prolonga la saliente a muestras posteriores,
no reparte contadores y no multiplica las inspeccionadas repetidas por distintos defectos.
Un cambio/contradicción de topología que impida verificar la máquina de toda la muestra
continúa pendiente, sin forzar esa convención.

Los AR sin discriminante físico suficiente pueden continuar ambiguos. CONT_ZC requiere
presencias ZC revisadas para compartir su rendimiento; los vínculos ZF no prueban presencia
ZC ni flujo efectivo. No se añaden reglas de minutos-cavidad o score en esta entrega.

**ZF_OK no cambia:** variantes 0/1 para una misma identidad permanecen en revisión, sin
preferir 1, el último archivo o la última fila. Esto no bloquea máquinas/producciones
independientes ni las muestras atribuibles a una producción saliente válida.

## Configuración y ejecución posterior en ensayo

Ejemplos nuevos: `config/sip_maquina_moldeo.example.toml` y
`config/sip_linea_zf.example.toml`. Los ejemplos de catálogo/asociaciones también activan
`topology_enabled = true` y declaran `[line_code_by_origin]` para cada origen. Mantener
`[line_by_origin]` en los derivados. Los códigos deben corresponder al catálogo; no se
deducen de los nombres. Cambiar estos mapeos deja la publicación anterior `STALE`.

El modo `topology_enabled = false` mantiene el flujo manual de asociación para ensayos
anteriores; no acredita que esté aplicándose la resolución física v2. No mezclar modos
entre catálogo y asociaciones. Los contratos MP9/catálogo son v2 en ambos modos.

En el siguiente ensayo del servidor, configurar **todas las familias** con las mismas
raíces nuevas de Bronze/Silver/control; los ejemplos anteriores de otras familias deben
adaptarse. El código rechaza bindings/esquemas v1 incompatibles, no los migra al instalarse.
Conservar las tablas, auditorías y resultados históricos anteriores.

Orden de ejecución explícita, una familia por configuración/llamada:

1. Cuatro diccionarios y `SIP_MAQUINA_MOLDEO`.
2. `SIP_LINEA_ZF` y `MP9_CAMBIO_MODELO`, tras sus dependencias.
3. `CATALOGO_PRODUCCIONES`; las cuatro fuentes de observaciones tras sus diccionarios.
4. `associations build`, cuando todos los snapshots estén confirmados.
5. `catalog search` / `associations inspect` para lectura y explicación de pendientes.

Las once fuentes se ingieren con el comando existente
`python -m ingesta_delta_lake ingest --config <ruta-absoluta>`.
Los comandos de derivados conservan las interfaces de las fichas 06/11. Instalar o actualizar
código no ejecuta estas operaciones, no vacía tablas ni inicia un proceso programado.

## Validación y límites de aceptación

Pruebas sintéticas de schemas, invariantes MP9, referencias, vigencias, repetición sin commit,
conflictos persistentes, recuperación antes/después del commit y acuse, configuración,
concurrencia del mismo modelo en dos partes, transición de cinco filas, ZF_OK contradictorio,
cierres tardíos y corrección explícita. Se mantiene la suite de regresión del proyecto.

No se han recalculado los conteos reales v1 ni publicado resultados reales v2. Por decisión
del usuario: **código/pruebas locales → GitHub/CI → reconstrucción/conciliación separada en
el servidor → piloto con llegadas reales**. La validación continua e integración de la app
son posteriores. El horario productivo, almacenamiento permitido y rendimiento a escala
de servidor siguen necesitando validación; las pruebas sintéticas no la sustituyen.

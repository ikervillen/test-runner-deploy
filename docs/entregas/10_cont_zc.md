# Entrega 10 — CONT_ZC

Estado: implementación local de ensayo completada. Contrato `cont_zc_v1`,
[sección 4.9 del plan](../PLAN_DELTA_LAKE.md).
Completa las nueve fuentes de origen seleccionadas; no implica integración de la app ni
despliegue, que siguen pendientes junto con las relaciones derivadas.

## Esquema conservado

Se conservan los 17 campos originales en su orden, todos no nulos, más origen técnico al final:

| Posición | Campo | Tipo Arrow / Delta |
| --- | --- | --- |
| 1 | MAQ_IS | int64 / BIGINT, código original |
| 2 | FECHA | timestamp(us) sin zona / timestamp_ntz |
| 3 | CONT_ZC_1 | int64 / BIGINT, no negativo |
| 4 | CONT_ZC_2 | int64 / BIGINT, no negativo |
| 5 | MAQUINA_A | int64 / BIGINT, no negativo |
| 6 | MAQUINA_B | int64 / BIGINT, no negativo |
| 7 | TUNEL | int64 / BIGINT, no negativo |
| 8 | RUEDA | int64 / BIGINT, no negativo |
| 9 | IMP_ARCA | int64 / BIGINT, no negativo |
| 10 | TEORICO_A | int64 / BIGINT, no negativo |
| 11 | TEORICO_B | int64 / BIGINT, no negativo |
| 12 | EXPULSOR | int64 / BIGINT, no negativo |
| 13 | CONT_ZC_4 | int64 / BIGINT, no negativo |
| 14 | VEL_A | decimal128(12,2) / DECIMAL(12,2), no negativo |
| 15 | VEL_B | decimal128(12,2) / DECIMAL(12,2), no negativo |
| 16 | CONT_ZC_3 | int64 / BIGINT, no negativo |
| 17 | SALIDA_MAQ_IS | int64 / BIGINT, 0 o 1 |

El CSV usa `;`, sin cabecera, separador final opcional. Los vacíos obligatorios, errores de
estructura y tipos/rangos inválidos conservan evidencia en revisión, sin rellenar o evolucionar
esquemas automáticamente. Los contadores no pasan por float ni se tratan como acumulados:
una disminución entre periodos no provoca restas, correcciones o descarte.

Velocidades exactas con punto decimal, sin exponente, dentro de 0–9999999999.99. Se admiten
representaciones equivalentes como `80`, `80.00`, `80.010` o ceros iniciales/adicionales,
pero no decimales significativos fuera de escala ni redondeos. Se normalizan a texto con dos
decimales para comparación/auditoría y a Decimal exacto para Arrow; en Delta son DECIMAL,
no string ni float. El hash incluye los 17 valores normalizados, sin metadatos de recepción.

Fechas día/mes/año, horas equivalentes de una/dos cifras y política explícita de ensayo
`local_naive_test_v1`. No se asigna UTC ni se resuelven DST implícitamente; política productiva
pendiente. No se convierte MAQ_IS mediante equivalencias de ID_LINEA_ZC del Excel histórico
ni se exige que su valor coincida numéricamente con el nombre de carpeta/origen.

## Identidad, conflictos y escritura

`K_ZC = (origen, MAQ_IS, FECHA)`. Planta fija en rutas/configuración/control. La clave se compara
completa; la huella de negocio no la sustituye. Otra máquina/fecha/origen con iguales valores
es otra observación, no un duplicado numérico. No se clonan filas por producción tándem.

Igualdad de todos los campos → coincidencia sin escritura. Otro contador, velocidad o
indicador para la misma clave → candidata íntegra en revisión, conservando lo aceptado, con
clave, antes/candidatas, hashes, diferencias y raw/origen/fila/revisión. Una ambigüedad inicial
no elige primera/última/mayoritaria y no se resuelve al omitir una alternativa. Una versión
inválida con clave interpretable retiene otras candidatas de esa clave; otras unidades válidas
pueden avanzar con estado parcial. No hay aún comando de corrección humana.

Silver: `<silver_root>/<planta>/cont_zc`. Control: `<control_root>/control-cont_zc.sqlite`.
`measurement_intents` prepara por clave operación, candidata/evidencia y hash antes del append;
después confirma versión una sola vez. `issues` conserva casos semánticos y `issue_evidence`
sus recepciones/lotes. Un reintento idéntico no duplica el caso. La primera inserción y su
evidencia quedan inmutables. No hay rama de actualización automática, overwrite o borrado
por ausencia. Sin novedades no se invoca al escritor ni se generan commits vacíos.

Se reutilizan captura estable Bronze, nombres fijos/versionados mixtos, bloqueo de escritor,
verificación de snapshot y reconciliación por operación/contenido. Un lote incierto conserva
su revisión preparada aunque cambie el CSV vivo; se recupera antes de procesar otra. Si Delta
confirmó pero falló el acuse SQLite, se confirma sin repetir. Una contradicción de negocio
no se arregla reintentando. No se asume atomicidad conjunta entre SQLite y Delta.

## Rendimiento y consulta

Configurar `config/cont_zc.example.toml` fuera del código. Ejecución explícita:

```powershell
python -m ingesta_delta_lake ingest --config 'C:/Ingesta_Delta_Lake_ensayo/zc.toml'
python -m ingesta_delta_lake zc-yield --config 'C:/Ingesta_Delta_Lake_ensayo/zc.toml' --origin AV23 --machine 23 --start '2026-10-13T23:50:00' --end '2026-10-14T00:00:00'
```

La consulta es de solo lectura, sin controles/tablas creados implícitamente, y exige snapshot
confirmado y ausencia de operaciones técnicas pendientes. Devuelve resumen y desglose por
origen/máquina. `--origin`, `--machine`, `--start` y `--end` son opcionales. Límites locales ISO
sin zona ni fracciones; start debe preceder a end. Selecciona finales de muestra en
`(start, end]`: el ejemplo devuelve el punto de las 00:00, representativo de 23:50–00:00.
No inventa ceros, interpola huecos, extiende un periodo desde la última muestra ni reparte
cantidades en una frontera. No asigna estos periodos todavía a producciones o turnos.

Sobre las observaciones resueltas con teórico positivo:

`100 × SUM(RUEDA) / SUM(TEORICO_A + TEORICO_B)`.

Numerador y denominador usan el mismo conjunto elegible; no se promedian porcentajes,
restan contadores ni cambia RUEDA por EXPULSOR/A+B según SALIDA_MAQ_IS. Se devuelven sumas
enteras y fracción del porcentaje, con presentación a cuatro decimales (redondeo decimal
HALF_EVEN explícito). El porcentaje no se guarda en las filas de origen ni se conecta al score.

Con teórico cero se conserva la fila y se excluye de ambos lados del cociente. Se informa
cuántas y cuánta rueda se excluye. Sin denominador elegible, rate_percent es nulo y la consulta
informa NOT_EVALUABLE, no 0/100/infinito ficticios. Es una falta de evaluabilidad de métrica,
no un error de conservación de fuente. La ingesta puede quedar completa y el rendimiento no
ser evaluable. Observaciones superiores al 100 % se conservan y señalan mediante indicadores,
sin recortar o reemplazar valores ni imponer una relación de esquema rueda≤teóricos.

Una revisión conocida excluye del cálculo la observación aceptada afectada; un error sin
clave identificable retiene el origen para el cálculo. Las sumas devueltas pueden ser solo
parciales y el estado lo señala: source_complete es global respecto a incidencias conocidas,
también al filtrar un origen sano. La cobertura es **solo de muestras observadas**, con
conteos/primer/último final y periodo nominal de diez minutos; no acredita continuidad, todos
los ciclos esperados ni completitud de una producción. No se confunde éxito de tipos/ingesta
con ausencia de huecos de captura. El agregador rechaza claves físicas duplicadas en su entrada.

CLI: PARTIAL y NOT_EVALUABLE devuelven 2; NO_INPUTS/NO_OBSERVATIONS, 4; fallo técnico, 3;
OK/APPLIED/NO_CHANGE, 0. No se crea un porcentaje para evitar un estado de no evaluable.

El alcance es máquina/periodo. La asignación compartida del mismo rendimiento a producciones
tándem, el tratamiento de fronteras y la consulta conjunta sin doble conteo se implementarán
en la entrega 11. No se reparten contadores físicos A/B o entre modelos. Se verificó en modo
lectura la fórmula y elegibilidad del backend; sus equivalencias/enriquecimientos, producciones,
turnos, pesos y contribución al score se validarán antes de integrar Delta.

## Conciliación real del 16/10/2026

Diez CSV de AV21, AV22, AV23, AV31 y AV32 de los días 13/14: **827 filas y 827 claves**.
Comparación independiente csv/datetime/int/Decimal de los 17 campos y origen contra Delta:
igualdad íntegra. Ambas columnas de velocidad verificadas como decimal128(12,2).
Sin conflictos, inválidos o teóricos cero en esta muestra; los ceros y su exclusión se prueban
con casos sintéticos.

| Origen | Observaciones | Rueda elegible | Teóricos elegibles | Rendimiento observado | Puntos >100 % |
| --- | --- | --- | --- | --- | --- |
| AV21 | 170 | 146.630 | 350.116 | 41,8804 % | 4 |
| AV22 | 169 | 178.772 | 280.182 | 63,8057 % | 39 |
| AV23 | 148 | 217.005 | 236.410 | 91,7918 % | 39 |
| AV31 | 170 | 440.755 | 448.822 | 98,2026 % | 11 |
| AV32 | 170 | 473.936 | 496.418 | 95,4712 % | 42 |

Son ventanas de datos disponibles, no resultados de una producción concreta. Las 135
observaciones superiores al 100 % permanecen intactas y señaladas; no se clasifican como
errores de tipo ni se corrigen por inferencia. La primera observación AV23 del día 14 tiene
rueda 1583, teóricos 1614+0 y rendimiento 98,0793 %, manteniendo expulsor 1613 e indicador 1.

Replay NO_CHANGE, cero inserciones, versión 0 y mismos hashes de todos los archivos Delta.
Dos intentos, veinte recepciones, 827 intenciones confirmadas y ninguna incidencia de ingesta.
Los diez CSV originales conservan sus hashes; consultar rendimiento no añade intentos.
Informe/datos de ensayo fuera del checkout, bajo
`LOCALAPPDATA/Ingesta_Delta_Lake/validation/20261016T122644Z-cont-zc-2cbc95b2/`.
Primera carga 0,76 s y replay 0,62 s en este equipo; no es un benchmark de capacidad productiva.

Las 60 pruebas nuevas cubren precisión/escala decimal, representación equivalente, conservación
de columnas, fórmula ponderada y elegibilidad/ceros, valores superiores al 100 %, conflictos,
nombres mixtos, publicaciones parciales, recuperación y consulta por finales de muestra sin
escrituras. Resultado: **455 pruebas superadas y cobertura de líneas/ramas del 92,81 %**,
manteniendo las 395 anteriores; Ruff y formato pasan. Windows, Python 3.12.14, deltalake 1.6.6
y Arrow 23.0.1. Sin dependencias nuevas ni cambios del lockfile; CI remota aún no ejecutada.

Las nueve fuentes quedan implementadas en ensayo. Permanecen pendientes la política temporal
productiva, optimización del control/escaneo observada en la entrega 08, asociación a producciones
y validación/integración de consumo. No se ha iniciado GitHub ni despliegue remoto.
Siguiente unidad del plan: entrega 11, asociación temporal producción–ramal.

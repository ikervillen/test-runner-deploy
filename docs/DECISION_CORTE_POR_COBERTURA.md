# Decisión: el corte de Gold lo determinan los datos acreditados

Acordado el 2026-11-09. Sustituye al corte fijo "hora actual menos una hora"
como regla de publicación desatendida.

## Regla

Gold calcula, para cada producción, hasta el último corte que Silver pueda
acreditar con todas las fuentes necesarias durante el intervalo de cálculo.
La hora de ejecución indica cuándo se intenta publicar; el corte lo determinan
los datos confirmados, sin aplicar una hora fija de desfase.

- Si una producción tiene cobertura hasta las 11:50, publica hasta las 11:50.
- Si a otra le faltan los contadores desde las 11:20, conserva su último
  resultado completo, muestra hasta qué hora está calculado e indica qué fuente
  falta.
- La hora del último registro, por sí sola, no acredita la cobertura del
  intervalo: la acreditación debe cubrir el intervalo completo.

## Orquestación

- **Cuándo se evalúa (disparo):** cuando el worker publica una nueva versión de
  Silver, con el reloj de 10 minutos como respaldo. Se implementa después de la
  comprobación, con datos reales de frecuencia de publicación.
- **Si se publica (comprobación):** una tarea previa a la publicación lee la
  cobertura de la versión publicada y publica, espera o marca pendiente. Ambos
  disparos desembocan en la misma comprobación.
- Se mantiene `max_active_runs=1` y la idempotencia `UNCHANGED` de Gold.

## Umbrales de referencia

| Situación | Valor | Efecto |
|---|---|---|
| Espera dentro del corte, solo si el corte entero está bloqueado | 8 min | Reintenta la comprobación; después, pendiente |
| Corte bloqueado | 30 min | Aviso |
| Corte bloqueado | 60 min | Crítico |
| Ventana sin cobertura | 24 h | Pasa de "en espera" a "incompleta por fuente X" |

Si parte de las ventanas tiene cobertura, se publica y el resto queda pendiente.
Los valores definitivos salen de medir en develop.

## Decisiones posteriores (2026-11-09)

1. **Sin desfase fijo.** Desaparece la hora de retraso de
   `industrial_cutoff`. La hora programada, redondeada al escalón de 10 minutos
   (`app.gold.schedule.attempt_cutoff`), es solo el techo del intento; el corte
   de cada producción lo fijan sus datos.
2. **Orden de implantación.** Primero Gold con la cobertura que Silver publica
   hoy; después la acreditación del intervalo en el worker de
   `Ingesta_Delta_Lake`; por último el disparo por evento (opción C).
3. **Rama de trabajo:** `feat/gold-coverage-cutoff`, derivada de
   `feat/gold-develop-volume` (worktree `calidad_serie-develop`).

## Implementación

| Pieza | Dónde |
|---|---|
| Acreditación por producción, escalón, regla de 24 h | `app.gold.coverage` |
| Corte efectivo y fila `coverage:<producción>` en `publication_scope_status` | `app.gold.pipeline` |
| Informe previo a publicar (`READY`, `BLOCKED`, `NOTHING_NEW`) | `python -m app.gold coverage --settings ... --at ...` (`app.gold.readiness`) |
| Métricas `calidad_gold_coverage_blocked_productions`, `calidad_gold_coverage_max_lag_seconds`; alertas a 30/60 min | `app.gold.operations`, `ops/gold-alerts.yaml` |
| Espera de 8 min solo si todo el corte está bloqueado | sensor `coverage_ready` del DAG `gold_publish` |
| Hasta qué hora está calculada cada producción y qué falta | `/api/gold/status` (`coverage`) y aviso en la interfaz |

La fila `coverage:<producción>` lleva `period_end` = último corte calculado,
`estado` (`READY`, `WAITING_DEPENDENCY`, `INCOMPLETE`) y `reason_code`
`COVERAGE:<familias que faltan>` o `COVERAGE:INTERVAL` cuando todas las
familias existen pero ninguna acredita el siguiente escalón. El corte nunca
retrocede respecto a la publicación anterior.

## Punto abierto

Origen de la acreditación: hoy Silver deriva `covered_until` de la fecha del
último registro leído. La regla exige que el worker de ingesta acredite el
intervalo completo; Gold ya consume `covered_from`/`covered_until` por familia
y origen, así que ese cambio no toca el cálculo, solo la calidad de la
evidencia.

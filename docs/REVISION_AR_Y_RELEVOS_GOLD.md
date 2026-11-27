# AR, relevos y revisión de cierres Gold

## Orden de revisión

1. Acreditación de entregas vacías de Silver (#110).
2. Identidad de la unidad AR y atribución en tándem.
3. Selección del cierre horario y conservación de hechos durante el relevo.
4. Componentes independientes y compatibilidad del contrato.
5. Reevaluación acotada y simulación por pin original.
6. Presentación de estados y compatibilidad con la vista de modelos activos (#111).

La rama de despliegue fija la revisión que contiene la aplicación Gold. El pin
se actualizará tras la revisión matemática y la autorización. Se utiliza la
generación Live actual y se mantienen T0, Silver y las retenciones.

## Comportamiento

- Un AR 99Z atribuido usa planta, origen, modelo acreditado y número de molde.
  El evento conserva su cavidad original. Los solapamientos comparten la unión
  temporal por unidad dentro de cada agrupación. El denominador procede de la
  capacidad acreditada.
- Si dos decisiones aceptadas reclaman el mismo episodio de un tándem (unidad,
  código, comienzo y zona), el cálculo registra la atribución pendiente. Los
  modelos diferentes mantienen identidades separadas. La continuidad entre 99Z
  y cavidad física exige el mismo episodio y el tramo acreditado.
- La producción en ZF al comienzo de la hora conserva el cierre de un relevo
  inequívoco. El fin ZF fija el corte. La entrante conserva sus hechos.
  La API y el planificador comparten la selección. Los cambios múltiples o
  incompatibles requieren una política adicional.
- Los resultados parciales llevan membresía de componentes y peso evaluado.
  La API expone el subtotal por separado y deja vacíos el total oficial, el
  normalizado y la clasificación. El almacenamiento mantiene las columnas.
- Un agregado que atraviesa el corte se conserva según su atribución. Si no se
  puede aislar la parte anterior al corte, el componente queda pendiente.
- Se mantienen las cuotas de 12 cierres recientes y 2 antiguos por ciclo. La
  revisión antigua exige verificar la configuración original. Los cierres
  anteriores al registro de ese identificador necesitan el recibo del run.

## Simulación de los 16 cierres

En el servidor Windows, desde una copia de la rama final y con el informe
Fenix-AV31-datos.json, ejecutar el diagnóstico. Usa la imagen local de Gold
Live, un contenedor sin red y el volumen montado solo lectura. Los temporales
quedan en tmpfs. Conviene ejecutarlo fuera del pico de publicación.

    .\scripts\Invoke-FenixArDryRun.ps1 -Informe 'C:\Users\rdtiv001\fenix-diagnosticos\Fenix-AV31-datos.json' |
        Set-Content -LiteralPath '.\AV31-revision-seca.jsonl' -Encoding UTF8

La simulación verifica ámbito, corte efectivo, evidencia, recibo y configuración.
Usa referencias del run original y datos elegibles hasta el corte. Si falta una
pieza, devuelve NO_VERIFICADO. La salida incluye bases, componentes, motivos
residuales y tiempo. Los cierres legados deben revisarse uno por uno antes de
preparar una operación de escritura selectiva.

## Despliegue y vuelta atrás

Tras aprobar los resultados, fijar el commit Gold final en la rama de despliegue.
Probar antes una copia local de los pines, un ciclo normal y la recuperación de
una ventana elegida. Registrar la revisión de la imagen y el pin publicado
anterior. Actualizar conjuntamente el escritor Live y el lector compatible,
con la misma generación y History fuera del rollout.

Validar las siete posiciones, motivos y cortes, componentes recuperados y
totales. Comparar tiempo, filas, fragmentos, RSS, commits y bytes nuevos con la
línea base. Para volver atrás, restaurar las imágenes anteriores por el mecanismo
de rollout. Mantener un lector compatible para publicaciones con parciales
nuevos. Una recuperación o reposición de un puntero de producción requiere
una operación separada. Conservar publicaciones y evidencias previas.

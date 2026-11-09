# Contratos Gold ejecutables

Versión: quality-gold-dimensional-v3
Huella: cbe74286ffae344459c87d455b8cd0f3b80796c4a0a6f2b3e97f409c9dcb8039

| Tabla | Tipo | Grano | Clave | Dependencias |
|---|---|---|---|---|
| dim_plant | dimension | One qualified plant identity | plant_key |  |
| dim_date | dimension | One calendar date; no implicit timezone conversion | date_key |  |
| dim_machine | dimension | One physical machine attribute version and effective interval | dimension_key | dim_plant |
| model_segment_catalog | catalog | Catalog version and bottle model | catalog_version, codigo_modelo_botella |  |
| dim_model | dimension | One qualified model attribute version | dimension_key | dim_plant, model_segment_catalog |
| defect_catalog | catalog | Catalog version, code namespace and defect code | catalog_version, espacio_codigo, codigo |  |
| dim_defect | dimension | One qualified defect attribute version | dimension_key | dim_plant, defect_catalog |
| productions | context | One accredited physical fabrication | id_produccion | dim_plant, dim_date, dim_machine, dim_model, dim_defect |
| production_assignments | bridge | One accepted physical assignment episode | id_asignacion | productions |
| mia_inspections | fact | One accepted physical inspection observation | id_observacion | productions, production_assignments, dim_plant, dim_date, dim_machine, dim_model, dim_defect |
| inspection_bases | fact | One deduplicated physical inspection basis and selection | id_base | mia_inspections |
| mia_window_exposures | fact | Production, window and selection; one independent denominator | id_produccion, tipo_ventana, inicio, fin, seleccion_id | inspection_bases, productions |
| mia_rejections | fact | One accepted defect observation | id_observacion | productions, production_assignments, defect_catalog, dim_plant, dim_date, dim_machine, dim_model, dim_defect |
| mia_window_bases | fact | Production, window, selection and defect | id_produccion, tipo_ventana, inicio, fin, seleccion_id, espacio_defecto, codigo_defecto | mia_rejections, mia_window_exposures, dim_plant, dim_date, dim_machine, dim_model, dim_defect |
| production_capacity | context | One assigned-capacity episode | id_capacidad | productions |
| ar_window_exposures | fact | Production, window and selection; one capacity basis | id_produccion, tipo_ventana, inicio, fin, seleccion_id | production_capacity, productions |
| ar_type_catalog | catalog | Catalog version and event type | catalog_version, codigo_tipo_ar |  |
| ar_status_catalog | catalog | Catalog version and status code | catalog_version, codigo_status |  |
| ar_intervals | fact | One accepted attributable quality-event interval | id_tramo | productions, production_assignments, defect_catalog, ar_type_catalog, ar_status_catalog, dim_plant, dim_date, dim_machine, dim_model, dim_defect |
| dar_profile_members | bridge | Catalog, profile, group and defect membership | catalog_version, profile_id, espacio_codigo, codigo | defect_catalog |
| ar_window_bases | fact | Production, window, selection and category or DAR group | id_produccion, tipo_ventana, inicio, fin, seleccion_id, ambito, codigo_ambito | ar_intervals, ar_window_exposures, dar_profile_members |
| zc_observations | fact | One physical counter sample independent of concurrent productions | id_observacion | dim_plant, dim_date, dim_machine, dim_model, dim_defect |
| zc_production_links | bridge | One producer-accepted physical counter observation to production link | id_produccion, id_observacion | productions, zc_observations |
| zc_window_bases | fact | Origin, physical counter, window, cutoff and selection | id_base_zc | zc_observations, zc_production_links |
| mia_reference_sets | reference | One explicitly selected historical population | reference_id | productions, model_segment_catalog |
| mia_reference_members | bridge | Reference population and member production | reference_id, id_produccion | mia_reference_sets, productions |
| mia_reference_values | reference | Reference population and defect | reference_id, espacio_defecto, codigo_defecto | mia_reference_sets, mia_reference_members, mia_window_bases |
| quality_scores | fact | One result per production, window and selection in this publication | id_resultado | productions, mia_window_exposures, mia_window_bases, ar_window_exposures, ar_window_bases, zc_window_bases, mia_reference_values, dim_plant, dim_date, dim_machine, dim_model, dim_defect |
| aql_score_details | fact | Result and severity category | id_resultado, categoria | quality_scores |
| dar_score_details | fact | Result and DAR group | id_resultado, grupo_id | quality_scores, dar_profile_members |
| mia_score_details | fact | Result and defect | id_resultado, espacio_defecto, codigo_defecto | quality_scores, defect_catalog |
| production_calculation_state | control | One calculation-input fingerprint per production | id_produccion | productions |
| publication_scope_status | availability | One source, production, interval and reason | scope_id |  |
| quality_score_components | fact | Result and component | id_resultado, componente | quality_scores |
| segment_catalog | catalog | Catalog version and exact workbook family classification | catalog_version, segmento_calidad |  |
| zc_score_details | fact | One hot-end result detail | id_resultado | quality_scores, zc_window_bases |

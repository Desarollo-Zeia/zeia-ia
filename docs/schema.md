# Esquema de la base de datos `energy`

## Esquema `public` (63 tablas/vistas)

### `public.accounts_energymonitoringmodule` (BASE TABLE, ~10 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| name | character varying | no |  |
| description | text | sí |  |
| is_active | boolean | no |  |
| frontend_url | character varying | sí |  |
| icon_url | text | sí |  |
| level | integer | no |  |
| lft | integer | no |  |
| order | integer | no |  |
| parent_id | bigint | sí |  |
| rght | integer | no |  |
| tree_id | integer | no |  |
| display_name | character varying | no |  |

**PK:** id

**FKs:** parent_id → public.accounts_energymonitoringmodule.id

### `public.accounts_user` (BASE TABLE, ~43 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| password | character varying | no |  |
| last_login | timestamp with time zone | sí |  |
| is_superuser | boolean | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| first_name | character varying | no |  |
| last_name | character varying | no |  |
| document_number | character varying | sí |  |
| gender | character varying | sí |  |
| birthday | date | sí |  |
| age | integer | sí |  |
| email | character varying | no |  |
| phone | character varying | sí |  |
| is_active | boolean | no |  |
| is_staff | boolean | no |  |
| is_user_energy_monitoring | boolean | no |  |
| is_user_quality_air_auto | boolean | no |  |
| is_user_thermal_comfort | boolean | no |  |
| send_email | boolean | no |  |
| admission_date | date | sí |  |
| departure_date | date | sí |  |

**PK:** id

### `public.accounts_user_groups` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| user_id | bigint | no |  |
| group_id | integer | no |  |

**PK:** id

**FKs:** group_id → public.auth_group.id; user_id → public.accounts_user.id

### `public.accounts_user_user_permissions` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| user_id | bigint | no |  |
| permission_id | integer | no |  |

**PK:** id

**FKs:** permission_id → public.auth_permission.id; user_id → public.accounts_user.id

### `public.accounts_userenergymodule` (BASE TABLE, ~297 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| is_active | boolean | no |  |
| assigned_date | timestamp with time zone | no |  |
| module_id | bigint | no |  |
| user_id | bigint | no |  |

**PK:** id

**FKs:** module_id → public.accounts_energymonitoringmodule.id; user_id → public.accounts_user.id

### `public.accounts_userenterpriserole` (BASE TABLE, ~42 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| role | character varying | no |  |
| date_joined | timestamp with time zone | no |  |
| enterprise_id | bigint | no |  |
| user_id | bigint | no |  |

**PK:** id

**FKs:** enterprise_id → public.enterprises_enterprise.id; user_id → public.accounts_user.id

### `public.alerts_alert` (BASE TABLE, ~317,012 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| alert_status | character varying | no |  |
| timestamp | timestamp with time zone | no |  |
| value | double precision | sí |  |
| status | character varying | no |  |
| reported | boolean | no |  |
| reported_at | timestamp with time zone | sí |  |
| alert_threshold_id | bigint | no |  |
| reading_id | bigint | no |  |
| acknowledged_at | timestamp with time zone | sí |  |
| acknowledged_by_id | bigint | sí |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| notes | text | sí |  |
| whatsapp_reported | boolean | no |  |
| whatsapp_reported_at | timestamp with time zone | sí |  |
| energy_subtype | character varying | sí |  |
| fluctuation_subtype | character varying | sí |  |
| phase_type | character varying | sí |  |
| unbalanced_subtype | character varying | sí |  |
| email_summary_reported | boolean | no |  |
| email_summary_reported_at | timestamp with time zone | sí |  |
| power_subtype | character varying | sí |  |
| current_subtype | character varying | sí |  |
| current_phase | character varying | sí |  |

**PK:** id

**FKs:** acknowledged_by_id → public.accounts_user.id; alert_threshold_id → public.alerts_alertthreshold.id; reading_id → public.readings_reading.id

### `public.alerts_alertthreshold` (BASE TABLE, ~45 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| name | character varying | no |  |
| alert_type | character varying | no |  |
| threshold_value | double precision | sí |  |
| measurement | character varying | no |  |
| is_active | boolean | no |  |
| email_recipients | text | sí |  |
| electrical_panel_id | bigint | sí |  |
| energy_headquarter_id | bigint | sí |  |
| enterprise_id | bigint | sí |  |
| measurement_point_id | bigint | sí |  |
| created_at | timestamp with time zone | no |  |
| description | text | sí |  |
| modified_at | timestamp with time zone | no |  |
| whatsapp_recipients | text | sí |  |
| email_sending | boolean | no |  |
| whatsapp_sending | boolean | no |  |

**PK:** id

**FKs:** electrical_panel_id → public.enterprises_electricalpanel.id; energy_headquarter_id → public.enterprises_energyheadquarter.id; enterprise_id → public.enterprises_enterprise.id; measurement_point_id → public.enterprises_measurementpoint.id

### `public.alerts_commentalert` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| content | text | no |  |
| user_id | bigint | no |  |
| alert_id | bigint | no |  |

**PK:** id

**FKs:** alert_id → public.alerts_limitalert.id; user_id → public.accounts_user.id

### `public.alerts_currentstatusreportschedule` (BASE TABLE, ~34 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| report_time | time without time zone | no |  |
| email_recipients | text | no |  |
| is_active | boolean | no |  |
| alert_threshold_id | bigint | no |  |

**PK:** id

**FKs:** alert_threshold_id → public.alerts_alertthreshold.id

### `public.alerts_energythresholdprofile` (BASE TABLE, ~30 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| name | character varying | no |  |
| voltage_tolerance_percent | double precision | sí |  |
| nominal_voltage_v | integer | sí |  |
| cuf_limit_ratio | double precision | no |  |
| vuf_limit_ratio | double precision | no |  |
| thd_moderate_ratio | double precision | no |  |
| thd_critical_ratio | double precision | no |  |
| iqr_multiplier | double precision | no |  |
| extra_parameter_overrides | jsonb | sí |  |
| enterprise_id | bigint | sí |  |
| energy_headquarter_id | bigint | sí |  |
| measurement_point_id | bigint | sí |  |
| critical_voltage_tolerance_percent | double precision | sí |  |
| max_demand_kw | double precision | sí |  |
| contracted_power_kw | double precision | sí |  |
| installed_power_kw | double precision | sí |  |
| max_reactive_kvar | double precision | sí |  |
| min_reactive_kvar | double precision | sí |  |
| max_current_a_phase_a | double precision | sí |  |
| min_current_a_phase_a | double precision | sí |  |
| max_current_a_phase_b | double precision | sí |  |
| min_current_a_phase_b | double precision | sí |  |
| max_current_a_phase_c | double precision | sí |  |
| min_current_a_phase_c | double precision | sí |  |
| cuf_current_limit | double precision | sí |  |
| eppos_workday_lower | double precision | sí |  |
| eppos_workday_upper | double precision | sí |  |
| eppos_saturday_lower | double precision | sí |  |
| eppos_saturday_upper | double precision | sí |  |
| eppos_sunday_lower | double precision | sí |  |
| eppos_sunday_upper | double precision | sí |  |
| epneg_workday_lower | double precision | sí |  |
| epneg_workday_upper | double precision | sí |  |
| epneg_saturday_lower | double precision | sí |  |
| epneg_saturday_upper | double precision | sí |  |
| epneg_sunday_lower | double precision | sí |  |
| epneg_sunday_upper | double precision | sí |  |
| energy_thresholds_last_calculated | timestamp with time zone | sí |  |
| reactive_power_factor_limit | double precision | sí |  |
| thd_individual_limit | double precision | sí |  |
| thd_total_limit | double precision | sí |  |
| voltage_tolerance_max_percent | double precision | sí |  |
| voltage_tolerance_min_percent | double precision | sí |  |

**PK:** id

**FKs:** energy_headquarter_id → public.enterprises_energyheadquarter.id; enterprise_id → public.enterprises_enterprise.id; measurement_point_id → public.enterprises_measurementpoint.id

### `public.alerts_limitalert` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| indicator | character varying | no |  |
| unit | character varying | no |  |
| value | character varying | no |  |
| level | character varying | no |  |
| resolved | boolean | no |  |
| device_id | bigint | no |  |
| room_id | bigint | no |  |

**PK:** id

**FKs:** device_id → public.devices_deviceairquality.id; room_id → public.enterprises_room.id

### `public.auth_group` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | integer | no |  |
| name | character varying | no |  |

**PK:** id

### `public.auth_group_permissions` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| group_id | integer | no |  |
| permission_id | integer | no |  |

**PK:** id

**FKs:** permission_id → public.auth_permission.id; group_id → public.auth_group.id

### `public.auth_permission` (BASE TABLE, ~216 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | integer | no |  |
| name | character varying | no |  |
| content_type_id | integer | no |  |
| codename | character varying | no |  |

**PK:** id

**FKs:** content_type_id → public.django_content_type.id

### `public.authtoken_token` (BASE TABLE, ~42 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| key | character varying | no |  |
| created | timestamp with time zone | no |  |
| user_id | bigint | no |  |

**PK:** key

**FKs:** user_id → public.accounts_user.id

### `public.control_devices_controldevice` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| dev_uid | character varying | no |  |
| name | character varying | no |  |
| state | character varying | no |  |
| is_active | boolean | no |  |
| room_id | bigint | sí |  |
| controlled_device_id | bigint | sí |  |

**PK:** id

**FKs:** controlled_device_id → public.control_devices_controlleddevice.id; room_id → public.enterprises_room.id

### `public.control_devices_controldevicedata` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| voltage | double precision | sí |  |
| active_power | double precision | sí |  |
| power_factor | double precision | sí |  |
| power_consumed | double precision | sí |  |
| current | double precision | sí |  |
| state | character varying | no |  |
| time | double precision | no |  |
| control_device_id | bigint | sí |  |

**PK:** id

**FKs:** control_device_id → public.control_devices_controldevice.id

### `public.control_devices_controlleddevice` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| name | character varying | no |  |

**PK:** id

### `public.devices_device` (BASE TABLE, ~48 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| dev_eui | character varying | no |  |
| name | character varying | no |  |
| model | character varying | no |  |
| phase_type | character varying | no |  |
| is_active | boolean | no |  |
| is_multichannel | boolean | no |  |
| number_of_channels | integer | no |  |
| electrical_panel_id | bigint | sí |  |

**PK:** id

**FKs:** electrical_panel_id → public.enterprises_electricalpanel.id

### `public.devices_deviceairquality` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| dev_eui | character varying | no |  |
| type_sensor | character varying | no |  |
| is_activated | boolean | no |  |
| no_data_alert | boolean | no |  |
| last_missing_alert_sent | timestamp with time zone | sí |  |
| room_id | bigint | no |  |

**PK:** id

**FKs:** room_id → public.enterprises_room.id

### `public.devices_deviceindicator` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| is_active | boolean | no |  |
| device_id | bigint | no |  |
| indicator_id | bigint | no |  |

**PK:** id

**FKs:** indicator_id → public.indicators_indicator.id; device_id → public.devices_device.id

### `public.devices_indicatordevice` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| is_numeric | boolean | no |  |
| is_activated | boolean | no |  |
| alert_high_on | boolean | no |  |
| alert_missing_on | boolean | no |  |
| device_id | bigint | no |  |
| indicator_id | bigint | no |  |
| unit_id | bigint | no |  |

**PK:** id

**FKs:** device_id → public.devices_deviceairquality.id; indicator_id → public.indicators_indicator.id; unit_id → public.indicators_unitmeasure.id

### `public.devices_indicatorroom` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| is_activated | boolean | no |  |
| indicator_id | bigint | no |  |
| room_id | bigint | no |  |
| unit_id | bigint | no |  |

**PK:** id

**FKs:** indicator_id → public.indicators_indicator.id; unit_id → public.indicators_unitmeasure.id; room_id → public.enterprises_room.id

### `public.django_admin_log` (BASE TABLE, ~1,239 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | integer | no |  |
| action_time | timestamp with time zone | no |  |
| object_id | text | sí |  |
| object_repr | character varying | no |  |
| action_flag | smallint | no |  |
| change_message | text | no |  |
| content_type_id | integer | sí |  |
| user_id | bigint | no |  |

**PK:** id

**FKs:** content_type_id → public.django_content_type.id; user_id → public.accounts_user.id

### `public.django_celery_beat_clockedschedule` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | integer | no |  |
| clocked_time | timestamp with time zone | no |  |

**PK:** id

### `public.django_celery_beat_crontabschedule` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | integer | no |  |
| minute | character varying | no |  |
| hour | character varying | no |  |
| day_of_week | character varying | no |  |
| day_of_month | character varying | no |  |
| month_of_year | character varying | no |  |
| timezone | character varying | no |  |

**PK:** id

### `public.django_celery_beat_intervalschedule` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | integer | no |  |
| every | integer | no |  |
| period | character varying | no |  |

**PK:** id

### `public.django_celery_beat_periodictask` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | integer | no |  |
| name | character varying | no |  |
| task | character varying | no |  |
| args | text | no |  |
| kwargs | text | no |  |
| queue | character varying | sí |  |
| exchange | character varying | sí |  |
| routing_key | character varying | sí |  |
| expires | timestamp with time zone | sí |  |
| enabled | boolean | no |  |
| last_run_at | timestamp with time zone | sí |  |
| total_run_count | integer | no |  |
| date_changed | timestamp with time zone | no |  |
| description | text | no |  |
| crontab_id | integer | sí |  |
| interval_id | integer | sí |  |
| solar_id | integer | sí |  |
| one_off | boolean | no |  |
| start_time | timestamp with time zone | sí |  |
| priority | integer | sí |  |
| headers | text | no |  |
| clocked_id | integer | sí |  |
| expire_seconds | integer | sí |  |

**PK:** id

**FKs:** clocked_id → public.django_celery_beat_clockedschedule.id; crontab_id → public.django_celery_beat_crontabschedule.id; interval_id → public.django_celery_beat_intervalschedule.id; solar_id → public.django_celery_beat_solarschedule.id

### `public.django_celery_beat_periodictasks` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| ident | smallint | no |  |
| last_update | timestamp with time zone | no |  |

**PK:** ident

### `public.django_celery_beat_solarschedule` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | integer | no |  |
| event | character varying | no |  |
| latitude | numeric | no |  |
| longitude | numeric | no |  |

**PK:** id

### `public.django_celery_results_chordcounter` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | integer | no |  |
| group_id | character varying | no |  |
| sub_tasks | text | no |  |
| count | integer | no |  |

**PK:** id

### `public.django_celery_results_groupresult` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | integer | no |  |
| group_id | character varying | no |  |
| date_created | timestamp with time zone | no |  |
| date_done | timestamp with time zone | no |  |
| content_type | character varying | no |  |
| content_encoding | character varying | no |  |
| result | text | sí |  |

**PK:** id

### `public.django_celery_results_taskresult` (BASE TABLE, ~4,353 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | integer | no |  |
| task_id | character varying | no |  |
| status | character varying | no |  |
| content_type | character varying | no |  |
| content_encoding | character varying | no |  |
| result | text | sí |  |
| date_done | timestamp with time zone | no |  |
| traceback | text | sí |  |
| meta | text | sí |  |
| task_args | text | sí |  |
| task_kwargs | text | sí |  |
| task_name | character varying | sí |  |
| worker | character varying | sí |  |
| date_created | timestamp with time zone | no |  |
| periodic_task_name | character varying | sí |  |

**PK:** id

### `public.django_content_type` (BASE TABLE, ~54 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | integer | no |  |
| app_label | character varying | no |  |
| model | character varying | no |  |

**PK:** id

### `public.django_migrations` (BASE TABLE, ~127 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| app | character varying | no |  |
| name | character varying | no |  |
| applied | timestamp with time zone | no |  |

**PK:** id

### `public.django_session` (BASE TABLE, ~21 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| session_key | character varying | no |  |
| session_data | text | no |  |
| expire_date | timestamp with time zone | no |  |

**PK:** session_key

### `public.enterprises_billingactive` (BASE TABLE)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| is_active | boolean | no |  |
| billing_permission_id | bigint | no |  |
| energy_headquarter_id | bigint | no |  |
| currency | character varying | sí |  |

**PK:** id

**FKs:** billing_permission_id → public.enterprises_billingpermission.id; energy_headquarter_id → public.enterprises_energyheadquarter.id

### `public.enterprises_billingcycle` (BASE TABLE)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| start_date | date | no |  |
| end_date | date | no |  |
| is_current | boolean | no |  |
| energy_headquarter_id | bigint | no |  |

**PK:** id

**FKs:** energy_headquarter_id → public.enterprises_energyheadquarter.id

### `public.enterprises_billingdata` (BASE TABLE, ~4 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| billing_name | character varying | no |  |
| description | text | no |  |
| monthly_fixed_charge | double precision | sí |  |
| charge_for_active_energy | double precision | sí |  |
| charge_for_active_energy_peak | double precision | sí |  |
| charge_for_active_energy_off_peak | double precision | sí |  |
| charge_for_active_power_generation_peak | double precision | sí |  |
| charge_for_active_power_generation_off_peak | double precision | sí |  |
| charge_for_active_power_distribution_peak | double precision | sí |  |
| charge_for_active_power_distribution_off_peak | double precision | sí |  |
| charge_for_reactive_energy_exceeding_30_percent | double precision | sí |  |
| currency | character varying | no |  |
| energy_unit | character varying | no |  |
| power_unit | character varying | no |  |

**PK:** id

### `public.enterprises_billingpermission` (BASE TABLE, ~14 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| name | character varying | no |  |
| is_active | boolean | no |  |
| code | character varying | no |  |

**PK:** id

### `public.enterprises_clampassignment` (BASE TABLE, ~36 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| clamp_serial_number | character varying | no |  |
| channel | character varying | no |  |
| assigned_at | timestamp with time zone | no |  |
| unassigned_at | timestamp with time zone | sí |  |
| notes | text | sí |  |
| is_active | boolean | no |  |
| device_id | bigint | no |  |
| measurement_point_id | bigint | no |  |

**PK:** id

**FKs:** device_id → public.devices_device.id; measurement_point_id → public.enterprises_measurementpoint.id

### `public.enterprises_electricalpanel` (BASE TABLE, ~13 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| type | character varying | no |  |
| threads | integer | sí |  |
| name | character varying | no |  |
| is_active | boolean | no |  |
| is_main | boolean | no |  |
| energy_headquarter_id | bigint | no |  |

**PK:** id

**FKs:** energy_headquarter_id → public.enterprises_energyheadquarter.id

### `public.enterprises_energyheadquarter` (BASE TABLE, ~8 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| name | character varying | sí |  |
| is_active | boolean | no |  |
| energy_provider | character varying | sí |  |
| supply_number | integer | sí |  |
| tariff_rating | boolean | no |  |
| reactive_energy_capacitive | boolean | no |  |
| first_max_demand_last_six_months | double precision | sí |  |
| second_max_demand_last_six_months | double precision | sí |  |
| home_assistant_webhook | character varying | sí |  |
| billing_data_id | bigint | sí |  |
| enterprise_id | bigint | no |  |

**PK:** id

**FKs:** billing_data_id → public.enterprises_billingdata.id; enterprise_id → public.enterprises_enterprise.id

### `public.enterprises_enterprise` (BASE TABLE, ~8 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| name | character varying | sí |  |
| acronym | character varying | sí |  |
| date_installation | date | sí |  |
| background_color | character varying | sí |  |

**PK:** id

### `public.enterprises_favoritepoint` (BASE TABLE, ~7 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| name | character varying | no |  |
| electrical_panel_id | bigint | no |  |
| energy_headquarter_id | bigint | no |  |
| enterprise_id | bigint | no |  |
| measurement_point_id | bigint | no |  |

**PK:** id

**FKs:** electrical_panel_id → public.enterprises_electricalpanel.id; energy_headquarter_id → public.enterprises_energyheadquarter.id; enterprise_id → public.enterprises_enterprise.id; measurement_point_id → public.enterprises_measurementpoint.id

### `public.enterprises_headquartertariffpdf` (BASE TABLE)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| title | character varying | no |  |
| pdf_url | character varying | no |  |
| month_date | date | no |  |
| energy_headquarter_id | bigint | no |  |

**PK:** id

**FKs:** energy_headquarter_id → public.enterprises_energyheadquarter.id

### `public.enterprises_measurementpoint` (BASE TABLE, ~85 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| name | character varying | no |  |
| is_active | boolean | no |  |
| is_main | boolean | no |  |
| channel | character varying | no |  |
| type | character varying | no |  |
| key | character varying | sí |  |
| capacity | character varying | sí |  |
| hardware | character varying | sí |  |
| device_id | bigint | sí |  |
| capacity_amperage | integer | no |  |
| capacity_voltage | integer | no |  |
| installation_date | date | sí |  |
| location_reference | text | sí |  |
| last_threshold_calculation | timestamp with time zone | sí |  |
| saturday_threshold_inferior_limit | double precision | sí |  |
| saturday_threshold_superior_limit | double precision | sí |  |
| sunday_threshold_inferior_limit | double precision | sí |  |
| sunday_threshold_superior_limit | double precision | sí |  |
| workday_threshold_inferior_limit | double precision | sí |  |
| workday_threshold_superior_limit | double precision | sí |  |
| monitoring_topology | character varying | no |  |

**PK:** id

**FKs:** device_id → public.devices_device.id

### `public.enterprises_panelautomatizationzeia` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| type | character varying | no |  |
| name | character varying | no |  |
| is_active | boolean | no |  |
| energy_headquarter_id | bigint | no |  |

**PK:** id

**FKs:** energy_headquarter_id → public.enterprises_energyheadquarter.id

### `public.enterprises_power` (BASE TABLE, ~2 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| power_installed | double precision | no |  |
| is_power_installed_active | boolean | no |  |
| power_contracted | double precision | no |  |
| is_power_contracted_active | boolean | no |  |
| power_max | double precision | no |  |
| is_power_max_active | boolean | no |  |
| energy_headquarter_id | bigint | no |  |

**PK:** id

**FKs:** energy_headquarter_id → public.enterprises_energyheadquarter.id

### `public.enterprises_room` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| name | character varying | sí |  |
| is_active | boolean | no |  |
| headquarter_id | bigint | no |  |

**PK:** id

**FKs:** headquarter_id → public.enterprises_energyheadquarter.id

### `public.historical_readinghistory` (BASE TABLE, ~8,267,592 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| data | jsonb | no |  |
| electrical_panel_id | bigint | no |  |

**PK:** id

**FKs:** electrical_panel_id → public.enterprises_electricalpanel.id

### `public.historical_readinghistoryairqualityco2temphum` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| data | jsonb | no |  |
| room_id | bigint | sí |  |

**PK:** id

**FKs:** room_id → public.enterprises_room.id

### `public.historical_readinghistoryenergyrelays` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| data | jsonb | no |  |
| panel_automatization_id | bigint | sí |  |

**PK:** id

**FKs:** panel_automatization_id → public.enterprises_panelautomatizationzeia.id

### `public.historical_readinghistorythermalcomfortpersonsensor` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| data | jsonb | no |  |
| room_id | bigint | sí |  |

**PK:** id

**FKs:** room_id → public.enterprises_room.id

### `public.historical_readinghistorythermalcomfortrelaybraker` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| data | jsonb | no |  |
| panel_automatization_id | bigint | sí |  |

**PK:** id

**FKs:** panel_automatization_id → public.enterprises_panelautomatizationzeia.id

### `public.historical_readinghistorythermalcomforttemphum` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| data | jsonb | no |  |
| room_id | bigint | sí |  |

**PK:** id

**FKs:** room_id → public.enterprises_room.id

### `public.indicators_indicator` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| name | character varying | no |  |
| abbreviation | character varying | no |  |
| is_active | boolean | no |  |
| unit_measure_id | bigint | sí |  |

**PK:** id

**FKs:** unit_measure_id → public.indicators_unitmeasure.id

### `public.indicators_unitmeasure` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| name | character varying | no |  |
| abbreviation | character varying | no |  |
| is_active | boolean | no |  |

**PK:** id

### `public.pruebabruno` (BASE TABLE, ~10 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| campo1 | character | sí |  |

### `public.readings_reading` (BASE TABLE, ~8,261,127 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| values_per_channel | ARRAY | no |  |
| P_value | double precision | sí |  |
| Q_value | double precision | sí |  |
| S_value | double precision | sí |  |
| PF_value | double precision | sí |  |
| F_value | double precision | sí |  |
| EPpos_value | double precision | sí |  |
| EPneg_value | double precision | sí |  |
| EQpos_value | double precision | sí |  |
| EQneg_value | double precision | sí |  |
| Ua_value | double precision | sí |  |
| Ub_value | double precision | sí |  |
| Uc_value | double precision | sí |  |
| Uab_value | double precision | sí |  |
| Ubc_value | double precision | sí |  |
| Uac_value | double precision | sí |  |
| Ia_value | double precision | sí |  |
| Ib_value | double precision | sí |  |
| Ic_value | double precision | sí |  |
| In_value | double precision | sí |  |
| THDUa_value | double precision | sí |  |
| THDUb_value | double precision | sí |  |
| THDUc_value | double precision | sí |  |
| THDIa_value | double precision | sí |  |
| THDIb_value | double precision | sí |  |
| THDIc_value | double precision | sí |  |
| device_id | bigint | no |  |
| measurement_point_id | bigint | no |  |
| clamp_assignment_id | bigint | sí |  |
| U_value | double precision | sí |  |
| I_value | double precision | sí |  |
| EP_value | double precision | sí |  |
| EQ_value | double precision | sí |  |

**PK:** id

**FKs:** clamp_assignment_id → public.enterprises_clampassignment.id; device_id → public.devices_device.id; measurement_point_id → public.enterprises_measurementpoint.id

### `public.readings_readingairqualityandenergy` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |
| value | character varying | sí |  |
| status_ica | character varying | sí |  |
| indicator_device_id | bigint | sí |  |
| room_id | bigint | sí |  |

**PK:** id

**FKs:** indicator_device_id → public.devices_indicatordevice.id; room_id → public.enterprises_room.id

### `public.readings_readingthermalcomfort` (BASE TABLE, ~0 filas)

| Columna | Tipo | Nullable | Default |
|---|---|---|---|
| id | bigint | no |  |
| created_at | timestamp with time zone | no |  |
| modified_at | timestamp with time zone | no |  |

**PK:** id

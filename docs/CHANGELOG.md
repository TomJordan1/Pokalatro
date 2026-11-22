# Changelog — Pokalatro v8 a v9

## Reestructuración del repositorio

El proyecto se organizó en módulos segregados manteniendo compatibilidad total de ejecución desde la raíz:

- `bot.py` (raíz): lanzador compatible que delega en `app/bot.py` manteniendo `python bot.py` y `conectar.bat` operativos.
- `app/`: código fuente de producción en Python 3.8+ puro, sin dependencias externas.
- `docs/`: documentación de investigación, auditoría de v8, arquitectura v9, reportes experimentales y guía de ejecución.
- `tests/`: suite de pruebas automatizadas (`test_estrategia.py` y `test_v9.py`), ejecutable vía `python -m unittest discover -s tests -v`.
- `tools/`: herramientas offline de entrenamiento CFR/MCCFR en C++, simulador fiel de PokerFight, evaluador rápido en C++, puente ACPC 2.0.0 contra Slumbot2019 y suite estadística (`benchmark.py`).
- `backup_v8/`: respaldo intacto de Pokalatro v8 (referencia inmutable para comparaciones pareadas).

---

## Cambios en `app/` (producción)

### Nuevos módulos
- **`app/abstraccion.py`**: ordenamiento realista de las 169 clases de manos por jugabilidad (`ORDEN_169`, `COMBOS_ORDENADOS`, `percentil_jugabilidad`) y clasificación estratégica postflop (`made_tier` 0..6, `draw_tier` 0..3 con validación de proyectos propios de color/escalera sin falsos positivos en tableros de 3 del mismo palo ni escaleras comunitarias, `nut_blocker`, buckets de `SPR`, textura y `facing`).
- **`app/blueprint_data.py`**: tablas precomputadas offline mediante CFR+ y External-Sampling MCCFR (`tools/trainer_cfr.cpp`):
  - `PUSH_FOLD`: equilibrio Nash Push/Fold interpolado para stacks de 5, 8, 11 y 14 BB (heads-up y multiway).
  - `PREFLOP_DEEP`: frecuencias de equilibrio para `EP`, `CO` y `BTN`.
  - `POSTFLOP_BP`: política MCCFR de 3M iteraciones sobre estados estratégicos postflop.
- **`app/blueprint_6max.py`**: blueprint 6-max (`UTG`, `HJ`, `CO`, `BTN`, `SB`, `BB`) entrenado con **Linear External-Sampling MCCFR** sobre 2 500 000 manos reales simultáneas (`tools/trainer_pluribus_6max.cpp`), cubriendo 1 974 estados postflop 6-max e integrado en `app/preflop.py` y `app/postflop.py`.
- **`app/subjuego.py`**: resolvedor local exacto de subjuego en el river heads-up (`resolver_river_hu`) que integra EV sobre la distribución bayesiana completa del rival con regularización por MDF y margen de seguridad ante apuestas polarizadas en river.
- **`app/torneo.py`**: gestión específica para el formato PokerFight (50/100 fijas, 10k iniciales, carryover de 10 rondas y eliminación en 0): consulta interpolada `probs_push_fold`, prima de supervivencia (`minimo_por_supervivencia`) acotada incluso en situaciones de eliminación directa y `factor_presion_eliminacion`.
- **`app/__init__.py`**: definición del paquete `app`.

### Módulos modificados
- **`app/turno.py`**:
  - Corrige el cálculo de posición relativa (`_lista_ordenada`) eliminando sesgo cuando existen asientos vacíos o eliminados.
  - Corrige `pot_odds` descontando el exceso no igualable frente a apuestas rivales que superan nuestro stack restante.
  - Añade `stack_efectivo_bb`, `spr`, `es_sb`, `es_bb`, `rivales_detras` y soporte opcional de banderas de ablación (`ablate`).
- **`app/motor.py`**:
  - Corrige `mejora` para que no otorgue bonus por proyectos de escalera puramente comunitarios.
  - Añade `RangoPonderado` (con soporte de muestreo ponderado por importancia y compatibilidad de atributos `.techo`/`.farol`) y `puntajes_en_mesa` / `valores_en_mesa` cacheados por tablero.
- **`app/rangos.py`**:
  - Reemplaza los cortes binarios por actualización bayesiana continua sobre las 1 326 combinaciones (`rango_rival`), incorporando topeado (*capping*) por llamadas/checks pasivos preflop, calibración por `fuerza_al_apostar`/`fuerza_al_allin`, efecto de tamaño relativo de apuesta, concentración ante múltiples calles agresivas o all-in, y modo de compatibilidad `rango_v8_compat` para ablación.
- **`app/stats.py`**:
  - Añade seguimiento detallado durante el torneo con decaimiento exponencial: `vpip_pct`, `pfr_pct`, `three_bet_pct`, `fold_to_3bet`, `fold_to_cbet`, `cbet_pct`, `frecuencia_apuesta_rio`, `tasa_allin` y métricas de confianza por muestra (`confianza`).
  - Corrige contaminación de showdowns cuando la ciega grande solo hizo check preflop sin acción voluntaria.
- **`app/preflop.py`**:
  - Integra `PUSH_FOLD` CFR+ en stacks cortos (`<= 13 BB` o efectivo `<= 11.5 BB`), apertura híbrida Blueprint+heurística por posición (`EP/CO/BTN`), aislamiento selectivo ante limpers, defensa acotada frente a subidas grandes con prima de supervivencia y rangos balanceados de 3-bet/4-bet.
- **`app/postflop.py`**:
  - Integra el resolvedor exacto de river HU (`subjuego.resolver_river_hu`), consulta de Blueprint MCCFR como ancla ante incertidumbre, control de pozo con una pareja, límite estricto a re-subidas repetidas en una misma calle (`subidas_en_esta_calle >= 2`), acotación de riesgo del stack en semi-faroles y explotación regularizada por confianza (`estacion`, `nit`, `loco`).
- **`app/bot.py`**:
  - Actualiza el saludo de conexión (`Pokalatro v9`) y conecta `stats.registrar_showdown` pasando tablero e historial para calibración bayesiana exacta.
- **`app/conexion.py`**, **`app/estrategia.py`**, **`app/tabla_preflop.py`**: conservan protocolo TCP 1.0.0, filtro estricto de `acciones_validas` y fallback seguro ante excepciones.

---

## Pruebas (`tests/`) y herramientas offline (`tools/`)

- **`tests/test_v9.py`**: 13 pruebas unitarias y de protocolo TCP loopback real (posicionamiento con eliminados, pot odds sin exceso, no pagar 100bb all-in con 77, ranking jugabilidad AKo/AQs > 99, rangos bayesianos loco vs nit, showdown BB check, subjuego river HU, push/fold, tabla `blueprint_6max`, regresión 4-bet/river y sesión TCP real con JSON corrupto).
- **`tests/test_estrategia.py`**: migrado a `tests/` con resolución de ruta hacia `app/`.
- **`tools/simulador_pf.py` y `tools/bots_kit.py`**: simulador exacto de PokerFight en Python (crupier, side pots, all-ins cortos, orden de eliminación, carryover 10 rondas) y réplica de los 8 bots oficiales + bots de estrés (`BotExplotador`, `BotCambiante`) apoyados en `tools/fast_eval.dll`.
- **`tools/trainer_cfr.cpp` y `tools/trainer_pluribus_6max.cpp`**: generadores C++ offline de CFR+ Push/Fold, `PREFLOP_DEEP`, MCCFR postflop (3M iteraciones) y Linear External-Sampling MCCFR 6-max (2.5M manos reales de 6 sillas simultáneas).
- **`tools/benchmark.py`**: suite estadística pareada con semillas deterministas, IC 95%, test t pareado, modos `comparar`, `estres`, `torneo10` y `ablacion`.
- **`tools/slumbot_arena.py`**: puente TCP ACPC 2.0.0 para evaluar partidas duplicadas HUNL contra `slumbot2019/bin/run_bot`.

---

## Documentación (`docs/`)

- `docs/INFORME_INVESTIGACION.md`: síntesis técnica de CFR/MCCFR/Slumbot2019, abstracción, subgame solving, modelado bayesiano, ICM/torneos y evaluación estadística.
- `docs/AUDITORIA_V8.md`: auditoría módulo por módulo de Pokalatro v8 ordenada por impacto estratégico.
- `docs/ARQUITECTURA_V9.md`: diseño de la arquitectura híbrida de 5 componentes de v9.
- `docs/EXPERIMENTOS.md`: reporte experimental completo (comparación pareada, torneo 10 rondas, estrés, ablación y duelo HUNL vs Slumbot2019).
- `docs/LEEME-EJECUTAR.md` y `README.md`: actualizados con la nueva estructura y comandos de ejecución.

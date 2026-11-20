# Pokalatro v9 — Bot Híbrido para PokerFight

Bot de Texas Hold'em No-Limit desarrollado en **Python puro** (3.8+, cero dependencias externas en producción) para competir en los torneos de **PokerFight** por TCP JSON Lines (protocolo `1.0.0`).

Al conectarse anuncia su versión (`Pokalatro v9 conectado como ...`).

---

## Arquitectura híbrida (5 componentes)

Pokalatro v9 conserva toda la robustez en tiempo real de v8 (filtro estricto de `acciones_validas`, corte por presupuesto temporal, fallback seguro ante excepciones y reconexión persistente con `-persistente`) e introduce una arquitectura híbrida en `app/`:

1. **Abstracción estratégica (`app/abstraccion.py`)**: orden realista de jugabilidad para 169 manos iniciales y clasificación postflop por `made_tier` (0..6), `draw_tier` (0..3 sin falsos positivos), `nut_blocker`, `SPR`, textura y `facing`.
2. **Blueprint offline CFR+ / MCCFR (`app/blueprint_data.py`, entrenado con `tools/trainer_cfr.cpp`)**: tablas de equilibrio Nash Push/Fold interpoladas (5–14 BB), apertura/defensa profunda (`EP/CO/BTN`) y política postflop de 3M iteraciones External-Sampling MCCFR como ancla ante muestras pequeñas.
3. **Modelado bayesiano continuo (`app/rangos.py` + `app/stats.py`)**: distribución ponderada sobre las 1 326 combinaciones actualizada secuencialmente por acciones, tamaño de apuesta, *capping* pasivo preflop, decaimiento estadístico durante el torneo y calibración por showdowns.
4. **Explotación regularizada (`app/preflop.py` + `app/postflop.py`)**: adaptación acotada por confianza estadística hacia estaciones de pago, nits y perfiles agresivos (`loco`), evitando sobreajustar con muestras chicas.
5. **Subjuego River HU + Gestión de Torneo (`app/subjuego.py` + `app/torneo.py`)**: integración exacta de EV en river heads-up con regularización MDF y margen de polarización, junto con prima de supervivencia y presión de eliminación ajustadas al formato PokerFight.

---

## Resumen de resultados validados (v9 vs v8)

| Experimento | Pokalatro v8 | Pokalatro v9 | Diferencia / Significancia |
| --- | --- | --- | --- |
| **Clasificatoria 5-max vs kit** (200 mesas) | +4 536.9 (Top-1 31.0%, Top-3 81.5%) | **+4 788.0** (Top-1 30.5%, **Top-3 84.5%**) | +251.0 fichas/mesa, latencia med **17.76 ms** (`vs 50.79 ms`) |
| **Torneo acumulativo 10 rondas (`torneo10`)** (40 torneos) | +26 030.7 (Top-6 70.0%, Quiebras 57.5%) | **+34 022.0** (**Top-6 80.0%**, **Quiebras 42.5%**) | **+7 991.2** fichas/torneo, campeón **32.5% vs 27.5%** |
| **Estrés `[fuertes]`** (120 mesas) | +3 007.7 (Top-1 20.8%, Quiebras 11.7%) | **+6 393.1** (**Top-1 33.3%**, **Quiebras 5.8%**) | **+3 385.4 (`p = 0.0011`)** |
| **Estrés `[adverso]`** (120 mesas) | +3 004.9 (Top-1 15.8%, Quiebras 11.7%) | **+5 961.0** (**Top-1 27.5%**, **Top-3 94.2%**) | **+2 956.1 (`p = 0.0268`)** |
| **Estrés `[duro]`** (120 mesas) | +2 429.9 (Top-1 20.0%, Quiebras 14.2%) | **+5 552.8** (**Top-1 30.0%**, **Quiebras 6.7%**) | **+3 122.8 (`p = 0.0012`)** |
| **Duelo duplicado HUNL vs Slumbot2019 local** (800 manos) | — | **+57 995 fichas** (`+724.9 mbb/h`) | **4/4 bloques ganados** |
| **Suite de pruebas (`tests/`)** | — | **30 / 30 OK** | 0 fallos, 0 timeouts |

Detalle experimental completo en [`docs/EXPERIMENTOS.md`](docs/EXPERIMENTOS.md).

---

## Estructura de carpetas

| Ruta | Contenido |
| --- | --- |
| `bot.py` / `conectar.bat` | Lanzadores compatibles desde la raíz (delegan en `app/bot.py`). |
| [`app/`](app/) | Bot de producción en Python 3.8+ puro (`estrategia.py`, `preflop.py`, `postflop.py`, `motor.py`, `abstraccion.py`, `blueprint_data.py`, `rangos.py`, `stats.py`, `subjuego.py`, `torneo.py`, `turno.py`, `conexion.py`, `tabla_preflop.py`, `bot.py`). |
| [`tests/`](tests/) | Pruebas unitarias, de protocolo TCP real y fuzz ([`test_estrategia.py`](tests/test_estrategia.py), [`test_v9.py`](tests/test_v9.py)). |
| [`docs/`](docs/) | [`INFORME_INVESTIGACION.md`](docs/INFORME_INVESTIGACION.md), [`AUDITORIA_V8.md`](docs/AUDITORIA_V8.md), [`ARQUITECTURA_V9.md`](docs/ARQUITECTURA_V9.md), [`EXPERIMENTOS.md`](docs/EXPERIMENTOS.md), [`CHANGELOG.md`](docs/CHANGELOG.md), [`LEEME-EJECUTAR.md`](docs/LEEME-EJECUTAR.md), [`RESULTADOS.md`](docs/RESULTADOS.md). |
| [`tools/`](tools/) | Simulador fiel de PokerFight (`simulador_pf.py`), bots del kit (`bots_kit.py`), evaluador C++ rápido (`fast_eval.cpp`/`dll`), entrenador CFR+/MCCFR (`trainer_cfr.cpp`), benchmark estadístico (`benchmark.py`) y arena Slumbot2019 (`slumbot_arena.py`). |
| [`backup_v8/`](backup_v8/) | Copia intacta de referencia de Pokalatro v8. |

---

## Ejecución rápida

```powershell
# Ejecutar las 30 pruebas automatizadas
python -m unittest discover -s tests -v

# Conectar el bot al servidor de PokerFight (o vía app/bot.py)
python bot.py -addr HOST:PUERTO -token "$env:TOKEN" -persistente

# Reproducir benchmark pareado o torneo de 10 rondas
python -m tools.benchmark --n-mesas 200 --seed 2026 --modo comparar --rivales kit
python -m tools.benchmark --n-mesas 40 --seed 2026 --modo torneo10
```

Ver guía operativa completa en [`docs/LEEME-EJECUTAR.md`](docs/LEEME-EJECUTAR.md).

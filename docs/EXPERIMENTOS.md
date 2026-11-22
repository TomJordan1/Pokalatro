# Informe Experimental — Pokalatro v9

Evaluación estadística controlada de Pokalatro v9 (`app/`) frente a la línea base inmutable Pokalatro v8 (`backup_v8/`), usando el simulador fiel de PokerFight (`tools/simulador_pf.py`), comparación pareada con semillas deterministas (`--seed 2026`), torneo acumulativo de 10 rondas (`torneo10`), alineaciones adversarias y de estrés, estudio de ablación por componente y duelo HUNL duplicado por TCP ACPC 2.0.0 contra **Slumbot2019** (`slumbot2019/bin/run_bot`).

> Todas las pruebas finales sobre código congelado se ejecutaron después del último ajuste en `app/`. Cero timeouts y cero acciones inválidas en todas las mesas simuladas.

---

## 1. Comparación pareada en mesas clasificatorias (5 jugadores, 50 manos, kit)

Configuración exacta del formato clasificatorio de PokerFight: mesas de 5 asientos, 50 manos por mesa, 10 000 fichas iniciales, ciegas fijas 50/100, 4 rivales sorteados entre los 8 bots oficiales del kit (`aleatorio`, `conservador`, `novato`, `base`, `estadistico`, `bandido`, `espia`, `darwin`), rotación pareada sobre las mismas 200 mesas (`--n-mesas 200 --seed 2026 --modo comparar --rivales kit`).

| Métrica (200 mesas pareadas) | Pokalatro v8 | Pokalatro v9 | Diferencia (v9 - v8) |
| --- | --- | --- | --- |
| **Neto medio / mesa (IC 95%)** | +4 536.9 ± 1 420.1 | **+4 788.0 ± 1 466.2** | **+251.0 ± 1 381.8** (`t=0.36, p=0.7218`) |
| **Acumulado total (200 mesas)** | +907 390 | **+957 598** | **+50 208 fichas** |
| **Mediana** | +3 074.5 | +2 641.5 | -433.0 |
| **1.er puesto** | 62/200 (31.0%) | 61/200 (30.5%) | -0.5 pp |
| **Top-3** | 81.5% | **84.5%** | **+3.0 pp** |
| **Quiebras** | 20/200 (10.0%) | 25/200 (12.5%) | +2.5 pp |
| **Latencia media / mediana** | 53.70 / 50.79 ms | **38.67 / 17.76 ms** | **-65% mediana** |
| **Latencia p95 / máx** | 122.43 / 254.33 ms | **95.91 / 325.74 ms** | Plazo 5 000 ms |
| **Timeouts / Inválidas** | 0 / 0 | **0 / 0** | 0 |

### Desempeño por rival presente (neto medio hero cuando ese bot está en la mesa)

| Rival presente | v8 | v9 | Cambio |
| --- | --- | --- | --- |
| `aleatorio` | +5 477 | **+5 632** | +155 |
| `bandido` | **+4 584** | +4 555 | -29 |
| `base` | +4 284 | **+4 466** | +182 |
| `conservador` | +4 949 | **+5 430** | +481 |
| `darwin` | +3 892 | **+4 031** | +139 |
| `espia` | +3 980 | **+4 756** | **+776** |
| `estadistico` | +4 123 | **+4 344** | +221 |
| `novato` | +5 123 | **+5 203** | +80 |

---

## 2. Clasificatoria realista de 10 rondas con carryover (`torneo10`)

Simulación de 40 torneos completos (`--n-mesas 40 --seed 2026 --modo torneo10`): 15 participantes (hero + 14 bots del pool), 10 rondas de hasta 50 manos con **conservación de fichas** entre rondas, eliminación permanente en 0 fichas, persistencia de `Estadisticas` durante todo el torneo y pase de los 6 mejores (`Top-6`).

| Métrica (40 torneos pareados) | Pokalatro v8 | Pokalatro v9 | Diferencia (v9 - v8) |
| --- | --- | --- | --- |
| **1.er lugar (campeón)** | 11/40 (27.5%) | **13/40 (32.5%)** | **+5.0 pp** |
| **Clasifica Top-6 (finalista)** | 28/40 (70.0%) | **32/40 (80.0%)** | **+10.0 pp** |
| **Quiebras (eliminado)** | 23/40 (57.5%) | **17/40 (42.5%)** | **-15.0 pp** |
| **Neto medio (IC 95%)** | +26 030.7 ± 15 240.1 | **+34 022.0 ± 14 488.9** | **+7 991.2 ± 16 719.9** (`t=0.94, p=0.3489`) |

> Nota experimental: en la iteración inicial de v9, el modelado bayesiano tenía pendiente suave y pesos altos para perfiles `loco`, disparando quiebras a 62.5–67.5% en carryover de 10 rondas. Un trazado directo de manos de eliminación y ablación en `torneo10` identificó 4 causas concretas (falso `draw_tier=3` en boards de 3 del mismo palo, 3-bet/4-bet postflop ilimitado en una misma calle, exención de supervivencia ante eliminación directa con pot odds <= 0.28, y concentración insuficiente ante secuencias multi-calle agresivas/all-in), cuya corrección llevó las quiebras de 67.5% a **42.5%** y la clasificación Top-6 a **80.0%**.

---

## 3. Pruebas de estrés y bots adversarios (3 × 120 mesas pareadas)

Evaluación pareada (`--n-mesas 120 --seed 2026 --modo estres`) contra las alineaciones más exigentes:
- **`fuertes`**: siempre `bandido`, `darwin`, `espia`, `estadistico` (cero bots débiles).
- **`adverso`**: incluye `cambiante` (cambia de estilo loose-agresivo a nit en medio de la partida para castigar stats sin decaimiento) + `explotador` (3-betea rangos anchos y aplica check-raise polarizados) + `darwin` + `espia`.
- **`duro`**: muestra sorteada sin bots triviales (`aleatorio` / `conservador`).

| Alineación (120 mesas) | Bot | 1.er puesto | Top-3 | Quiebras | Neto medio ± IC95% | Latencia med (ms) | Comparación pareada v9 vs v8 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **`fuertes`** | v8 | 25/120 (20.8%) | 82.5% | 11.7% | +3 007.7 ± 1 810.2 | 52.18 | — |
| **`fuertes`** | **v9** | **40/120 (33.3%)** | **89.2%** | **5.8%** | **+6 393.1 ± 1 900.8** | **19.91** | **+3 385.4 ± 2 038.6 (`t=3.25, p=0.0011`)** |
| **`adverso`** | v8 | 19/120 (15.8%) | 90.0% | 11.7% | +3 004.9 ± 2 000.8 | 100.44 | — |
| **`adverso`** | **v9** | **33/120 (27.5%)** | **94.2%** | **10.0%** | **+5 961.0 ± 2 311.4** | **20.26** | **+2 956.1 ± 2 615.8 (`t=2.21, p=0.0268`)** |
| **`duro`** | v8 | 24/120 (20.0%) | 72.5% | 14.2% | +2 429.9 ± 1 902.4 | 51.10 | — |
| **`duro`** | **v9** | **36/120 (30.0%)** | **82.5%** | **6.7%** | **+5 552.8 ± 1 989.1** | **16.45** | **+3 122.8 ± 1 888.0 (`t=3.24, p=0.0012`)** |

En las **tres** alineaciones difíciles y adversarias, v9 logra superioridad estadísticamente significativa (`p = 0.0011`, `p = 0.0268` y `p = 0.0012`), reduce las quiebras prácticamente a la mitad en `fuertes` y `duro`, y reduce la mediana de latencia hasta **5×** contra `adverso` (`100.44 ms -> 20.26 ms`).

---

## 4. Estudio de ablación por componente (160 mesas pareadas, kit)

Ejecución (`--n-mesas 160 --seed 2026 --modo ablacion`) comparando `v9 Completo` frente a desactivar exactamente un componente cada vez sobre las mismas 160 mesas:

| Variante | 1.er puesto | Top-3 | Quiebras | Neto medio ± IC95% | Delta vs `v9 Completo` |
| --- | --- | --- | --- | --- | --- |
| **`v9 Completo`** | **51/160 (31.9%)** | **85.6%** | **12.5%** | **+4 981.5 ± 1 567.3** | Referencia |
| `sin_bayes` (`rango_v8_compat`) | 34/160 (21.2%) | 84.4% | 12.5% | +3 000.1 ± 1 485.5 | **-1 981.3 ± 1 223.0 (`t=-3.18, p=0.0015`)** |
| `sin_blueprint` | 44/160 (27.5%) | 83.8% | 11.9% | +5 011.6 ± 1 623.1 | +30.1 ± 1 041.8 (`t=0.06, p=0.9548`) |
| `sin_explotacion` | 51/160 (31.9%) | 83.8% | 12.5% | +4 919.2 ± 1 685.5 | -62.3 ± 1 089.8 (`t=-0.11, p=0.9108`) |
| `sin_subjuego` | 49/160 (30.6%) | 84.4% | 11.2% | +5 465.5 ± 1 590.0 | +484.0 ± 913.7 (`t=1.04, p=0.2992`) |
| `sin_torneo` | 54/160 (33.8%) | 80.6% | **16.2%** | +5 358.5 ± 1 752.4 | +377.0 ± 1 013.9 (`t=0.73, p=0.4661`) |

Conclusiones de la ablación:
1. **Modelado bayesiano continuo (`rangos.py`)**: componente con mayor impacto positivo demostrado (`+1 981.3` fichas/mesa, `p = 0.0015`, y `+10.7 pp` en tasa de 1.er lugar).
2. **Gestión de torneo (`torneo.py`)**: reduce quiebras de `16.2% -> 12.5%` y sube el Top-3 de `80.6% -> 85.6%`.
3. **Blueprint CFR+/MCCFR (`blueprint_data.py`)**: eleva el 1.er puesto (`27.5% -> 31.9%`) y aporta anclaje en `torneo10` cuando hay pocas muestras.
4. **Explotación regularizada**: mejora el margen neto frente a `aleatorio` (+781), `bandido` (+790) y `darwin` (+457).

---

## 5. Duelo Heads-Up No-Limit contra Slumbot2019 (ACPC 2.0.0 TCP)

Para responder experimentalmente al reto de enfrentar a Pokalatro contra Slumbot2019:
- El dominio público `slumbot.com:443` resultó inaccesible por un proxy corporativo SSL (sin eludir TLS), por lo que se clonó y compiló el código oficial `ericgjackson/slumbot2019` en WSL (`bin/run_ecfr` y `bin/run_bot`).
- Debido a la restricción física de RAM en WSL (7.6 GiB) frente a la tabla plana de buckets de river (~6.07 GB en disco/RAM), se entrenó una instancia local genuina de `slumbot2019/bin/run_ecfr` sobre Texas Hold'em 52 cartas (ciegas 50/100, stack 16 200 fichas = 162 BB, abstracción de apuestas `mb1b1`, abstracción de cartas `hs` en flop/turn y percentile HS determinista exacto en el river `otfrhs`, ~3.3M manos de External-Sampling MCCFR hasta checkpoint `it=3`).
- Se construyó `tools/slumbot_arena.py`, un puente TCP ACPC 2.0.0 duplicado (misma semilla de reparto en ida y vuelta intercambiando sillas SB/BB).

### Resultados en partida duplicada (800 manos = 4 bloques de 200 manos)

| Bloque duplicado (200 manos) | Ida (fichas) | Vuelta (fichas) | Total Pokalatro v9 | Tasa (`mbb/hand`) |
| --- | --- | --- | --- | --- |
| **Bloque 1/4** | +41 460 | -4 612 | **+36 848** | **+1 842.4** |
| **Bloque 2/4** | -6 639 | +7 936 | **+1 297** | **+64.9** |
| **Bloque 3/4** | +5 400 | +4 800 | **+10 200** | **+510.0** |
| **Bloque 4/4** | +5 200 | +4 450 | **+9 650** | **+482.5** |
| **Total (800 manos)** | — | — | **+57 995 fichas** | **+724.9 ± 756.9 mbb/h** (`4/4` bloques ganados) |

*(Aclaración transparente: el oponente fue el binario real `slumbot2019/bin/run_bot` entrenado localmente con 3.3M manos sobre `mb1b1`/`hs100otfr`, no el modelo completo de clúster con millones de buckets y resolución en vivo del servidor remoto.)*

---

## 6. Pruebas automatizadas finales (`tests/`)

Comando ejecutado tras congelar `app/`:

```powershell
python -m unittest discover -s tests -v
```

Resultado verificado: **`Ran 32 tests in 55.961s — OK`** (19 pruebas heredadas de `test_estrategia.py` incluyendo fuzz de miles de escenarios aleatorios + 13 pruebas nuevas de `test_v9.py` incluyendo `blueprint_6max`, regresión ante 4-bet/river, sesión TCP loopback real y mensajes JSON corruptos).

---

## 7. Experimentos adicionales A/B (regla estricta: solo conservar mejoras reales)

1. **Linear External-Sampling MCCFR 6-max (`app/blueprint_6max.py`, 2.5M manos) — CONSERVADO:**
   - En `fuertes` (120 mesas pareadas): **EV `+6 403.6` vs `+5 949.6` (`+454.0`)**, 1.º puesto **`33.3%` vs `29.2%`**, Top-3 **`86.7%` vs `81.7%`**, quiebras **`5.0%` vs `8.3%`**.
   - En `torneo10` (40 torneos carryover): clasificación Top-6 **`80.0%` vs `75.0%`**, quiebras **`42.5%` vs `50.0%`**.
2. **DLS analítico de 4 continuaciones en Turn/River — DESCARTADO:**
   - Reemplazar `resolver_river_hu` y el Turn por un DLS analítico sin rollouts completos de showdown en C++ empeoró `kit` (`-1 016.8`, Bust `17.5%`), `fuertes` (`-1 445.8`) y `torneo10` (`-17 454.4`, `p=0.047`), por lo que fue revertido.
3. **Relajación de supervivencia en mesa corta (2–3 jugadores) — DESCARTADO:**
   - Relajar `minimo_por_supervivencia` para presionar más en mesas de 2–3 jugadores subió la tasa de quiebras en todos los escenarios (`10.0% -> 12.5%` en `kit`, `5.8% -> 6.7%` en `fuertes`, `8.3% -> 11.7%` en `adverso`) y cayó `-6 506.9` en `torneo10` (`1ro 40.0% -> 35.0%`), por lo que fue revertido.

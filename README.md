# Pokalatro v9 — Bot Híbrido para PokerFight

> **¿Por qué "Pokalatro"?**  
> El nombre nace de juntar **Poker** + **Balatro**. Así como en *Balatro* la clave no es mirar una carta aislada sino combinar mano base, sinergias, multiplicadores y economía para sobrevivir ronda tras ronda, **Pokalatro** combina un esqueleto matemático de teoría de juegos (**CFR+ / MCCFR**) con módulos adaptativos que actúan como multiplicadores de decisión en tiempo real: **inferencia bayesiana de rangos**, **lectura estadística con decaimiento**, **resolución exacta de river** y **gestión de supervivencia de torneo**.

Bot de Texas Hold'em No-Limit desarrollado en **Python puro** (3.8+, cero dependencias externas en producción) para competir en los torneos de **PokerFight** por TCP JSON Lines (protocolo `1.0.0`). Al conectarse anuncia su versión (`Pokalatro v9 conectado como ...`).

---

## Lógica de creación: cómo nació y evolucionó hasta v9

Casi todos los bots básicos de póker cometen el mismo error: calculan qué tan fuerte es su mano *contra cartas al azar*. En póker real, cuando un rival apuesta o sube en el turn, no tiene una mano aleatoria: **su acción filtra sus cartas posibles**.

1. **Punto de partida (v8):** construimos un bot en Python puro con evaluación Monte Carlo rápida, estimación básica de rangos por cortes porcentuales, estadísticas acumuladas de rivales, filtro estricto de acciones válidas y corte por tiempo. Esa versión ya logró el 1.er puesto contra los 8 bots de referencia del kit, pero una auditoría profunda mostró sesgos ante rivales que cambian de estilo, en botes profundos y en torneos acumulativos de 10 rondas.
2. **Investigación y entrenamiento offline:** estudiamos los principios de **CFR+**, **External-Sampling MCCFR** (arquitectura de Slumbot2019), **abstracción de estados y apuestas**, **resolución local de subjuegos** y **modelado bayesiano de oponentes**. Implementamos un entrenador propio en C++ ([`tools/trainer_cfr.cpp`](tools/trainer_cfr.cpp)) que resolvió tablas Push/Fold Nash (5–14 BB), apertura/defensa profunda (`EP/CO/BTN`) y **3 000 000 de iteraciones MCCFR postflop**, exportando los resultados a tablas estáticas en Python puro ([`app/blueprint_data.py`](app/blueprint_data.py)) sin sumar ni una librería externa al bot en producción.
3. **Depuración guiada por datos (ablación y trazas):** construimos un simulador fiel al motor Go de PokerFight ([`tools/simulador_pf.py`](tools/simulador_pf.py)) y evaluamos v9 frente a v8 en cientos de mesas pareadas, pruebas de estrés con bots adaptativos, torneos acumulativos de 10 rondas (`torneo10`), ablaciones componente por componente y un duelo duplicado contra una instancia local de **Slumbot2019** ([`tools/slumbot_arena.py`](tools/slumbot_arena.py)). Al inspeccionar las trazas exactas de eliminación en `torneo10`, detectamos y corregimos 4 fugas reales (falsos *flush draws* con carta baja en tableros con 3 del mismo palo, re-raises postflop ilimitados en una misma calle, exenciones de supervivencia ante eliminación directa y sobreestimación de faroles ante all-ins), recortando las quiebras de 57.5% a **42.5%** en 10 rondas y duplicando el rendimiento frente a mesas duras.

---

## Mecánica de juego: qué hace Pokalatro en cada turno (~18 ms)

Cada vez que la mesa envía `solicitar_accion`, Pokalatro ejecuta una secuencia de 5 pasos en Python puro:

### 1. Lectura exacta de mesa y abstracción (`app/turno.py`, `app/abstraccion.py`)
- Calcula la posición real respecto al botón ignorando asientos vacíos o eliminados.
- Calcula **pot odds reales** descontando cualquier exceso de apuesta que el rival no pueda igualar contra nuestro stack.
- Clasifica la mano en **6 niveles hechos** (`made_tier`: desde aire hasta escalera/color/full+) y **4 niveles de proyecto propio** (`draw_tier`, verificando que la escalera o color realmente use nuestras cartas), además de detectar bloqueadores a las *nuts* (`nut_blocker`), humedad del tablero y relación stack-pozo (`SPR`).

### 2. Modelado bayesiano continuo de rivales (`app/rangos.py`, `app/stats.py`)
- En lugar de recortar manos con umbrales rígidos, mantiene una distribución ponderada sobre las **1 326 combinaciones posibles** por rival:
  $$P(\text{mano} \mid \text{acciones}, \text{stats}) \propto \text{Prior}_{\text{preflop}}(\text{mano}) \times \prod_t L(\text{acción}_t \mid \text{mano}, \text{tablero}, \text{stats})$$
- **Capping pasivo:** si un rival con PFR normal solo paga o hace *check* en BB preflop, atenúa el peso de manos premium (`AA/KK/QQ/AK`).
- **Calibración en vivo con decaimiento:** actualiza `VPIP`, `PFR`, `3-bet`, `fold_post`, `c-bet`, frecuencia de apuesta en river y fuerza promedio observada al apostar/ir all-in en showdowns, aplicando decaimiento exponencial (`DECAY`) para reaccionar si un rival cambia de estrategia a mitad del torneo.

### 3. Decisión Preflop (`app/preflop.py`)
- **Stack corto ($\le 13\text{ BB}$):** consulta la tabla **Nash Push/Fold** precomputada por CFR+ (interpolada entre 5, 8, 11 y 14 BB y diferenciada entre heads-up y multiway).
- **Stack medio/profundo:** mezcla la política de equilibrio (`PREFLOP_DEEP`) con ajuste posicional, aislamiento selectivo contra *limpers*, *3-bets* por valor o presión contra rivales con alto *fold* preflop y defensa acotada frente a resubidas grandes.

### 4. Decisión Postflop y Subjuego en River (`app/postflop.py`, `app/subjuego.py`)
- Calcula la **equity Monte Carlo** contra los rangos bayesianos ponderados de los rivales activos, con corte estricto por presupuesto temporal (`PRESUPUESTO_S`) para no arriesgar un timeout.
- Ajusta la equity por **realización** (posición y proyectos por venir) y consulta el **Blueprint MCCFR** como ancla cuando hay pocas manos observadas del rival.
- **Explotación regularizada:** frente a *estaciones de pago* cobra más grande por valor y elimina los faroles puros; frente a *nits* presiona más en botes sin dueño; frente a perfiles hiperagresivos (`loco`) tiende trampas (*check-raise* / *slowplay*) manteniendo márgenes de seguridad en calles caras.
- **Resolución exacta en River HU (`app/subjuego.py`):** en botes importantes de river contra un rival, evalúa el valor esperado exacto (sin ruido Monte Carlo) de `check`, `bet` (`0.45p`, `0.80p`, `1.10p`), `call` y `raise` integrando sobre las combinaciones vivas del rival y regularizando la frecuencia de defensa mediante **MDF** (*Minimum Defense Frequency*).

### 5. Gestión de torneo y blindaje de producción (`app/torneo.py`, `app/estrategia.py`, `app/conexion.py`)
- **Supervivencia y presión (`app/torneo.py`):** en decisiones que comprometen una fracción alta del stack exige un margen adicional de equity (`minimo_por_supervivencia`), mientras que cuando cubrimos a stacks cortos aplica `factor_presion_eliminacion`.
- **Cero acciones inválidas y cero caídas:** toda jugada pasa por un filtro final contra `acciones_validas` (ajustando montos al mínimo legal y declarando `allin` cuando corresponde). Ante cualquier excepción inesperada, responde una acción segura (`check` o `fold`), y el flag `-persistente` reconecta automáticamente con el mismo token para recuperar asiento y fichas ante cortes de red.

---

## Arquitectura híbrida (5 componentes)

1. **Abstracción estratégica (`app/abstraccion.py`):** orden realista de jugabilidad para 169 manos iniciales y clasificación postflop (`made_tier`, `draw_tier`, `nut_blocker`, `SPR`, textura y `facing`).
2. **Blueprint offline CFR+ / Linear MCCFR (`app/blueprint_data.py`, `app/blueprint_6max.py`, entrenados con `tools/trainer_cfr.cpp` y `tools/trainer_pluribus_6max.cpp`):** tablas Push/Fold, `PREFLOP_DEEP`, 3M iteraciones MCCFR postflop y 2.5M manos 6-max simultáneas en Linear External-Sampling MCCFR.
3. **Modelado bayesiano continuo (`app/rangos.py` + `app/stats.py`):** verosimilitud sobre 1 326 combos, *capping* pasivo, decaimiento estadístico y calibración por showdowns.
4. **Explotación regularizada (`app/preflop.py` + `app/postflop.py`):** adaptación acotada por confianza estadística (`estacion`, `nit`, `loco`) y control de riesgo en re-subidas y semi-faroles.
5. **Subjuego River HU + Gestión de Torneo (`app/subjuego.py` + `app/torneo.py`):** EV exacto en river HU con MDF y prima de supervivencia ajustada a PokerFight.

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
| **Suite de pruebas (`tests/`)** | — | **32 / 32 OK** | 0 fallos, 0 timeouts |

Detalle experimental completo (incluyendo estudio de ablación de los 5 componentes) en [`docs/EXPERIMENTOS.md`](docs/EXPERIMENTOS.md).

---

## Estructura de carpetas

| Ruta | Contenido |
| --- | --- |
| `bot.py` / `conectar.bat` | Lanzadores compatibles desde la raíz (delegan en `app/bot.py`). |
| [`app/`](app/) | Bot de producción en Python 3.8+ puro (`estrategia.py`, `preflop.py`, `postflop.py`, `motor.py`, `abstraccion.py`, `blueprint_data.py`, `blueprint_6max.py`, `rangos.py`, `stats.py`, `subjuego.py`, `torneo.py`, `turno.py`, `conexion.py`, `tabla_preflop.py`, `bot.py`). |
| [`tests/`](tests/) | Pruebas unitarias, de protocolo TCP real y fuzz ([`test_estrategia.py`](tests/test_estrategia.py), [`test_v9.py`](tests/test_v9.py)). |
| [`docs/`](docs/) | [`INFORME_INVESTIGACION.md`](docs/INFORME_INVESTIGACION.md), [`AUDITORIA_V8.md`](docs/AUDITORIA_V8.md), [`ARQUITECTURA_V9.md`](docs/ARQUITECTURA_V9.md), [`EXPERIMENTOS.md`](docs/EXPERIMENTOS.md), [`CHANGELOG.md`](docs/CHANGELOG.md), [`LEEME-EJECUTAR.md`](docs/LEEME-EJECUTAR.md), [`RESULTADOS.md`](docs/RESULTADOS.md). |
| [`tools/`](tools/) | Simulador fiel de PokerFight (`simulador_pf.py`), bots del kit (`bots_kit.py`), evaluador C++ rápido (`fast_eval.cpp`), entrenadores CFR+/Linear MCCFR (`trainer_cfr.cpp`, `trainer_pluribus_6max.cpp`), benchmark estadístico (`benchmark.py`) y arena Slumbot2019 (`slumbot_arena.py`). |
| [`backup_v8/`](backup_v8/) | Copia intacta de referencia de Pokalatro v8. |

---

## Ejecución rápida

```powershell
# Ejecutar las 32 pruebas automatizadas
python -m unittest discover -s tests -v

# Conectar el bot al servidor de PokerFight (o vía app/bot.py)
python bot.py -addr HOST:PUERTO -token "$env:TOKEN" -persistente

# Reproducir benchmark pareado o torneo de 10 rondas
python -m tools.benchmark --n-mesas 200 --seed 2026 --modo comparar --rivales kit
python -m tools.benchmark --n-mesas 40 --seed 2026 --modo torneo10
```

Ver guía operativa completa en [`docs/LEEME-EJECUTAR.md`](docs/LEEME-EJECUTAR.md).

# Arquitectura de Pokalatro v9

## 1. Visión General

Pokalatro v9 implementa una **arquitectura híbrida en cinco componentes**, manteniendo el 100% de compatibilidad con el protocolo TCP/JSON-Lines de PokerFight, Python 3.8+ puro sin dependencias externas en producción, y un presupuesto estricto de latencia (< 100 ms por turno frente al límite de 5 000 ms).

```
Mensaje JSON ("solicitar_accion")
        │
        ▼
┌──────────────────────────────────────────────────────────┐
│ Turno (turno.py)                                         │
│ • Orden posicional real sin huecos por eliminaciones     │
│ • Pot odds reales (descontando exceso no igualable)      │
│ • Stack efectivo, SPR, geometría del pozo, textura       │
└────────────────────────┬─────────────────────────────────┘
                         │
        ┌────────────────┴────────────────┐
        ▼                                 ▼
┌──────────────────────┐        ┌──────────────────────────┐
│ Modelo Bayesiano     │        │ Abstracción + Blueprint  │
│ (stats.py, rangos.py)│        │ (abstraccion.py,         │
│ • Priors Beta +      │        │  blueprint_data.py)      │
│   decaimiento γ      │        │ • Estrategia base MCCFR/ │
│ • Verosimilitud por  │        │   CFR+ entrenada offline │
│   1326 combos        │        │ • Preflop (169 × pos ×   │
│ • Rangos topeados,   │        │   stack × línea)         │
│   sizing y showdowns │        │ • Postflop (made × draw ×│
└──────────┬───────────┘        │   textura × SPR × pos)   │
           │                    └─────────────┬────────────┘
           └────────────────┬─────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────┐
│ Motor de Decisión Híbrida + Gestión de Torneo            │
│ (preflop.py, postflop.py, torneo.py)                     │
│ • Mezcla regularizada Blueprint ↔ Explotación (λ_r)      │
│ • Utilidad de torneo PokerFight (supervivencia + push)   │
│ • Subjuego local exacto en river/turn crítico            │
│   (subjuego.py, presupuesto acotado)                     │
└────────────────────────┬─────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│ Filtro de Seguridad (estrategia.py)                      │
│ • Validación estricta contra `acciones_validas`          │
│ • Subida mínima, conversión a `allin`, red `try/except`  │
└──────────────────────────────────────────────────────────┘
```

---

## 2. Componentes Detallados

### Componente 1: Abstracción Estratégica y Blueprint Base (MCCFR / CFR+)
- **Archivos**: `abstraccion.py`, `blueprint_data.py` (generado offline por `tools/entrenar_blueprint.py` / `tools/trainer_cfr.cpp`).
- **Por qué CFR/MCCFR y no solo reglas**:
  - Las reglas heurísticas de v8 son vulnerables ante rivales que aprenden patrones o castigan frecuencias extremas (0% o 100%).
  - Un **Blueprint** entrenado offline mediante **External-Sampling MCCFR / CFR+** sobre un juego abstracto de Hold'em (incluyendo escenarios multijugador y heads-up) proporciona probabilidades de acción mixtas y equilibradas como punto de anclaje cuando hay poca muestra del rival.
- **Abstracción de cartas (2 ejes ortogonales, no 1-D EHS)**:
  - **Preflop**: ranking de jugabilidad real (combinando equity 1v1, equity 4-way y potencial de conectividad/suited para botes multiway, corrigiendo el sesgo de `ORDEN` en v8) + 169 clases × posición efectiva (`EP`, `MP`, `CO`, `BTN`, `SB`, `BB`, `HU_SB`, `HU_BB`) × profundidad efectiva (`<=12bb`, `12-28bb`, `>28bb`) × línea previa.
  - **Postflop**: `(calle, made_tier, draw_potential, textura, spr_bucket, posicion, multiway, enfrentando)`:
    - `made_tier` (0..6): aire, par débil/mesa, par medio/top débil, top pair fuerte/overpair, doble par, trío/set, escalera/color/full+.
    - `draw_potential` (0..3): ninguno, gutshot/overcards, proyecto 8–9 outs (OESD/FD), combo draw 12+ outs.
    - `spr_bucket`: bajo ($\le 2.5$), medio ($2.5\text{–}6$), alto ($> 6$).
- **Abstracción de apuestas y traducción**:
  - Buckets de sizing: `check/call`, `small (0.40p)`, `med (0.70p)`, `big (1.10p)`, `allin`.
  - Mapeo pseudo-armónico / interpolación para clasificar apuestas rivales continuas.

### Componente 2: Modelo Probabilístico Bayesiano de Rivales
- **Archivos**: `stats.py`, `rangos.py`, `motor.py`.
- **Estadísticas acumuladas con decaimiento**:
  - Contadores flotantes con decaimiento exponencial por mano ($\gamma = 0.996$) para seguir cambios de estrategia (como `bandido`), manteniendo priores Beta conservadores para evitar sobreajuste con pocas manos ($< 10$).
  - Métricas separadas por contexto: `vpip`, `pfr`, `three_bet`, `fold_to_open`, `fold_to_3bet`, `cbet_flop`, `fold_to_cbet`, `fold_post_bet`, `fold_post_raise`, `agresion_post`, `frecuencia_river_bet`, `tasa_allin_pre`, `tasa_allin_post`, `fuerza_showdown_bet`, `fuerza_showdown_allin`, `rango_showdown_voluntario` (sin contaminar con checks gratis en BB).
- **Actualización Bayesiana sobre 1 326 combinaciones**:
  - Cada rival mantiene un vector de pesos $w_r[0..1325]$ (eliminando inmediatamente cartas bloqueadas por la mano propia y el board).
  - **Prior preflop según acción**:
    - *Open raise / 3-bet / 4-bet*: concentrado alrededor de su `pfr` / `3bet` real y ajustado por su perfil (`aleatorio`/`maníaco` mantiene cola ancha).
    - *Cold call / Limp*: **rango topeado (*capped*)** que reduce drásticamente el peso de monstruos que hubieran resubido preflop (según su `3bet` / `pfr`), y da peso realista a conectores suited y pares chicos/medios.
    - *BB check*: rango completo menos las manos que habría subido.
  - **Verosimilitud postflop $\mathcal{L}(a_t \mid h, s_t, \theta_r)$**:
    - Usa la fuerza real de cada combo en el board actual + potencial legítimo de proyecto (corrigiendo el bug de proyectos en el board de v8).
    - Pondera según el tamaño de la apuesta (`bet / pot`), la calle, si hizo check previo, y su calibración de showdowns (`fuerza_showdown_bet`) + frecuencia en river (`frecuencia_river_bet`).
    - **Cero fallbacks destructivos**: si los pesos quedan muy concentrados por bloqueadores, normaliza los pesos existentes en lugar de reemplazarlos por el top 15%.

### Componente 3: Estrategia Híbrida Explotativa Regularizada
- **Archivos**: `preflop.py`, `postflop.py`.
- **Mezcla con confianza $\lambda_r$**:
  - Para cada rival o mesa, la confianza estadística $\lambda = \frac{N_{\text{eff}}}{N_{\text{eff}} + M_0}$ regula cuánto nos desviamos del Blueprint hacia la respuesta explotativa:
    - $\lambda \to 0$ (primeras manos contra rival nuevo): predomina el Blueprint equilibrado.
    - $\lambda \to 1$ (rival conocido): explota fugas específicas:
      - *Contra pagadores / estaciones* (`fold_post` bajo o `darwin`/`estadistico` que pagan amplio): suprime faroles puros, amplia valor fino y aumenta tamaño de valor (`0.75p–1.05p`).
      - *Contra nits / over-folders* (`fold_pre` o `fold_post` altos): incrementa robos de ciegas, c-bets en tableros secos y semi-faroles con bloqueadores.
      - *Contra maníacos / `aleatorio`*: ensancha el rango de *bluff-catch* (call) contra rangos diluidos y permite *slowplay*/trampas con monstruos cuando el rival apuesta por sí solo.
      - *Anti-explotación*: mantiene aleatorización controlada en manos frontera y limita desviaciones extremas cuando un rival tiene alta tasa de check-raise o adaptación.

### Componente 4: Evaluación Local de Situaciones Críticas (`subjuego.py`)
- Se activa en **river** (y **turn** heads-up) cuando hay una decisión de alto impacto (pozo $\ge 8\text{ bb}$, o `compromiso >= 0.25`, o enfrentando apuesta/subida significativa) y el presupuesto temporal restante lo permite:
  - **Enumeración exacta en river HU**: calcula la equidad exacta contra los pesos bayesianos del rival (0 varianza Monte Carlo, ~1–3 ms).
  - **Árbol local de EV / CFR+ de 1 calle**: estima el EV de `fold`, `call`, y tamaños candidatos (`0.45p`, `0.80p`, `allin`) integrando la probabilidad estimada de fold/call/raise del rival (regularizada entre modelo bayesiano y MDF de equilibrio) sobre su distribución posterior de manos.

### Componente 5: Gestión de Torneo PokerFight (`torneo.py`)
- Modela la utilidad real de PokerFight:
  1. **Push/Fold para stacks cortos ($\le 14\text{ bb}$ efectivos)**: tablas y cálculos de *fold equity* + equidad contra rangos reales de call, evitando foldear pasivamente hasta morir en ciegas ni pagar all-ins con manos dominadas.
  2. **Factor de Riesgo de Ruina / Supervivencia (*Skill-Weighted Bubble Factor*)**:
     - En mesas de 50 manos y clasificatoria de 10 rondas, quedar en 0 fichas corta toda la cosecha futura de EV positivo.
     - Cuando una decisión arriesga gran parte del stack ($\text{compromiso} > 0.30$) y no estamos obligados por pot odds muy favorables ($\text{pot\_odds} > 0.28$), exige un margen de equidad adicional acorde al riesgo de eliminación y al tamaño de pila propio vs la mesa, siendo más estricto con manos de una sola pareja (`hecha <= 1`) en pozos sin compromiso previo.
     - Cuando cubrimos a rivales cortos y tenemos ventaja, ejerce presión para eliminarlos y capturar sus fichas.

---

## 3. Presupuesto de Latencia (Objetivo < 50 ms mediano, < 250 ms máximo)

| Fase del turno | Presupuesto típico | Límite duro |
| :--- | :---: | :---: |
| Parseo + `Turno` + `Abstracción` | < 1 ms | 2 ms |
| Consulta Blueprint | < 0.1 ms | 1 ms |
| Actualización Bayesiana de rangos | 2–5 ms | 15 ms |
| Equity exacta (river HU) / Monte Carlo estratificado | 3–18 ms | 75 ms |
| Subjuego local (si aplica) | 2–10 ms | 35 ms |
| **Total por decisión** | **8–35 ms** | **< 130 ms** (vs 5 000 ms límite arena) |

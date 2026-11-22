# Informe de Investigación Documental: Poker AI y Game Theory aplicada a PokerFight

## 1. Resumen Ejecutivo

Este informe sintetiza la literatura académica y las implementaciones de referencia en inteligencia artificial para póker de información imperfecta (Texas Hold'em No-Limit), evaluando su aplicabilidad directa al formato competitivo de **PokerFight** (mesas de 5–6 jugadores, 10 000 fichas iniciales, ciegas fijas 50/100, clasificatoria de 10 rondas con conservación de fichas y eliminación hasta 50 manos / 6 minutos por mesa, final de 6 jugadores *winner-take-all*, plazo máximo de 5 s por turno, ejecución en Python 3.8+ puro sin dependencias externas).

Conclusión central: las técnicas diseñadas exclusivamente para **Heads-Up (2 jugadores) suma cero** (como Slumbot o DeepStack en su forma pura) **no son transferibles sin adaptación** a mesas de 5 jugadores con formato de torneo y rivales heterogéneos no equilibrados. La arquitectura óptima para PokerFight es un **sistema híbrido en cinco capas** inspirado en *Pluribus* (Brown & Sandholm, 2019), el modelado bayesiano de *Bayes' Bluff* / *Safe Opponent Exploitation* (Southey et al., 2005; Ganzfried & Sandholm, 2015) y la utilidad de supervivencia ponderada por ventaja (*risk-aware tournament EV*):

1. **Blueprint Strategy offline** entrenada mediante **External-Sampling MCCFR** (y **CFR+** en subjuegos compactos) sobre una abstracción estratégica multijugador y exportada como tablas livianas en Python puro.
2. **Actualización Bayesiana de Rangos por Combinación (1 326 combos)** mediante verosimilitud de acciones (*Action Likelihood Models*), efectos de bloqueo (*blockers*), rangos topeados (*capped ranges*) y calibración con showdowns.
3. **Estrategia Explotativa Regularizada (Safe Exploitation)**: mezcla controlada entre el blueprint de equilibrio y la mejor respuesta frente al perfil bayesiano del rival, evitando el sobreajuste con muestras pequeñas.
4. **Resolución Local / Subjuego en Situaciones Críticas** (cálculo exacto de equity y EV de árbol corto en river/turn para pozos grandes o decisiones de all-in, dentro de un presupuesto estrictamente acotado de < 100 ms).
5. **Modelo de Utilidad de Torneo para PokerFight**: función de utilidad cóncava adaptativa calibrada al formato real de ciegas fijas, 50 manos por ronda, conservación de fichas entre rondas y alto costo de eliminación prematura.

---

## 2. Análisis de Slumbot2019 y Familia CFR

### 2.1 Repositorio estudiado: `ericgjackson/slumbot2019`

El repositorio público de Eric Jackson implementa en C++17 el pipeline completo que ganó competencias de Heads-Up No-Limit Hold'em (HUNL):

- **CFR+ (`cfrp.cpp`)**: implementa *Regret-Matching+* (piso instantáneo en 0 para arrepentimientos negativos: $R^{+,T}(I,a) = \max(R^{+,T-1}(I,a) + r^T(I,a), 0)$) y promediado lineal de estrategias (iteraciones tardías ponderan $t$). Converge drásticamente más rápido que Vanilla CFR en árboles donde es viable recorrer todo el árbol.
- **External Sampling MCCFR (`ecfr.cpp`)**: implementa muestreo externo de Monte Carlo CFR. En cada iteración para el jugador actualizador $i$, se recorren **todas** sus acciones en sus nodos de decisión, mientras que en los nodos de azar (cartas) y de los rivales se muestrea **una** trayectoria según la estrategia actual del rival. Esto reduce el costo por iteración en órdenes de magnitud manteniendo un estimador insesgado del arrepentimiento contrafactual.
- **Targeted CFR (`tcfr.cpp`)**: variante experimental de muestreo dirigido hacia subárboles de mayor impacto o probabilidad de alcance, útil en árboles enormes de 4 calles.
- **Abstracción de cartas y apuestas (`build_rollout_features`, `build_kmeans_buckets`, `build_betting_tree`)**: agrupa manos en *buckets* por características de rollout/fuerza y discretiza el árbol de apuestas.
- **Endgame Resolving (`endgame`) & Evaluación (`head_to_head`, `best_response`)**: resuelve en tiempo real el subjuego tardío (p. ej. turn/river) partiendo de los rangos alcanzados por el blueprint, y mide la calidad mediante enfrentamientos directos y cálculo de *Best Response* (explotabilidad).

### 2.2 Fundamentos teóricos de CFR y MCCFR

| Algoritmo / Concepto | Referencia | Mecanismo central | Viabilidad en PokerFight |
| :--- | :--- | :--- | :--- |
| **CFR (Vanilla)** | Zinkevich et al. (2007) | Minimiza arrepentimiento contrafactual local por Information Set $I$: $\sigma^{T+1}(I,a) = \frac{R^{+,T}(I,a)}{\sum_b R^{+,T}(I,b)}$. La estrategia promedio $\bar{\sigma}^T$ converge a equilibrio de Nash en 2P suma cero a tasa $O(1/\sqrt{T})$. | Inviable recorrer árbol completo de Hold'em 5-max sin abstracción agresiva. |
| **MCCFR (External / Outcome)** | Lanctot et al. (2009) | Estima valores contrafactuales muestreando nodos de azar y rivales (*External Sampling*) o trayectorias completas (*Outcome Sampling*). | **Altamente viable** offline en C++ para entrenar un blueprint multijugador/abstracto y exportarlo a Python. |
| **CFR+** | Tammelin (2014) | *Regret-Matching+* (no acumula arrepentimientos negativos) + actualizaciones alternadas + promedio lineal. | **Viable** para entrenamiento offline en abstracciones compactas (preflop / river) y resolución rápida de subjuegos locales. |
| **Multiplayer MCCFR (Pluribus)** | Brown & Sandholm (2019) | External-Sampling MCCFR + poda de arrepentimientos negativos (*negative-regret pruning*) en 6-max. En >2 jugadores no garantiza convergencia única a Nash, pero produce estrategias altamente equilibradas y no trivialmente explotables. | **Alineado** con mesas de 5–6 jugadores de PokerFight para generar el blueprint base. |

### 2.3 Limitaciones de Slumbot para PokerFight

1. **Diseñado para Heads-Up (2 jugadores)**: en 5 jugadores existen rangos multiway, órdenes de posición distintos (UTG, MP, CO, BTN, SB, BB), múltiples callers (*cold calls*, *squeeze*, *limp pots*) y *side pots*. Un blueprint HUNL sobre-agrede preflop y postflop en botes multijugador.
2. **Juego Cash (cEV puro) vs Torneo**: Slumbot maximiza valor esperado en fichas por mano con reposición constante; PokerFight penaliza la quiebra al cortar rondas futuras de acumulación y clasificar por fichas totales en clasificatoria.
3. **Oponentes subóptimos no equilibrados**: un bot estrictamente Nash/GTO deja de extraer valor masivo contra bots deterministas o con sesgos fijos (`darwin`, `estadistico`, `base`, `novato`, `conservador`, `aleatorio`). Por ejemplo, contra `conservador` o `novato`, farolear con frecuencia GTO pierde EV si no foldean; contra `darwin` (`margen_pago_post = 1.18`), las apuestas de valor grandes (90–100% pozo) ganan mucho más que el sizing balanceado.
4. **Tamaño de tablas vs Python puro**: las tablas completas de Slumbot ocupan gigabytes en C++; para Pokalatro deben comprimirse a decenas/centenares de kilobytes en estructuras nativas de Python (`dict` / `tuple` / `list`).

---

## 3. Abstracción de Estados y Acciones

### 3.1 Abstracción de Cartas: Fuerza vs Comportamiento Estratégico

La literatura distingue tres generaciones de abstracción de cartas:

1. **Expected Hand Strength ($EHS$)** (Billings et al., 1998): resume una mano en un escalar (probabilidad de ganar hoy + potencial de mejora). **Deficiencia crítica**: agrupa en el mismo bucket manos con igual equity promedio pero comportamiento estratégico opuesto (ej., un proyecto de color con carta alta $A\spades 5\spades$ con ~36% de equity vs una tercera pareja débil $6\heartsuit 6\diamondsuit$ con ~36% de equity). La primera quiere semi-farolear/presionar por su *fold equity* y *nut potential*; la segunda quiere controlar el pozo y llegar barato al showdown.
2. **Distribution-Aware Abstraction con Earth Mover's Distance (EMD)** (Johanson et al., 2013): agrupa manos comparando el histograma completo de fuerza futura en el river usando la distancia EMD (costo mínimo de transporte entre distribuciones).
3. **Potential-Aware Imperfect-Recall Abstraction** (Ganzfried & Sandholm, 2014): extiende EMD a trayectorias multi-calle (*potential-aware*), permitiendo olvido controlado de buckets pasados (*imperfect recall*) para concentrar resolución en la calle actual.

**Decisión de diseño para Pokalatro v9**:
- Separar explícitamente la representación de la mano en **dos ejes ortogonales** (no un único escalar de equity):
  - **Fuerza hecha relativa al board (`made_tier`)**: aire/carta alta, par débil/medio, top pair buen kicker, overpair/top pair fuerte, doble par+, trío/set+, escalera/color/full+.
  - **Potencial de mejora y bloqueadores (`draw_potential`)**: sin proyecto, proyecto débil/gutshot/overcards, proyecto fuerte (8+ outs: OESD, flush draw), combo draw (12+ outs), bloqueador de las nuts (p. ej. As del palo de color).
- Esto permite que tanto el **Blueprint CFR** como la política postflop distingan automáticamente entre valor puro, *bluff-catcher*, semi-farol con equidad de proyecto y farol puro con bloqueadores.

### 3.2 Abstracción de Apuestas, Geometría del Pozo y SPR

- **Stack-to-Pot Ratio ($\text{SPR} = \frac{\text{stack efectivo}}{\text{pozo}}$)**: determina el compromiso de la mano:
  - $\text{SPR} \le 1.5$: casi cualquier top pair / proyecto fuerte está comprometido (*pot-committed*).
  - $1.5 < \text{SPR} \le 4$: tamaño ideal para jugar por todo el stack con TPTK/overpair y combo draws.
  - $\text{SPR} > 7$: una sola pareja pierde valor relativo (peligro de *reverse implied odds*); las manos especulativas (pares chicos para set, conectores suited) ganan *implied odds*.
- **Geometría de Apuestas (Geometric Sizing)**: tamaño fraccional $f$ constante por calle restante $k \in \{1,2,3\}$ tal que el stack efectivo se comprometa exactamente en el river: $(1 + 2f)^k = 1 + 2\cdot \text{SPR} \implies f = \frac{(1 + 2\cdot\text{SPR})^{1/k} - 1}{2}$.
- **Action Translation (Mapeo Pseudo-Armónico)** (Ganzfried & Sandholm, 2013): cuando un rival usa un tamaño de apuesta continuo $x$ fuera de los buckets discretos $(a, b)$ del árbol abstracto, se interpola probabilísticamente con la fórmula pseudo-armónica $p(a) = \frac{(b - x)(1 + a)}{(b - a)(1 + x)}$, evitando errores de redondeo burdos ante apuestas raras (como las de `aleatorio`).

---

## 4. Resolución en Tiempo Real vs Estrategias Precomputadas

### 4.1 DeepStack y Libratus/Pluribus

- **DeepStack** (Moravčík et al., 2017): elimina las abstracciones globales fijas usando *Continual Re-solving* (CFR-D limitado a la calle actual) apoyado en *Counterfactual Value Networks* (redes neuronales que predicen el vector de valores contrafactuales al final de la calle). Requiere redes neuronales pesadas entrenadas con millones de ejemplos y ~1–5 s en GPU/C++ por turno.
- **Pluribus** (Brown & Sandholm, 2019): combina un blueprint rápido (lookup O(1)) con búsqueda en subjuego limitada en profundidad (1–2 calles) asumiendo un conjunto reducido de estrategias de continuación posibles en las hojas.

### 4.2 Viabilidad en PokerFight (Python puro, < 100 ms objetivo)

- **DeepStack con redes neuronales**: **No viable ni recomendable** en Python puro sin NumPy/PyTorch y con presupuesto de < 100 ms (además de innecesario frente al ecosistema de bots y el riesgo de timeout).
- **Resolución exacta en River y Turn corto (Subgame / Local Solver)**: **Altamente viable** cuando quedan 1–2 rivales, el pozo es significativo ($\ge 10\text{ bb}$ o decisión de all-in) y el tablero ya tiene 4 o 5 cartas:
  - En el **river** ($0$ cartas por repartir), contra 1 rival existen $\le 990$ combinaciones posibles (con pesos bayesianos del rango rival). Podemos calcular **EV exacto sin varianza Monte Carlo** para `fold`, `call`, `bet/raise (0.5x, 0.85x, allin)` en **1–3 ms** en Python puro, e incluso ejecutar **15–30 iteraciones de CFR+ / Best-Response regularizado** sobre el subjuego de river de 1 calle en **< 15 ms**!
  - En **preflop y flop**, consultar el **Blueprint precomputado** cuesta **< 0.1 ms**, reservando el presupuesto temporal para Monte Carlo / evaluación exacta de rangos bayesianos.

---

## 5. Modelado Probabilístico de Rivales y Explotación Segura

### 5.1 Actualización Bayesiana de Rangos (*Bayesian Range Updating*)

Basado en Southey et al. (*Bayes' Bluff*, 2005) y Ganzfried & Sandholm (2011, 2015):

En lugar de mantener un corte binario ("está en el top X%"), el estado oculto de la mano del rival $r$ se representa como una distribución de probabilidad $P_r(h)$ sobre todas las $1\,326$ manos iniciales $h = (c_1, c_2)$ compatibles con las cartas visibles (bloqueadores del héroe y del tablero, eliminados automáticamente).

Tras observar una secuencia de acciones $a_1, a_2, \dots, a_t$ en los estados $s_1, \dots, s_t$:

$$P_r(h \mid a_{1:t}, \theta_r) \propto P_r(h \mid \text{posición}) \prod_{k=1}^{t} \mathcal{L}(a_k \mid h, s_k, \theta_r)$$

donde $\mathcal{L}(a_k \mid h, s_k, \theta_r)$ es el **Action Likelihood Model** condicionado a:
- **Tipo de acción y tamaño relativo** (check, call, min-bet, 0.5x pot, 1x+ pot, overbet, all-in).
- **Perfil estimado del rival $\theta_r$** (VPIP, PFR, 3-bet, c-bet, fold-to-bet por calle, agresión en river, fuerza media al apostar en showdown, frecuencia all-in).
- **Separación explícita de componentes**:
  - **Valor**: manos fuertes en la textura actual (`made_tier` alto).
  - **Semi-farol / Proyecto**: proyectos de color o escalera en calles con cartas por venir.
  - **Farol puro**: manos débiles con mayor peso cuando el rival muestra alta frecuencia de farol / agresión excesiva en river o *missed draws* con bloqueadores.
  - **Rango topeado (*capped*)**: si el rival hizo `call` preflop o `check/call` pasivo, la verosimilitud de manos monstruo (AA/KK preflop o sets/nuts en river si es agresivo) se atenúa según su propensión histórica a subir con monstruos.

### 5.2 Suavizado, Incertidumbre y Decaimiento Temporal

- **Priores Beta-Binomiales / Dirichlet** con masa efectiva $M_0$ (p. ej. $M_0 = 8\text{–}15$ observaciones): con pocas manos del rival, el peso de explotación $\lambda_r = \frac{n_r}{n_r + M_0}$ tiende a 0 y domina el **Blueprint**; a medida que $n_r$ crece (en 10 rondas de 50 manos se observan hasta 150–400 manos de rivales frecuentes), $\lambda_r \to 1$.
- **Decaimiento exponencial (*forgetting factor* $\gamma \approx 0.995$ por mano)**: permite detectar rápidamente a bots adaptativos como `bandido` (que alterna entre *roca*, *tiburón*, *maníaco* y *pagador* con UCB1) o rivales que cambian de estrategia entre rondas.
- **Separación limpia en Showdown**: solo contabilizar el percentil preflop voluntario cuando el rival invirtió fichas voluntariamente (excluyendo manos donde solo defendió gratis en BB sin subir), y registrar por separado la fuerza al apostar/all-in en postflop y en all-in preflop.

---

## 6. Estrategia en Torneos Multijugador (Formato PokerFight)

### 6.1 Análisis Matemático del Formato de PokerFight

El reglamento oficial (`docs/competencia.md`) define dos fases:
1. **Clasificatoria**: 10 rondas de hasta 50 manos (o 6 min) en mesas de 5 (o 6), **ciegas fijas 50/100**, stack inicial 10 000 (100 bb). Quien llega a 0 fichas queda eliminado. Las fichas se conservan entre rondas. Clasifican los **6 con más fichas**.
2. **Final**: mesa de 6 jugadores, 10 000 fichas iniciales, ciegas fijas 50/100, *winner-take-all* (1 hora o último en pie).

### 6.2 ¿Es aplicable el ICM clásico (Malmuth-Harville)?

El **ICM clásico** asume: (a) premios escalonados en una sola mesa donde la supervivencia pasiva sube de puesto, y (b) **habilidad igual** entre todos los jugadores (probabilidad de 1.º igual a $S_i / \sum S_j$).

En PokerFight, ambas hipótesis requieren ajuste:
1. **En la Final (*winner-take-all*)**, el ICM puro para premios `[1, 0, 0, 0, 0, 0]` colapsa a **cEV lineal** si todos tienen la misma habilidad. **Sin embargo**, cuando nuestro bot tiene ventaja positiva ($\mu > 0$ bb/100 frente al campo), **sobrevivir tiene un valor de opción positivo**: quedar eliminado en la mano 10 sacrifica el EV acumulado de las manos 11..50 (o de las rondas 2..10 en clasificatoria)! En `RESULTADOS.md` de Pokalatro v8 se verificó empíricamente que reducir las quiebras del 22% al 12% preservando equity en botes grandes aumentó la estabilidad y el rendimiento neto.
2. **Modelo recomendado — Utilidad Cóncava Ponderada por Ventaja y Presión de Stack**:
   - Definimos una aversión al riesgo de ruina dinámica $\kappa$ (equivalente a un *Bubble Factor* local entre $1.00$ y $1.25$ cuando el riesgo de eliminación en la jugada es alto $\frac{\text{costo}}{\text{stack}} > 0.35$, atenuándose cuando el pozo ya ofrece pot odds altas $\le 0.30$ o cuando estamos cortos $< 15\text{ bb}$ donde **evitar desangrarse y maximizar fold equity mediante Push/Fold Nash** es óptimo).
   - **Multiway Equity y Realización**: contra $k \ge 2$ rivales, la probabilidad de llevarse el pozo sin mejorar disminuye no linealmente y la realización de equidad (especialmente OOP con pares bajos/conectores) cae; además, se incorpora **presión de stack efectivo** ($\min(\text{mi stack}, \max \text{stack rival})$) para ajustar implícitas y SPR.

---

## 7. Metodología de Evaluación Estadística

Para evitar conclusiones espurias por la altísima varianza del No-Limit Hold'em en mesas de 50 manos ($\sigma \approx 10\,000$ fichas por mesa; error estándar en 80 mesas $\approx 1\,100$ fichas):

1. **Reducción de varianza por semillas pareadas (*Duplicate / Common Random Numbers*)**: v8 y v9 (y cada ablación) juegan **exactamente las mismas manos, barajas y rotaciones de silla** (las 5 posiciones iniciales rotadas equitativamente), replicando el principio de `ida_y_vuelta` de la arena de PokerFight y reduciendo drásticamente el error estándar de la diferencia $\Delta = \text{v9} - \text{v8}$.
2. **Intervalos de Confianza y Pruebas de Significancia**: IC del 95% para la media pareada $\bar{\Delta} \pm 1.96 \cdot \text{SE}(\Delta)$, prueba t pareada / Wilcoxon y bootstrap estratificado, con $N \ge 200\text{–}500$ mesas por experimento.
3. **Evaluación de Población y Perfiles Adversos**: pruebas contra los 8 bots del kit (`aleatorio`, `conservador`, `novato`, `base`, `estadistico`, `bandido`, `espia`, `darwin`), contra mesas pobladas de copias fuertes (`darwin`/`espia`/`estadistico`), contra un bot **GTO/Explotador sintético** diseñado para castigar patrones previsibles, y contra bots que cambian de estrategia a mitad de partida.
4. **Estudios de Ablación**: desactivar individualmente cada nuevo componente (Blueprint, Bayesiano, Explotación, Subjuego River/Turn, Ajustes de Torneo) sobre las mismas semillas para demostrar empíricamente la contribución marginal de cada módulo.

---

## 8. Referencias Bibliográficas Principales

1. Zinkevich, M., Johanson, M., Bowling, M., & Piccione, C. (2007). *Regret Minimization in Games with Incomplete Information*. NeurIPS.
2. Lanctot, M., Waugh, K., Zinkevich, M., & Bowling, M. (2009). *Monte Carlo Sampling for Regret Minimization in Extensive Games*. NeurIPS.
3. Tammelin, O. (2014). *Solving Large Imperfect Information Games Using CFR+*. arXiv:1407.5042.
4. Jackson, E. (2019). *Slumbot2019: C++ CFR+, External Sampling MCCFR, Targeted CFR & Endgame Solving*. GitHub `ericgjackson/slumbot2019`.
5. Brown, N., & Sandholm, T. (2018). *Superhuman AI for heads-up no-limit poker: Libratus beats top professionals*. Science, 359(6374), 418–424.
6. Brown, N., & Sandholm, T. (2019). *Superhuman AI for multiplayer poker (Pluribus)*. Science, 365(6456), 885–890.
7. Moravčík, M., Schmid, M., Burch, N., Lisý, V., Morrill, D., Bard, N., Davis, T., Waugh, K., Johanson, M., & Bowling, M. (2017). *DeepStack: Expert-level artificial intelligence in heads-up no-limit poker*. Science, 356(6337), 508–513.
8. Johanson, M., Burch, N., Valenzano, R., & Bowling, M. (2013). *Evaluating State-Space Abstractions in Extensive-Form Games*. AAMAS.
9. Ganzfried, S., & Sandholm, T. (2014). *Potential-Aware Imperfect-Recall Abstraction with Earth Mover's Distance in Imperfect-Information Games*. AAAI.
10. Ganzfried, S., & Sandholm, T. (2013). *Action Translation in Extensive-Form Games with Large Action Spaces: Axioms, Paradoxes, and the Pseudo-Harmonic Mapping*. IJCAI.
11. Southey, F., Bowling, M., Larson, B., Piccione, C., Burch, N., Billings, D., & Rayner, C. (2005). *Bayes' Bluff: Opponent Modelling in Poker*. UAI.
12. Ganzfried, S., & Sandholm, T. (2015). *Safe Opponent Exploitation*. ACM Transactions on Economics and Computation.
13. Burch, N., Schmid, M., Moravčík, M., & Bowling, M. (2018). *AIVAT: A New Variance Reduction Technique for Agent Evaluation in Imperfect Information Games*. AAAI.
14. Harville, D. (1973) & Malmuth, M. (1987). *Independent Chip Model (ICM)*; Nelson, L. et al. (2008), *Kill Everyone* (Bubble Factor).

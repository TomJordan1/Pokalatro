# Auditoría Técnica y Estratégica de Pokalatro v8

Este documento audita el código completo de **Pokalatro v8** (`bot.py`, `conexion.py`, `estrategia.py`, `turno.py`, `motor.py`, `rangos.py`, `stats.py`, `preflop.py`, `postflop.py`, `tabla_preflop.py`, `test_estrategia.py`), contrastándolo con el protocolo real de PokerFight (`docs/reglas.md`, `docs/protocolo.md`, `internal/mesa/`, `internal/crupier/`) y los principios de Game Theory.

Todos los hallazgos están verificados directamente sobre los archivos fuente sin modificaciones al código durante esta fase.

---

## 1. Diagnóstico General: Fortalezas vs Debilidades de v8

### Lo que ya funciona sólidamente en v8
1. **Protocolo y Robustez (`conexion.py`, `bot.py`, `estrategia.py`)**:
   - Lectura por líneas JSON, `TCP_NODELAY`, `SO_KEEPALIVE`, manejo de `MESA_SATURADA` con backoff exponencial + jitter, reconexión persistente con mismo token.
   - Filtro estricto `validar(t, accion)` que garantiza pertenencia a `acciones_validas`, corrige a `subida_minima` y convierte subidas al techo en `allin`.
   - Red de seguridad `try/except` con `accion_segura(t)` que garantiza cero cuelgues.
   - Semilla reproducible por situación (`_sembrar(t)`).
2. **Evaluador de 5–7 cartas (`motor.py: evaluar`)**:
   - Implementación correcta con bitmasks `STRAIGHT` y `TOP5`, desempates exactos (incluida rueda A-2-3-4-5) y `categoria_propia` que descuenta parejas/tríos comunitarios.
3. **Filtrado postflop por fuerza en la mesa real (`motor.py: puntajes_en_mesa`)**:
   - Gran acierto conceptual respecto a bots ingenuos: filtra rangos postflop por fuerza en las cartas comunitarias reales y no por percentil preflop.

### Limitaciones estructurales de v8
- Toda la política de decisión es **heurística basada en umbrales fijos** sin estrategia base equilibrada (Blueprint), lo que la expone a explotación por rivales adaptativos o que varien frecuencias.
- El modelado de rangos es **discreto/binario** en lugar de probabilístico bayesiano, pierde información crítica de tamaño de apuesta, capeo de rangos y perfiles específicos (como `aleatorio` vs `darwin`).
- Existen **errores matemáticos y posicionales concretos** en `turno.py`, `motor.py`, `preflop.py`, `postflop.py` y `stats.py`.

---

## 2. Problemas Detectados Ordenados por Impacto Estratégico

### P1 (Crítico) — Error en el cálculo de posición cuando hay jugadores eliminados (`turno.py:153-162`)
- **Código afectado**: `Turno._orden(self, silla, preflop)` en `turno.py`:
  ```python
  if len(self.en_mano) == 2:
      desplazamiento = 0 if preflop else 1
  else:
      desplazamiento = 3 if preflop else 1
  return (silla - boton - desplazamiento) % total
  ```
- **Error**: Asume que la silla de SB es siempre `boton + 1`, la de BB `boton + 2` y el primero en hablar preflop `boton + 3`. Cuando algún jugador en la mesa ya fue eliminado (`Activo=False`, `EnMano=False`), quedan sillas vacías entre `boton` y las ciegas.
  - *Ejemplo real*: Mesa de 5 sillas (0..4), `boton = 0`, silla 1 eliminada, siguen sillas `0 (BTN), 2 (SB), 3 (BB), 4 (UTG)`. Con `desplazamiento = 3`, `_orden(3, preflop=True)` da `(3 - 0 - 3) % 5 = 0`, indicando que **la Ciega Grande habla PRIMERA preflop** y que tiene 3 rivales detrás (`detras() == 3`), cuando en realidad habla ÚLTIMA (`detras() == 0`)!
- **Impacto**: Distorsiona `detras()`, `hablo_ultimo`, `rivales_detras()`, `_apertura(t)` y `_equity_requerida(t)` en todas las manos tras la primera eliminación de la mesa.
- **Solución recomendada**: Calcular el orden exacto recorriendo en sentido horario las sillas presentes en `self.en_mano` desde `posicion_boton`, replicando exactamente `posicionesCiegas` y `primerEnHablarPreflop` de `internal/mesa/turnos.go`.

### P2 (Crítico) — Sesgo en `tabla_preflop.ORDEN` y fallos en rangos premium/callers (`motor.py`, `preflop.py`)
- **Código afectado**: `tabla_preflop.py`, `motor.py:192-194` (`combos_top`), `preflop.py:159-186`.
- **Error**:
  1. `ORDEN` clasifica las 169 manos exclusivamente por equity all-in 1v1 contra una mano al azar (`eq1`). Esto coloca `99` (`pct = 0.0226`), `88` (`0.0332`) y `77` (`0.0498`) por encima de `AQs` (`0.0302`), `AKo` (`0.0377`) y `KQs` (`0.0468`), y coloca manos offsuit basura como `K4o`/`Q7o` por encima de conectores suited jugables (`76s`, `65s`, `54s`).
  2. En `preflop.py:159`, `pct <= 0.030` marca como "premium" a `99` (¡all-in con $\le 35\text{ bb}$!) pero **excluye a `AQs` (`0.0302`) y `AKo` (`0.0377`)**. Y ante un 3-bet (`preflop.py:185`), `pct <= 0.015` solo incluye `AA–JJ`, excluyendo `AKs` y `AKo` del all-in automático.
  3. En `motor.py:420` (`combos_de_rango`), cuando un rival solo **paga** una subida o hace limp (`rangos.py:47-52`), `combos_top(ancho)` le asigna el top `ancho` desde `AA` hacia abajo, cuando un caller preflop casi siempre tiene un rango **topeado (*capped*)** (las manos top 3%–5% usualmente hicieron 3-bet).
- **Impacto**: Sobrejuega parejas medias (`88/99`) como si fueran `AK/QQ+` preflop, infrajuega `AK/AQ`, y sobrestima la probabilidad de monstruos en rangos de call pasivo.

### P3 (Crítico) — Actualización rígida de rangos e ignorancia de perfil/sizing en `rangos.py` y `stats.py`
- **Código afectado**: `rangos.py:56-115`, `stats.py:101-110, 149-227`, `motor.py:262-264, 416-438`.
- **Errores específicos**:
  1. **Código muerto en `stats.py`**: `Rival.multiplicador_apuesta` (calibrado por showdowns) se definió en `stats.py:101` pero **nunca se llama en `rangos.py`**, que usa constantes rígidas `conserva *= 0.50` / `0.40`.
  2. **Ceguera ante apuestas absurdas / `aleatorio`**: si `aleatorio` va all-in con cualquier mano, `rangos.py` reduce su rango a `conserva *= 0.40` (top 28% del board + 30% farol), atribuyéndole manos fuertes cuando su verosimilitud es prácticamente uniforme!
  3. **Inversión de rango por fallback (`motor.py:262-264`)**: `if len(lista) < 4:` reemplaza un rango súper estrecho (p. ej. 3 combos de sets/top-two después de bloqueadores) por `[:200]` (¡el top 15% de todas las manos!).
  4. **Contaminación de `rango_showdown` (`stats.py:218-223`)**: registra `percentil(cartas)` incluso cuando el rival solo pasó gratis en la Ciega Grande (`voluntario=False`), sesgando el VPIP estimado de jugadores tight.
  5. **Showdowns all-in preflop ignorados**: `registrar_showdown` solo registra `fuerza_al_apostar` si `h.get("etapa") != "preflop"`, perdiendo calibración de all-ins preflop.
  6. **Sin decaimiento temporal (*forgetting*)**: si un rival cambia de estrategia (`bandido` alternando brazos, o un rival humano/adaptativo en rondas posteriores), el promedio histórico simple tarda demasiado en reaccionar.
  7. **Sin distinción entre tamaños de apuesta**: una apuesta de `0.25x` pozo y un `2x` overbet reducen el rango exactamente igual (`conserva *= 0.50`).

### P4 (Alto) — Errores estratégicos en `preflop.py`
- **Código afectado**: `preflop.py:111-193`.
- **Errores**:
  1. **Llamada potencialmente catastrófica con `77/ATo` ante all-in de 100bb (`preflop.py:165-166`)**:
     `if pct <= 0.075 and ancho >= 0.24: return t.apostar(t.apuesta_actual * 3)`
     Si un rival abre directamente all-in de 100bb (`subidas == 1`) y su `ancho` estimado (mezclado con `rango_showdown`) supera `0.24`, Pokalatro v8 responde `allin` sin comprobar `equity` ni `compromiso`, pagando 100bb con `ATo`, `KJo` o `77`.
  2. **SB nunca completa detrás de limpers (`preflop.py:134-147`)**: cuando `limpers > 0`, el bloque `if limpers:` retorna en línea 139 antes de evaluar el complete barato de SB (`t.por_igualar <= bb * 0.5`), foldeando manos con enormes pot odds (10:1) como `65s` o `22` por 0.5bb.
  3. **Aislamiento excesivo OOP desde SB contra limpers**: en SB con 1 jugador detrás (`detras() == 1`), `limite * 0.75` aísla el ~31.5% de manos a `3.5 + limpers` ciegas grandes jugando fuera de posición botes grandes multiway con manos marginales (`K8o`, `Q9o`).
  4. **Stack corto (`<= 12 bb`) subóptimo**: ante una subida (`subidas >= 1`), solo hace all-in con `pct <= 0.07` (~99+, AQs+), foldeando manos altamente rentables para reshove de 6–11 bb (`77–88`, `A9s–AJs`, `KQs`, `ATo+`), y no ajusta por `t.stack_efectivo` cuando somos deep pero el rival está short.
  5. **`t.stack_efectivo` sin usar**: `Turno.stack_efectivo` existe en `turno.py:95` pero ni `preflop.py` ni `postflop.py` lo consultan.

### P5 (Alto) — Errores estratégicos y matemáticos en `postflop.py` y `turno.py`
- **Código afectado**: `postflop.py:44-173`, `turno.py:210-214`.
- **Errores**:
  1. **`pot_odds` incorrecto frente a apuestas mayores al stack (`turno.py:210-214`)**:
     No descuenta la parte no igualable (`noIgualable = por_igualar - min(por_igualar, saldo)`), que según `docs/reglas.md` vuelve al apostador. Sobreestima el precio de pagar all-ins cortos cuando nuestro stack es menor que la apuesta rival.
  2. **Confusión entre mano hecha débil y proyecto en "semi-farol" (`postflop.py:149-153`)**:
     `if not river and n <= 2 and 0.28 <= e < umbral_valor and not estacion:`
     Trata cualquier mano con `0.28 <= e < 0.58` igual: apuesta el 55% del pozo en turn con una pareja baja/media sin outs (que solo se hace pagar por manos mejores y tira manos peores) igual que con un proyecto de color + escalera.
  3. **Faroles y C-bets ciegos a la mano y a bloqueadores (`postflop.py:156-171`)**:
     El bloque de c-bet y farol en river sortea con `random.random() < p` sin mirar `e` ni `hecha` ni bloqueadores: puede apostar de farol en el river con una pareja débil que tiene 45% de showdown value (convirtiendo una mano ganadora al check en farol), o hacer triple-barrel sin bloqueadores.
  4. **Falta de SPR y geometría multi-calle**: decide con una sola equidad hasta el river (`e`) y umbrales fijos (`VALOR`), sin adaptar el compromiso al SPR (`SPR <= 2.5` vs `SPR >= 10`) ni considerar el valor de protección/bloqueo.

### P6 (Medio-Alto) — Bug de proyecto de escalera en el board y rendimiento en `motor.py`
- **Código afectado**: `motor.py:275-321, 355-402`.
- **Errores**:
  1. **Bug de straight draw comunitario (`motor.py:392-399`)**: `_puntajes_en_mesa` calcula `mascara = mascara_mesa | (1 << (a >> 2)) | (1 << (b >> 2))` y cuenta `salidas`, sin exigir que al menos una carta propia `(a, b)` participe en la ventana de escalera. Si el board es `9-8-7-6` (o `9-8-7-2`), combinaciones basura como `2c 3d` reciben `+0.15` de bonus por "proyecto de escalera abierta" que en realidad está en la mesa comunitaria para todos.
  2. **Redundancia en river Monte Carlo (`motor.py:307`)**: cuando `faltan == 0` (river), `evaluar(mano + tablero)` es constante en las 6 000 iteraciones, pero se recalcula 6 000 veces. Además, contra 1 rival en river la enumeración exacta sobre los combos ponderados del rango elimina el ruido Monte Carlo y toma ~1–2 ms.
  3. **Velocidad de la suite de pruebas y presupuesto**: las 1 500 pruebas fuzz en `test_estrategia.py` tardan **111.2 s** por la falta de optimización en `evaluar` / `equity` / cacheo de combos en river/turn.

### P7 (Estructural) — Ausencia de Blueprint Equilibrado y Explotación Bayesiana Continua
- Contra rivales que aprenden (`espia`, `bandido`, o bots de otros equipos en la final), usar siempre reglas deterministas alrededor de umbrales fijos sin un blueprint mixto y sin un modelo bayesiano de verosimilitud deja EV en la mesa y aumenta la explotabilidad.
- Asimismo, el modelo de torneo en v8 solo ajusta `_minimo_por_ruina` por `compromiso > 0.30/0.55`, sin modelar la asimetría entre preservar ventaja de fichas para seguir cosechando EV en 50 manos / 10 rondas vs empujar agresivamente con stack corto.

---

## 3. Matriz de Priorización para Pokalatro v9

| Prioridad | Área | Acción en v9 | Impacto esperado |
| :--- | :--- | :--- | :--- |
| **1** | `turno.py` | Corregir orden posicional real con eliminaciones + `pot_odds` exacto con tope de stack efectivo. | Alto (elimina errores en mesas shorthanded y all-ins). |
| **2** | `motor.py` | Corregir bug de proyecto en board, eliminar fallback inversor, enumeración exacta river HU, acelerar evaluador/MC. | Alto (precisión de equity y latencia 3–5× menor). |
| **3** | `stats.py` + `rangos.py` | Modelo bayesiano continuo sobre 1 326 combos con verosimilitud por acción/sizing/perfil, rangos topeados y decaimiento temporal. | Muy Alto (lectura precisa desde `aleatorio` hasta `darwin` y bots nuevos). |
| **4** | Abstracción + Blueprint CFR | Entrenar offline con MCCFR/CFR+ un blueprint multijugador (preflop por posición/stack/acción + postflop por `made_tier` × `draw_potential` × textura × SPR × posición) y exportarlo a Python puro. | Alto (base sólida no explotable y mejor preflop/postflop multiway). |
| **5** | `preflop.py` + `postflop.py` | Política híbrida: Blueprint + Explotación Bayesiana regularizada + Subjuego local river/turn en pozos grandes + Utilidad de torneo PokerFight. | Muy Alto (maximiza fichas netas y 1.º puesto con baja tasa de quiebra). |

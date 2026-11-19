# Resultados de las pruebas locales

Todo se midió con la arena del kit, sin modificar su código.

## El simulacro del torneo

Cada mesa: Pokalatro + 4 rivales sorteados entre los 8 bots del kit (`aleatorio`,
`conservador`, `base`, `novato`, `estadistico`, `bandido`, `espia`, `darwin`).
Formato exacto de la clasificatoria: 50 manos, 10 000 fichas, ciegas 50/100. Se miden
las fichas netas de **todos** los bots, porque el top del torneo lo decide quién gana
más fichas por mesa.

Herramienta: `tools/torneo_sim.py` (en el repo de pruebas, no en el del bot).

### Versión final (v6), 80 mesas

| # | Bot | Mesas | Neto por mesa | Mesas ganadas | Quiebras |
|---|---|---|---|---|---|
| **1** | **pokalatro** | 80 | **+3 973** | 27 | 10 |
| 2 | bandido | 43 | +2 644 | 9 | 5 |
| 3 | novato | 45 | +2 041 | 5 | 5 |
| 4 | darwin | 39 | +1 343 | 9 | 14 |
| 5 | base | 40 | +681 | 10 | 16 |
| 6 | estadistico | 44 | −618 | 11 | 17 |
| 7 | espia | 34 | −3 134 | 3 | 15 |
| 8 | aleatorio | 44 | −5 147 | 6 | 36 |
| 9 | conservador | 31 | −7 831 | 0 | 5 |

Timeouts: 0. Acciones inválidas: 0.

### Confirmación con 80 mesas nuevas (semillas que el bot nunca vio)

| # | Bot | Mesas | Neto por mesa | Mesas ganadas | Quiebras |
|---|---|---|---|---|---|
| **1** | **pokalatro** | 80 | **+5 519** | 30 | 9 |
| 2 | novato | 44 | +3 425 | 10 | 1 |
| 3 | darwin | 41 | +1 080 | 8 | 15 |
| 4 | bandido | 43 | +871 | 8 | 5 |
| 5 | base | 42 | +498 | 8 | 9 |
| 6 | espia | 38 | −103 | 8 | 13 |
| 7 | estadistico | 35 | −2 372 | 7 | 16 |
| 8 | conservador | 40 | −7 462 | 0 | 3 |
| 9 | aleatorio | 37 | −8 364 | 1 | 33 |

Primeros en las dos tandas independientes de 80 mesas.

## Cómo se llegó ahí

Cada cambio se midió en el simulacro antes de quedarse.

| Versión | Qué cambió | Neto por mesa | Quiebras |
|---|---|---|---|
| base | equity contra rangos por percentil preflop | +4 392 | 22% |
| v4 | rango de all-in según la frecuencia del rival | +3 555 | 25% — **descartada** |
| v5 | rangos filtrados por la fuerza de cada mano **en la mesa real** | +4 555 | 18% |
| v6 | menos faroles, pagos más exigentes en turn y river, apuestas de valor más grandes | +3 973* | **12%** |

\* v5 y v6 se jugaron sobre las mismas 80 mesas: en ellas v5 sacó +3 478 y v6 +3 973.

### v5: el rango del rival se filtra por la mesa, no por sus cartas iniciales

El análisis mano a mano del historial mostró que casi todas las manos grandes perdidas
seguían el mismo patrón: apostábamos con una pareja, el rival resubía en el turn o el
river, pagábamos y mostraba escalera, trío o full. Perdíamos 345 000 fichas en turn y
river contra 78 000 en preflop y flop.

La causa: el rango del rival se estrechaba por qué tan buenas eran sus cartas *antes
del flop*. Pero quien paga tres calles y resube el river tiene una mano fuerte **en
esta mesa**: QT no es una mano inicial premium, pero en J-J-A-K es escalera.

Ahora cada acción del rival después del flop filtra su rango por la fuerza de cada
combinación en las cartas comunitarias reales. Efecto en manos reales del historial:

| Mano | Equity antes | Equity ahora |
|---|---|---|
| AK con par de A en 8-A-5-8, tras apuesta, apuesta y resubida | 83% | 33% |
| AJ con par de A, tras dos resubidas | 55% | 23% |
| K5 con par de K en 9-K-8-9, tras dos resubidas | 64% | 25% |

También se corrigió que una pareja de la mesa contara como nuestra (con 6-6 en la mesa
y un 2 en la mano, el bot creía tener doble par).

### v6: ajustes medidos decisión por decisión

Con un registro de cada decisión cruzado con el resultado de la mano (80 mesas):

| Decisión | Antes | Después |
|---|---|---|
| Seguir faroleando en el turn | 64 veces, −918 fichas c/u | 22 veces, −9 c/u |
| Pagar una apuesta en el turn | −1 261 c/u | +775 c/u |
| Apuesta de continuación en el flop | −69 c/u | +120 c/u |
| Entrar pagando detrás de los que solo igualaron | −81 c/u | eliminado |

La razón la explica el código de `darwin` (su genoma está en el kit): paga cuando su
fuerza contra una mano al azar supera las pot odds por 1.18, menos un descuento por
cada subida. Con media apuesta del pozo eso es casi cualquier mano. Contra bots así
los faroles rinden poco y las apuestas de valor grandes rinden mucho: las apuestas por
valor dejaron +885 fichas por vez en el flop, +1 174 en el turn y +1 638 en el river.

## Qué se probó y no funcionó

1. **Pagar más ancho cuando se juega el stack** (dos veces, de dos formas distintas).
   Gana fichas a la larga en teoría, pero en el simulacro subió las quiebras y bajó el
   promedio. En un torneo con eliminación, quedar sin fichas cuesta más que lo que esa
   jugada gana.
2. **Medir qué tan seguido va all-in cada rival.** `darwin` va all-in seguido, pero
   siempre con manos fuertes: la frecuencia lo confundía con un loco.

## Sobre el ruido

En 50 manos el resultado de una mesa va de −10 000 a más de +40 000 fichas: el error
estándar del promedio de 80 mesas es de unas 1 200 fichas. Por eso cada cambio se
justificó primero por lógica o por el análisis de manos concretas, y el simulacro se
usó para confirmar la dirección y descartar lo que empeoraba.

## Velocidad

Mesa de 5, 80 manos, 148 decisiones medidas: mediana **33 ms**, percentil 95 58 ms,
máximo **66 ms**, contra un plazo de 5 000 ms. Cero timeouts en todas las pruebas
(más de 30 000 manos).

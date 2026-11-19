# Pokalatro — bot para PokerFight (versión v8)

Al conectarse, el bot imprime su versión: `Pokalatro v8 conectado como ...`.

Bot de Texas Hold'em no-limit en **Python puro** (3.8+, sin dependencias). Se conecta
por TCP a la arena, recibe sus cartas en JSON (una línea por mensaje) y responde una
acción válida en milisegundos.

## La idea en un párrafo

Casi todos los bots de reglas deciden con la fuerza de su mano *contra una mano al
azar*. Eso sobrevalora las manos: el rival que sigue en el pozo y acaba de subir no
tiene una mano al azar, tiene una buena. Pokalatro estima primero **qué rango de
manos puede tener cada rival** (según lo que hizo en la mano, sus estadísticas de todo
el torneo y las cartas que mostró en showdowns anteriores) y recién entonces calcula
por Monte Carlo **su probabilidad real de ganar contra ese rango**. Esa cifra se
compara con las pot odds: si gana más seguido de lo que cuesta pagar, paga.

## Cómo decide

| Etapa | Qué hace |
| --- | --- |
| **Antes del flop** | Tabla de las 169 manos iniciales con su equity real (`tabla_preflop.py`, generada por Monte Carlo). Abre con un porcentaje de manos que depende de la posición, de cuántos rivales quedan por hablar y de cuánto se retiran esos rivales. Ante una subida, calcula equity contra el rango del subidor y la compara con las pot odds. |
| **Después del flop** | Cada acción del rival filtra su rango por la fuerza de cada mano **en las cartas comunitarias reales**: quien resube el river tiene escalera o trío en esta mesa, no "una buena mano inicial". Contra ese rango, equity Monte Carlo (~2 µs por mano evaluada). Apuesta grande por valor cuando va adelante, paga solo cuando la equity supera las pot odds con margen, y farolea poco: los bots de umbral fijo pagan demasiado. |
| **Tamaño de apuesta** | Según la equity y la textura de la mesa: más caro en mesas húmedas (proyectos de color o escalera) y contra rivales que pagan todo; más barato contra los que se retiran igual. |
| **Stack corto** | Con 12 ciegas o menos, all-in o nada: nunca una subida corta que la mesa pueda rechazar. |

Tres correcciones sobre la cuenta de equity, que son las que evitan los errores caros:

- **Realización.** Un proyecto no cobra su equity entera: antes del river hay que pagar
  más apuestas. Fuera de posición, menos todavía.
- **Ruina.** En un torneo con eliminación, jugarse el stack entero exige más equity que
  jugarse una ciega. Con una sola pareja no se arma un pozo enorme.
- **Rival.** A quien paga todo se le cobra más y no se le farolea; a quien se retira
  mucho se le roba más seguido.

## Robustez (la prioridad número uno)

Un timeout o una acción inválida cuestan lo mismo: la mesa juega por nosotros y nos
anota una penalización. Por eso:

- Toda decisión pasa por un último filtro que solo deja salir acciones que están en
  `acciones_validas`, con el monto corregido a la subida mínima y el all-in declarado
  como `allin` (nunca como una subida corta).
- Si la estrategia lanza una excepción, el despachador responde la acción segura
  (`check`, o `fold` si hay que pagar). Hay un test que lo fuerza.
- El cálculo de equity corta por tiempo, no por cantidad de simulaciones: nunca se pasa
  del plazo aunque la máquina esté cargada.
- `-persistente` reconecta siempre con el mismo token, así la mesa devuelve la silla y
  las fichas.
- El azar se siembra con la mano y la situación: la misma situación da siempre la misma
  jugada (se puede reproducir un error) pero un rival no la puede predecir.

## Resultados en pruebas locales

Simulacro del torneo: mesas de 5 con el formato de la clasificatoria (50 manos,
10 000 fichas, ciegas 50/100), con los 4 rivales sorteados entre los 8 bots del kit.

| Medida | Valor |
| --- | --- |
| Puesto entre los 9 bots (simulacro de torneo, 2 × 80 mesas) | **1.º en las dos tandas** |
| Fichas netas por mesa de 50 manos | +3 973 y +5 519 |
| Timeouts y acciones inválidas | 0 |
| Tiempo por decisión | mediana 33 ms, máximo 66 ms (el plazo es de 5 000 ms) |

## Archivos

| Archivo | Qué contiene |
| --- | --- |
| `bot.py` | Bucle de mensajes y reconexión. |
| `conexion.py` | TCP y JSON Lines (protocolo 1.0.0). |
| `estrategia.py` | Despachador: elige preflop o postflop y garantiza acción válida. |
| `preflop.py` | Decisiones antes del flop. |
| `postflop.py` | Decisiones en flop, turn y river. |
| `motor.py` | Evaluador de manos, percentiles, textura de mesa y equity Monte Carlo. |
| `rangos.py` | Estimación del rango de cada rival. |
| `stats.py` | Estadísticas por rival, acumuladas en todo el torneo. |
| `turno.py` | Ayudantes de apuestas y posición. |
| `tabla_preflop.py` | Equity de las 169 manos iniciales (generada, no se edita a mano). |
| `test_estrategia.py` | Pruebas, incluida una de 1 500 situaciones al azar. |

## Ejecutar

Ver `LEEME-EJECUTAR.md`. En resumen:

```bash
python bot.py -addr HOST:PUERTO -token $TOKEN -persistente
python -m unittest -v
```

El código del kit de la competencia no se modifica: este bot vive en su propio
repositorio y solo usa el protocolo documentado.

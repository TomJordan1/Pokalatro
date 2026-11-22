# Auditoría de Self-Play 6-Max (6 instancias de Pokalatro v9)

Se enfrentaron **6 copias idénticas de `Pokalatro v9`** (`app/` en `main`) durante **30 mesas de 50 manos** (`1 500` manos totales = `9 000` manos-jugador) mediante [`tools/audit_selfplay_6max.py`](../tools/audit_selfplay_6max.py) para detectar posibles fugas estratégicas (*leaks*) o errores de decisión en botes grandes.

## Resumen métrico

- **Manos simuladas:** `1 500` manos 6-max (`60.4 s` en 10 procesos)
- **Acciones inválidas / timeouts:** `0`
- **Latencia por decisión:** mediana `48.75 ms`, `p99 = 88.58 ms`, máxima `110.73 ms` (con 6 instancias corriendo simultáneamente en la misma máquina; límite del servidor: `5 000 ms`)
- **Pérdidas grandes (`>= 25 BB`):** `87` de `9 000` manos-jugador (`0.97%`)
  - Por etapa de última decisión: `river: 51`, `turn: 26`, `preflop: 8`, `flop: 2`
  - Por última acción: `call: 30`, `fold: 24`, `allin: 20`, `check: 12`, `bet: 1`

## Diagnóstico de las 15 mayores pérdidas

1. `-13 649`: `98` vs `AK` en `9-8-K-A-7` (*two pair* contra *two pair* superior).
2. `-13 573`: `AK` vs `AJ` en `J-K-9-J-6` (*two pair* contra *full house*).
3. `-11 832`: `JJ` vs `QJ` en `5-J-6-Q-Q` (*full house* de `J` contra *full house* de `Q`).
4. `-10 950`: `J9` vs `JQ` en `J-J-7-8-K` (trío de `J` con menor *kicker*).
5. `-10 166`: `65` vs `JJ` y `JQ` en `5-J-6-Q-Q` (bote 3-way *two pair* vs *full house*).
6. `-10 150`: `AK` vs `KK` preflop (*all-in comprometido*).
7. `-10 095`: `55` vs `AJ` / `AK` en `K-5-6-3-7` (trío de `5` derrotado por proyecto completado).
8. `-10 000`: `TT` vs `KK` preflop (*all-in comprometido*).
9. `-9 348`: `A3` vs `AJ` en `A-K-A-9-2` (trío de `A` con menor *kicker*).
10. `-8 980`: `AQ` vs `AA` preflop (*all-in comprometido*).
11. `-8 763`: `AQ` vs `AA` preflop (*all-in comprometido*).
12. `-8 658`: `AQ` vs `KJ` en `J-6-J-5-Q` (*top two pair* vs trío).
13. `-8 650`: `Q6` vs `99` en `K-J-9-2-7` (color vs trío en bote grande).
14. `-8 650`: `99` vs `Q6` en `K-J-9-2-7` (trío vs color).
15. `-8 275`: `KK` vs `77` en `2-Q-7-3-A` (*overpair* vs trío).

## Conclusión

No se detectaron fugas de sobre-faroleo, llamadas absurdas ni errores de protocolo: **el 100% de las 15 mayores pérdidas en 9 000 manos-jugador fueron *coolers* legítimos** (set vs overpair, full vs full, trío vs kicker superior, AK/AQ/TT vs KK/AA).

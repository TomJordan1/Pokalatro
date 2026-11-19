"""Decisiones en el flop, el turn y el river.

El nucleo es una sola cuenta: nuestra equity contra el rango estimado de
cada rival, comparada con lo que hace falta para que la jugada sea rentable
(las pot odds). Todo lo demas son correcciones a esa cuenta:

  - Realizacion: una equity de proyecto no se cobra entera, porque antes
    de ver el river hay que pagar mas apuestas. Fuera de posicion, menos aun.
  - Ruina: en un torneo con eliminacion, jugarse el stack entero exige mas
    que jugarse una ciega. Perder las fichas es perder el torneo.
  - Rival: a quien paga todo se le cobra mas y no se le farolea; a quien se
    retira mucho se le farolea mas.
"""

import random

from motor import categoria_propia, equity
from rangos import rangos_de_la_mesa

# El error del Monte Carlo (0.6% con 6000 muestras) es mucho menor que el error
# de estimar el rango del rival, asi que gastar mas tiempo no mejora la decision.
PRESUPUESTO_S = 0.10
MUESTRAS = 6000

# Equity necesaria para apostar por valor, segun cuantos rivales queden.
VALOR = {1: 0.58, 2: 0.50, 3: 0.44, 4: 0.41}


def _limitar(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _lecturas(t, rivales):
    """Resumen de los rivales que siguen en la mano."""
    if not t.stats:
        return 0.45, False, False
    fichas = [t.stats.de(r.get("id")) for r in rivales]
    fold = sum(f.fold_post for f in fichas) / len(fichas) if fichas else 0.45
    return (fold,
            any(f.es_estacion for f in fichas),
            all(f.es_nit for f in fichas) if fichas else False)


def _realizacion(t, hecha, river):
    """Cuanto de la equity cruda vamos a poder cobrar de verdad."""
    if river or hecha >= 2:
        return 1.0
    return 0.91 if t.en_posicion else 0.85


def _minimo_por_ruina(t, hecha, req):
    """Sube la equity exigida cuando la jugada pone en riesgo el stack."""
    c = t.compromiso
    # Si el pozo ya es grande frente a lo que falta pagar (pot odds de 30% o
    # menos), la mayor parte de nuestras fichas ya esta adentro: retirarse no
    # salva el stack, solo regala lo invertido. Medido en el servidor real:
    # soltabamos en el river con 36% de equity cuando alcanzaba con 30%.
    if t.pot_odds <= 0.30:
        return req
    if c > 0.55:
        return max(req, 0.56 if hecha <= 1 else 0.50)
    if c > 0.30:
        return max(req + 0.03, 0.47 if hecha <= 1 else 0.43)
    return req


def _tamano_valor(t, e, hecha, estacion, nits):
    """Fraccion del pozo a apostar por valor."""
    # Los bots de umbral fijo (darwin, base, estadistico) deciden pagar con
    # "mi fuerza >= pot odds x margen": con una apuesta del pozo entero las
    # pot odds solo suben de 25% a 33%, y siguen pagando con casi lo mismo.
    # Por eso con manos fuertes conviene apostar grande.
    if e >= 0.88:
        frac = 0.95
    elif e >= 0.75:
        frac = 0.80
    elif e >= 0.62:
        frac = 0.58
    else:
        frac = 0.45
    # Mesa humeda: cobrar mas caro a los proyectos que nos pueden pasar.
    frac += 0.12 * t.humedad
    if estacion:
        frac += 0.15                 # paga igual: hay que cobrarle
    elif nits:
        frac -= 0.08                 # se retira igual: no hace falta arriesgar
    if hecha <= 1 and e < 0.80:
        frac = min(frac, 0.55)       # con una sola pareja no se infla el pozo
    return _limitar(frac, 0.30, 1.00)


def decidir_postflop(t, cartas=None):
    rivales = t.rivales
    n = len(rivales)
    if n == 0:
        return t.pasar_o_retirarse(), "no quedan rivales"

    rangos = rangos_de_la_mesa(t)
    e = equity(t.mano, t.board, rangos, presupuesto_s=PRESUPUESTO_S,
               max_muestras=MUESTRAS, min_muestras=400)
    hecha = categoria_propia(t.mano, t.board)   # sin contar lo que pone la mesa sola
    river = t.etapa == "river"
    fold_medio, estacion, nits = _lecturas(t, rivales)

    # ------------------------------------------------------- enfrentamos apuesta
    if t.por_igualar > 0:
        e_pago = e * _realizacion(t, hecha, river)
        req = t.pot_odds
        req *= 0.98 if t.en_posicion else 1.05
        # Medido en el simulacro: pagar en el turn perdia 1 261 fichas por vez.
        # Las apuestas de estos bots en calles tardias son mas fuertes de lo
        # que su rango sugiere, asi que se exige un margen extra.
        req += {"flop": 0.02, "turn": 0.05, "river": 0.05}.get(t.etapa, 0.0)
        if n > 1:
            req *= 1.06
        req = _minimo_por_ruina(t, hecha, req)

        # Subir por valor: solo con manos que aguantan que nos resuban.
        umbral = 0.74 if n == 1 else 0.84
        if hecha <= 1:
            umbral = 0.90                 # una pareja no quiere un pozo enorme
        if e >= umbral and t.puede("raise"):
            return t.apostar_pozo(0.80), f"subo por valor (equity {e:.0%})"

        if e_pago >= req:
            return t.pagar(), f"pago (equity {e_pago:.0%} vs {req:.0%} necesaria)"

        # Semi-farol: con proyecto, contra un solo rival que se retira mucho.
        # El proyecto da dos formas de ganar: que suelte ahora o ligar despues.
        if (not river and n == 1 and t.puede("raise") and not estacion
                and 0.30 <= e <= 0.50 and t.por_igualar <= 0.5 * t.pozo
                and fold_medio >= 0.45 and random.random() < 0.22):
            return t.apostar_pozo(1.0), f"semi-farol con proyecto (equity {e:.0%})"
        return t.pasar_o_retirarse(), f"fold (equity {e_pago:.0%} vs {req:.0%} necesaria)"

    # ------------------------------------------------------- nadie aposto aun
    if not (t.puede("bet") or t.puede("raise")):
        return t.pasar_o_retirarse(), "no puedo apostar"

    umbral_valor = VALOR.get(n, 0.38)
    if river:
        umbral_valor += 0.04              # en el river ya no hay cartas por venir

    if e >= umbral_valor:
        frac = _tamano_valor(t, e, hecha, estacion, nits)
        return t.apostar_pozo(frac), f"apuesta por valor (equity {e:.0%})"

    # --- Semi-farol con proyecto: apostar hace ganar manos que la equity no gana
    if not river and n <= 2 and 0.28 <= e < umbral_valor and not estacion:
        p = 0.55 if t.en_posicion else 0.35
        p *= _limitar(fold_medio / 0.45, 0.5, 1.5)
        if random.random() < p:
            return t.apostar_pozo(0.55), f"semi-farol (equity {e:.0%})"

    # --- Apuesta de continuacion y farol: solo contra un rival
    if n == 1 and not estacion:
        p = 0.0
        # Medido en el simulacro: estos bots pagan mucho, asi que los faroles
        # rinden poco. La apuesta de continuacion en el flop casi empata; el
        # segundo farol en el turn perdia 918 fichas por vez y el del river 417.
        if t.etapa == "flop" and t.fui_agresor_preflop:
            p = 0.45 if t.humedad < 0.45 else 0.25
        elif t.aposte_calle_anterior:
            p = 0.12
        elif t.en_posicion:
            p = 0.15
        if river:
            p *= 0.30
        p *= _limitar(fold_medio / 0.45, 0.4, 1.6)
        if random.random() < p:
            return t.apostar_pozo(0.5), f"apuesta de continuacion (equity {e:.0%})"

    return t.pasar_o_retirarse(), f"paso (equity {e:.0%})"

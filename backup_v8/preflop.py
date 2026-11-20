"""Decisiones antes del flop.

Dos herramientas:
  - El percentil de la mano (tabla_preflop.py, equity real de las 169 manos
    iniciales) para decidir con que manos ABRIR. Abrir es una cuestion de
    rango, no de equity puntual: cuesta 0 ms.
  - La equity Monte Carlo contra el rango estimado del rival para decidir si
    PAGAR una subida. Ahi si importa la cifra exacta, y comparar equity con
    pot odds es la unica forma correcta de decidirlo.
"""

import random

from motor import equity, percentil_mano
from rangos import rango_rival, rangos_de_la_mesa

# Fraccion de manos con la que abrimos, segun cuantos rivales hablan despues.
# Mas gente por hablar = mas probable que alguien tenga una mano mejor.
APERTURA = {0: 0.52, 1: 0.42, 2: 0.34, 3: 0.28, 4: 0.24, 5: 0.21}

PRESUPUESTO_S = 0.05
MUESTRAS = 4000


def _limitar(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _factor_robo(t):
    """Abrir mas contra rivales que se retiran mucho, menos contra los locos."""
    detras = t.rivales_detras()
    if not t.stats or not detras:
        return 1.0
    folds = [t.stats.de(r.get("id")).fold_pre for r in detras]
    medio = sum(folds) / len(folds)
    factor = 1.0 + (medio - 0.55) * 1.5          # 0.55 es el valor tipico
    if any(t.stats.de(r.get("id")).es_loco for r in detras):
        factor *= 0.88
    return _limitar(factor, 0.75, 1.45)


def _apertura(t):
    base = APERTURA.get(t.detras(), 0.20)
    activos = t.rivales_en_mano + 1
    if activos <= 2:
        base += 0.18                              # heads-up todo vale mas
    elif activos == 3:
        base += 0.08
    elif activos == 4:
        base += 0.03
    return min(0.85, base * _factor_robo(t))


def _rivales_con_fichas(t):
    """Los rivales que YA pusieron fichas en el pozo por decision propia.

    Es contra ellos que hay que calcular la equity: los que todavia no
    hablaron tienen rango "cualquier mano" y, si los contamos, la equity sale
    absurdamente baja y el bot se retira con manos que deberia pagar. Que
    alguno de los que falta hablar pague se cobra aparte, exigiendo un poco
    mas de equity.
    """
    dentro = []
    for r in t.rivales:
        acciones = [a.get("tipo") for a in t.acciones_de(r.get("id"), "preflop")]
        if any(a in ("call", "bet", "raise", "allin") for a in acciones):
            dentro.append(r)
    return dentro or t.rivales


def _equity(t, rivales=None):
    """Equity contra los rivales que ya estan en el pozo."""
    objetivos = _rivales_con_fichas(t) if rivales is None else rivales
    rangos = [rango_rival(t, r) for r in objetivos]
    return equity(t.mano, t.board, rangos, presupuesto_s=PRESUPUESTO_S,
                  max_muestras=MUESTRAS, min_muestras=300)


def _equity_requerida(t, pct):
    """Pot odds, corregidas por lo que podemos realizar de nuestra equity.

    Fuera de posicion se gana menos seguido de lo que dice la equity cruda
    (nos apuestan y hay que soltar). Con stacks profundos y manos que ligan
    fuerte (parejas y del mismo palo) hay odds implicitas: cuando se liga,
    se gana el stack del rival.
    """
    req = t.pot_odds
    req *= 0.97 if t.en_posicion else 1.12
    # Cada rival que ya esta en el pozo reparte el premio; cada uno que
    # todavia puede entrar es un riesgo que no esta en la cuenta de equity.
    if len(_rivales_con_fichas(t)) > 1:
        req *= 1.06
    req *= 1.0 + 0.035 * len(t.rivales_detras())
    alto, bajo = sorted((t.mano[0] >> 2, t.mano[1] >> 2), reverse=True)
    mismo_palo = (t.mano[0] & 3) == (t.mano[1] & 3)
    especulativa = alto == bajo or mismo_palo or (alto - bajo) <= 2
    if t.stack_bb >= 35 and especulativa and t.compromiso < 0.25:
        req *= 0.88
    return req


def decidir_preflop(t, cartas=None):
    bb = t.ciega_grande
    pct = percentil_mano(t.mano[0], t.mano[1])
    subidas = t.subidas_preflop
    stack_bb = t.stack_bb

    # --- Stack corto: empujar o retirarse, nunca subidas chicas ---------------
    # Con 12 ciegas o menos no hay juego despues del flop: o se va con todo
    # (y se cobran las ciegas sin pelear) o se espera una mano mejor.
    if stack_bb <= 12:
        limite = _apertura(t) * 0.70
        if subidas == 0:
            if pct <= limite:
                return t.todo(), f"stack corto ({stack_bb:.0f} ciegas): all-in"
            return t.pasar_o_retirarse(), "stack corto: espero mano mejor"
        if pct <= 0.07:
            return t.todo(), "stack corto: all-in con mano premium"
        if t.pot_odds <= 0.33 and pct <= 0.18:
            return t.pagar(), "stack corto: las odds dan para pagar"
        return t.pasar_o_retirarse(), "stack corto: no alcanza"

    # --- Nadie subio todavia --------------------------------------------------
    if subidas == 0:
        limite = _apertura(t)
        limpers = t.limpers

        if t.por_igualar == 0 and t.puede("check"):
            # Somos la ciega grande y nadie subio: la opcion es subir o pasar.
            if pct <= limite * 0.55:
                return t.apostar(bb * (3 + limpers)), "ciega grande: subo"
            return {"tipo": "check"}, "ciega grande: paso y veo el flop gratis"

        if limpers:
            if pct <= limite * 0.75:
                return t.apostar(bb * (3.5 + limpers)), f"aislo a {limpers} limper(s)"
            # Entrar pagando detras de los limpers perdia 81 fichas por vez en
            # el simulacro: o se aisla subiendo, o se deja pasar.
            return t.pasar_o_retirarse(), "no vale entrar"

        if pct <= limite:
            tamano = 2.5 if t.hablo_ultimo else 3.0
            return t.apostar(bb * tamano), f"abro (mano en el {pct * 100:.0f}% mejor)"
        # Ciega chica: completar es pagar para jugar fuera de posicion. Solo
        # con manos que ligan fuerte y cuando es casi gratis.
        if t.por_igualar <= bb * 0.5 and pct <= limite * 1.5:
            return t.pagar(), "ciega chica: completo barato"
        return t.pasar_o_retirarse(), "mano debil: me retiro"

    # --- Alguien subio una vez ------------------------------------------------
    if subidas == 1:
        dentro = _rivales_con_fichas(t)
        anchos = [float(rango_rival(t, r)) for r in dentro]
        ancho = min(anchos) if anchos else 0.25
        e = _equity(t, dentro)
        req = _equity_requerida(t, pct)

        # Premium: resubir siempre. Con stack medio, directamente all-in.
        if pct <= 0.030:
            if stack_bb <= 35:
                return t.todo(), "premium: all-in"
            return t.apostar(t.apuesta_actual * 3 + bb * t.limpers), "premium: resubo"

        # Resubida por valor contra un rival que abre demasiado.
        if pct <= 0.075 and ancho >= 0.24:
            return t.apostar(t.apuesta_actual * 3), f"resubo (su rango es ancho: {ancho:.0%})"

        # Resubida de presion: solo contra quien se retira mucho, y a veces,
        # para no ser previsible. Los bots con umbrales fijos sueltan mucho.
        if t.en_posicion and pct <= 0.16 and t.stats:
            subidor = t.ultimo_agresor
            st = t.stats.rivales.get(subidor)
            if st and st.fold_pre >= 0.60 and random.random() < 0.35:
                return t.apostar(t.apuesta_actual * 3), "resubo de presion (se retira mucho)"

        # Pagar: la cuenta de siempre, equity contra pot odds.
        if e >= req and t.compromiso <= 0.35:
            return t.pagar(), f"pago (equity {e:.0%} vs {req:.0%} necesaria)"
        if e >= req * 1.25 and t.compromiso > 0.35:
            return t.pagar(), f"pago comprometiendo stack (equity {e:.0%})"
        return t.pasar_o_retirarse(), f"fold (equity {e:.0%} vs {req:.0%} necesaria)"

    # --- Resubida o mas: solo manos muy fuertes -------------------------------
    e = _equity(t)
    limite_premium = 0.015 if subidas == 2 else 0.005    # ante un 4-bet: solo AA/KK
    if pct <= limite_premium:
        return t.todo(), "mano maxima: all-in ante la resubida"
    if e >= 0.55 and t.compromiso > 0.30 and t.puede("allin"):
        return t.todo(), f"all-in ante la resubida (equity {e:.0%})"
    req = _equity_requerida(t, pct) * 1.10        # ante un 3-bet, mas margen
    if e >= req and t.compromiso <= 0.30:
        return t.pagar(), f"pago el 3-bet (equity {e:.0%})"
    return t.pasar_o_retirarse(), f"fold ante 3-bet (equity {e:.0%})"

"""Decisiones antes del flop (Pokalatro v9): Blueprint CFR+ + Explotacion + Torneo.

Combina:
  1. Ranking real de jugabilidad (`abstraccion.percentil_jugabilidad`, sin el sesgo
     1v1 de `tabla_preflop` donde 99 superaba a AKo/AQs).
  2. Equilibrio Nash Push/Fold resuelto por CFR+ para stacks cortos (<= 13 bb).
  3. Blueprint CFR+ profundo (`PREFLOP_DEEP`) por posicion y accion previa.
  4. Ajustes bayesianos al rival (robo contra nits, valor vs estaciones/locos) y
     proteccion ante all-ins largos.
"""

import random

from abstraccion import indice_clase, percentil_jugabilidad
from blueprint_data import PREFLOP_DEEP
from motor import equity
from rangos import rango_rival
from torneo import factor_presion_eliminacion, minimo_por_supervivencia, probs_push_fold

PRESUPUESTO_S = 0.045
MUESTRAS = 3000


def _limitar(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _pos_blueprint(t):
    det = t.detras()
    if det <= 1:
        return "BTN"
    if det == 2:
        return "CO"
    return "EP"


def _factor_robo(t):
    if getattr(t, "ablate", None) and t.ablate.get("no_exploit"):
        return 1.0
    detras = t.rivales_detras()
    if not t.stats or not detras:
        return 1.0
    folds = [t.stats.de(r.get("id")).fold_pre for r in detras]
    confs = [t.stats.de(r.get("id")).confianza for r in detras]
    medio = sum(folds) / len(folds)
    conf = sum(confs) / len(confs)
    factor = 1.0 + conf * (medio - 0.55) * 1.35
    if any(t.stats.de(r.get("id")).es_loco for r in detras):
        factor *= 0.88
    return _limitar(factor, 0.78, 1.42)


def _apertura(t):
    # Base equilibrada por jugadores detras (calibrada con CFR+ 5-max)
    base_map = {0: 0.54, 1: 0.44, 2: 0.34, 3: 0.26, 4: 0.21, 5: 0.18}
    base = base_map.get(t.detras(), 0.18)
    activos = t.rivales_en_mano + 1
    if activos <= 2:
        base += 0.16
    elif activos == 3:
        base += 0.07
    elif activos == 4:
        base += 0.02
    base *= _factor_robo(t) * factor_presion_eliminacion(t)
    return min(0.84, base)


def _rivales_con_fichas(t):
    dentro = []
    for r in t.rivales:
        acciones = [a.get("tipo") for a in t.acciones_de(r.get("id"), "preflop")]
        if any(a in ("call", "bet", "raise", "allin") for a in acciones):
            dentro.append(r)
    return dentro or t.rivales


def _equity(t, rivales=None):
    objetivos = _rivales_con_fichas(t) if rivales is None else rivales
    rangos = [rango_rival(t, r) for r in objetivos]
    return equity(t.mano, t.board, rangos, presupuesto_s=PRESUPUESTO_S,
                  max_muestras=MUESTRAS, min_muestras=300)


def _equity_requerida(t):
    req = t.pot_odds
    req *= 0.96 if t.en_posicion else 1.11
    dentro = _rivales_con_fichas(t)
    if len(dentro) > 1:
        req *= 1.06
    req *= 1.0 + 0.03 * len(t.rivales_detras())
    alto, bajo = sorted((t.mano[0] >> 2, t.mano[1] >> 2), reverse=True)
    mismo_palo = (t.mano[0] & 3) == (t.mano[1] & 3)
    especulativa = (alto == bajo) or mismo_palo or (alto - bajo <= 2 and bajo >= 4)
    if t.stack_efectivo_bb >= 32 and especulativa and t.compromiso < 0.22:
        req *= 0.87
    return req


def decidir_preflop(t, cartas=None):
    bb = t.ciega_grande
    pct = percentil_jugabilidad(t.mano[0], t.mano[1])
    idx_h = indice_clase(t.mano[0], t.mano[1])
    subidas = t.subidas_preflop
    stack_bb = t.stack_bb
    eff_bb = t.stack_efectivo_bb
    ablate = getattr(t, "ablate", None) or {}
    usar_bp = not ablate.get("no_blueprint")

    # --- 1. Stack corto (<= 13 bb): Nash Push/Fold CFR+ ----------------------
    if stack_bb <= 13.0 or (subidas == 0 and eff_bb <= 11.5):
        p_push, p_call = probs_push_fold(t, idx_h)
        if subidas == 0:
            if t.por_igualar == 0 and t.puede("check"):
                if pct <= 0.24 or p_push >= 0.70:
                    return t.todo(), f"stack corto BB ({stack_bb:.0f}bb): all-in"
                return {"tipo": "check"}, "stack corto BB: check gratis"
            limpers = t.limpers
            # Con limpers previos hay mas dinero muerto pero mas riesgo de trap
            umbral_p = p_push * _factor_robo(t) * (0.78 if limpers > 0 else 1.0)
            if p_push >= 0.85 or (umbral_p > 0.45 and random.random() < min(1.0, umbral_p)):
                return t.todo(), f"stack corto ({stack_bb:.0f}bb): push CFR+"
            return t.pasar_o_retirarse(), "stack corto: espero mejor spot"

        # Enfrentando subida con stack corto
        dentro = _rivales_con_fichas(t)
        e = _equity(t, dentro)
        req = _equity_requerida(t)
        if p_call >= 0.65 or pct <= 0.065:
            return t.todo(), f"stack corto: all-in/call CFR+ (pct {pct:.0%})"
        if e >= req * 1.03:
            if t.puede("allin") and t.saldo <= t.por_igualar * 1.4:
                return t.todo(), f"stack corto: all-in por odds ({e:.0%} vs {req:.0%})"
            return t.pagar(), f"stack corto: pago ({e:.0%} vs {req:.0%})"
        return t.pasar_o_retirarse(), f"stack corto: fold ({e:.0%} vs {req:.0%})"

    # --- 2. Nadie subio todavia ----------------------------------------------
    pos_key = _pos_blueprint(t)
    bp_open, bp_call_o, bp_3b_o, bp_call_3b, bp_4b = PREFLOP_DEEP[pos_key]

    if subidas == 0:
        limite = _apertura(t)
        limpers = t.limpers

        if t.por_igualar == 0 and t.puede("check"):
            # Ciega Grande con opcion tras limps o walk imposible
            if pct <= max(0.14, limite * 0.45):
                return t.apostar(bb * (3 + min(3, limpers))), "ciega grande: subo por valor"
            return {"tipo": "check"}, "ciega grande: paso y veo flop gratis"

        if limpers:
            # Ciega Chica: completar solo manos jugables (evita sangrar OOP en multiway)
            if t.es_sb and t.por_igualar <= bb * 0.6:
                if pct <= 0.14:
                    return t.apostar(bb * (3.5 + min(2, limpers))), f"SB aislo fuerte a {limpers} limper(s)"
                if pct <= 0.36:
                    return t.pagar(), f"SB completo jugable detras de {limpers} limper(s)"
                return t.pasar_o_retirarse(), "SB fold mano debil tras limpers"

            # En posicion o medio: aislar con rango selectivo
            iso_lim = 0.28 if t.hablo_ultimo else (0.22 if t.en_posicion else 0.16)
            if pct <= iso_lim:
                return t.apostar(bb * (3.5 + limpers)), f"aislo a {limpers} limper(s)"
            # Overlimp rentable solo en posicion tardia con pares/suited y 2+ limpers
            if t.hablo_ultimo and limpers >= 2 and pct <= 0.34:
                alto, bajo = sorted((t.mano[0] >> 2, t.mano[1] >> 2), reverse=True)
                mp = (t.mano[0] & 3) == (t.mano[1] & 3)
                if alto == bajo or mp:
                    return t.pagar(), "overlimp especulativo en boton"
            return t.pasar_o_retirarse(), "no vale aislar/limpear"

        # Unopened pot: combinar Blueprint CFR+ con ajuste de robo
        p_bp = bp_open[idx_h] if usar_bp else (1.0 if pct <= limite else 0.0)
        p_heu = 1.0 if pct <= limite else 0.0
        p_abrir = 0.35 * p_bp + 0.65 * p_heu
        if _factor_robo(t) > 1.15 and pct <= limite:
            p_abrir = max(p_abrir, 0.85)
        if p_abrir >= 0.78 or (p_abrir > 0.25 and random.random() < p_abrir):
            tamano = 2.5 if (t.hablo_ultimo and pct > 0.05) else 3.0
            return t.apostar(bb * tamano), f"abro (pct {pct*100:.0f}%, bp {p_bp:.2f})"

        # SB sin limpers ni subidas: completar barato manos especulativas razonables
        if t.por_igualar <= bb * 0.55 and pct <= min(0.70, limite * 1.45):
            return t.pagar(), "ciega chica: completo barato"
        return t.pasar_o_retirarse(), "mano debil: fold"

    # --- 3. Alguien subio una vez -------------------------------------------
    if subidas == 1:
        dentro = _rivales_con_fichas(t)
        e = _equity(t, dentro)
        req = _equity_requerida(t)
        subida_grande = t.apuesta_actual > 10 * bb or t.compromiso > 0.30

        # Ante all-in o subida enorme preflop: decidir ESTRICTAMENTE por equity + torneo
        if subida_grande:
            margen = 1.15 if (t.compromiso > 0.45 and stack_bb > 22.0) else 1.05
            req_allin = minimo_por_supervivencia(t, 3 if pct <= 0.035 else 1, req * margen)
            if pct <= 0.025 or e >= req_allin:
                if t.por_igualar >= t.saldo and t.puede("allin"):
                    return t.todo(), f"pago all-in preflop (eq {e:.0%} vs {req_allin:.0%})"
                return t.pagar(), f"pago subida grande (eq {e:.0%} vs {req_allin:.0%})"
            return t.pasar_o_retirarse(), f"fold ante subida grande (eq {e:.0%} vs {req_allin:.0%})"

        # Subida estandar (2bb..10bb): 3-bet por valor, 3-bet de presion o call
        if pct <= 0.030:  # AA-QQ, AKs
            if eff_bb <= 30:
                return t.todo(), "premium: all-in"
            return t.apostar(t.apuesta_actual * 3.0 + bb * t.limpers), "premium: 3-bet"

        # 3-bet ampliado por valor contra abridores anchos
        subidor = t.ultimo_agresor
        st_sub = t.stats.rivales.get(subidor) if (t.stats and subidor) else None
        pfr_sub = st_sub.pfr_pct if st_sub else 0.20
        conf_sub = st_sub.confianza if st_sub else 0.0

        p_3b_bp = bp_3b_o[idx_h] if usar_bp else 0.0
        if pct <= 0.085 and ((conf_sub >= 0.3 and pfr_sub >= 0.25) or p_3b_bp >= 0.45):
            return t.apostar(t.apuesta_actual * 3.0), f"3-bet valor/balance (pfr {pfr_sub:.0%})"

        # 3-bet de presion contra nits con bloqueadores (Ax suited o KQ/KJ)
        if not ablate.get("no_exploit") and t.en_posicion and st_sub and pct <= 0.17:
            if st_sub.fold_pre >= 0.62 and conf_sub >= 0.25 and random.random() < 0.32:
                return t.apostar(t.apuesta_actual * 3.0), "3-bet presion vs fold alto"

        # Evaluar call combinando equity real + blueprint
        p_call_bp = bp_call_o[idx_h] if usar_bp else 0.0
        req_call = req
        if t.compromiso > 0.25:
            req_call = minimo_por_supervivencia(t, 2, req * 1.12)
        if e >= req_call:
            return t.pagar(), f"pago open (eq {e:.0%} vs {req_call:.0%})"
        if usar_bp and p_call_bp >= 0.55 and e >= req_call * 0.94 and t.compromiso <= 0.12:
            return t.pagar(), f"pago blueprint defensa (eq {e:.0%}, bp {p_call_bp:.2f})"
        return t.pasar_o_retirarse(), f"fold (eq {e:.0%} vs {req_call:.0%})"

    # --- 4. Ante 3-bet o 4-bet+ ---------------------------------------------
    e = _equity(t)
    req = _equity_requerida(t) * (1.08 + 0.04 * min(3, subidas - 2))
    req = minimo_por_supervivencia(t, 3 if pct <= 0.04 else 1, req)

    # AA, KK, QQ, AKs segun nivel de subida
    limite_allin = 0.018 if subidas == 2 else 0.006  # ante 4-bet+: AA/KK
    if pct <= limite_allin:
        return t.todo(), "premium ante resubida: all-in"

    p_4b_bp = bp_4b[idx_h] if usar_bp else 0.0
    if subidas == 2 and eff_bb <= 35 and ((pct <= 0.030 and e >= 0.49) or (p_4b_bp >= 0.72 and e >= 0.47)):
        return t.todo(), f"4-bet all-in (eq {e:.0%})"

    if e >= 0.56 and t.compromiso > 0.35 and t.puede("allin"):
        return t.todo(), f"all-in comprometido (eq {e:.0%})"

    if e >= req and t.compromiso <= 0.32:
        return t.pagar(), f"pago 3-bet (eq {e:.0%} vs {req:.0%})"

    p_c3b_bp = bp_call_3b[idx_h] if usar_bp else 0.0
    if usar_bp and subidas == 2 and p_c3b_bp >= 0.60 and e >= req * 0.93 and t.compromiso <= 0.18:
        return t.pagar(), f"pago 3-bet blueprint (eq {e:.0%})"

    return t.pasar_o_retirarse(), f"fold ante resubida (eq {e:.0%} vs {req:.0%})"

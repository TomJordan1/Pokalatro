"""Gestion de torneo para el formato exacto de PokerFight.

Formato PokerFight:
  - Ciegas fijas 50/100, 10 000 fichas iniciales (100 bb).
  - Clasificatoria: 10 rondas de hasta 50 manos, conservacion de fichas,
    eliminacion en 0, pasan los 6 con mas fichas.
  - Final: 6 jugadores, winner-take-all.

Principio matematico:
  1. Con ventaja positiva (+EV/mano), quedar eliminado destruye todo el EV
     futuro de la mesa y de las rondas restantes: arriesgar el stack entero con
     una pareja marginal requiere un margen de seguridad (prima de supervivencia).
  2. Con stack corto (<= 13 bb efectivos), el riesgo de desangrarse supera al
     de doblarse: se consulta la tabla Nash Push/Fold resuelta por CFR+.
  3. Cuando cubrimos a un rival corto, el valor de eliminacion y fold equity
     aumentan la agresividad optima.
"""

from blueprint_data import PUSH_FOLD, PUSH_FOLD_STACKS


def _limitar(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _interpolar_push_fold(stack_bb, is_mw, idx_mano):
    """Consulta interpolada del equilibrio Push/Fold CFR+ (p_push, p_call)."""
    mw_key = 1 if is_mw else 0
    s = _limitar(stack_bb, float(PUSH_FOLD_STACKS[0]), float(PUSH_FOLD_STACKS[-1]))
    lo_s = PUSH_FOLD_STACKS[0]
    hi_s = PUSH_FOLD_STACKS[-1]
    for k in range(len(PUSH_FOLD_STACKS) - 1):
        a, b = PUSH_FOLD_STACKS[k], PUSH_FOLD_STACKS[k + 1]
        if a <= s <= b:
            lo_s, hi_s = a, b
            break
    t_push_lo, t_call_lo = PUSH_FOLD[(mw_key, lo_s)]
    if lo_s == hi_s:
        return t_push_lo[idx_mano], t_call_lo[idx_mano]
    t_push_hi, t_call_hi = PUSH_FOLD[(mw_key, hi_s)]
    w = (s - lo_s) / float(hi_s - lo_s)
    p_push = (1.0 - w) * t_push_lo[idx_mano] + w * t_push_hi[idx_mano]
    p_call = (1.0 - w) * t_call_lo[idx_mano] + w * t_call_hi[idx_mano]
    return p_push, p_call


def probs_push_fold(t, idx_mano):
    """-> (p_push, p_call) segun stack efectivo, rivales detras y tamano de mesa."""
    if getattr(t, "ablate", None) and t.ablate.get("no_tournament"):
        return (1.0 if idx_mano < 35 else 0.0), (1.0 if idx_mano < 18 else 0.0)
    is_mw = t.detras() >= 2
    eff_bb = min(t.stack_bb, t.stack_efectivo_bb)
    return _interpolar_push_fold(eff_bb, is_mw, idx_mano)


def minimo_por_supervivencia(t, made_tier, req):
    """Ajusta la equity requerida cuando la decision arriesga una fraccion alta del stack."""
    if getattr(t, "ablate", None) and t.ablate.get("no_tournament"):
        return req

    c = t.compromiso
    # Si las pot odds son excelentes y NO arriesgamos eliminacion directa, evitar foldear
    if t.pot_odds <= 0.26 and c <= 0.45:
        return req
    if t.pot_odds <= 0.28 and c > 0.65 and made_tier <= 2:
        return max(req, 0.48)
    if t.pot_odds <= 0.28:
        return req

    stack_bb = t.stack_bb
    # Si estamos muy cortos (<= 14 bb), evitar foldear por exceso de cautela
    if stack_bb <= 14.0:
        return req * 0.96

    # Si cubrimos comodamente al rival (su stack efectivo < 45% del nuestro), el
    # riesgo de ruina propio es bajo -> menor aversion.
    rivales_saldos = [r.get("saldo", 0) + r.get("apuesta_ronda", 0) for r in t.rivales]
    max_riv = max(rivales_saldos) if rivales_saldos else 0
    mi_tot = t.saldo + t.mi_apuesta
    cubrimos = mi_tot > max_riv * 1.4 and max_riv > 0

    factor_riesgo = 0.72 if cubrimos else 1.0
    if c > 0.55:
        piso = 0.56 if made_tier <= 2 else (0.51 if made_tier == 3 else 0.45)
        if cubrimos:
            piso -= 0.025
        return max(req + 0.035 * factor_riesgo, piso)
    if c > 0.30:
        piso = 0.47 if made_tier <= 2 else 0.42
        return max(req + 0.025 * factor_riesgo, piso)
    return req


def factor_presion_eliminacion(t):
    """Multiplicador de agresividad (>1 si cubrimos stacks cortos, <=1 si estamos en riesgo)."""
    if getattr(t, "ablate", None) and t.ablate.get("no_tournament"):
        return 1.0
    mi_tot = t.saldo + t.mi_apuesta
    rivales_saldos = [r.get("saldo", 0) + r.get("apuesta_ronda", 0) for r in t.rivales]
    if not rivales_saldos:
        return 1.0
    min_riv = min(rivales_saldos)
    cg = max(1, t.ciega_grande)
    # Si algun rival activo tiene <= 16 bb y lo cubrimos >= 2x, conviene presionar
    if min_riv <= 16 * cg and mi_tot >= 2.0 * min_riv:
        return 1.12
    # Si estamos entre los mas cortos (< 25 bb) con mesa profunda, cuidar fichas en spots marginales
    if mi_tot < 25 * cg and max(rivales_saldos) > 2.2 * mi_tot:
        return 0.94
    return 1.0

"""Resolucion local exacta de subjuego en situaciones criticas (River HU).

Cuando el pozo es significativo en el river contra un solo rival y el
presupuesto temporal lo permite, evalua de manera EXACTA (sin ruido Monte Carlo)
el EV de las acciones candidatas integrando sobre la distribucion bayesiana de
combinaciones del rival, combinando:
  - Frecuencia de fold/call del modelo estadistico del rival.
  - Regularizacion por MDF (Minimum Defense Frequency) para evitar sobreajuste
    con pocas manos observadas.
"""

from motor import evaluar, valores_en_mesa
from rangos import RangoPonderado


def _limitar(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _ordenar_combos_river(t, rango):
    """Lista ordenada de (valor_eval, peso) del rango rival en el river actual."""
    vistas = set(t.mano)
    vistas.update(t.board)
    valores_map = valores_en_mesa(t.board)
    items = []
    if isinstance(rango, RangoPonderado):
        for (a, b), w in zip(rango.combos, rango.pesos):
            if w <= 0.0 or a in vistas or b in vistas:
                continue
            v = valores_map.get((a, b))
            if v is None:
                v = evaluar([a, b] + t.board)
            items.append((v, w))
    else:
        from motor import combos_de_rango
        for a, b in combos_de_rango(rango, t.board, vistas):
            v = valores_map.get((a, b))
            if v is None:
                v = evaluar([a, b] + t.board)
            items.append((v, 1.0))
    items.sort(key=lambda x: x[0], reverse=True)
    return items


def resolver_river_hu(t, rango, equity_exacta, made_tier, estacion, nit, loco):
    """Evalua EV exacto de acciones en river HU cuando estamos sin apuesta pendiente
    o enfrentando apuesta, devolviendo (tipo_recomendado, frac_o_none, ev_rel) o None.
    """
    if getattr(t, "ablate", None) and t.ablate.get("no_subgame"):
        return None
    if len(t.board) != 5 or t.rivales_en_mano != 1:
        return None
    cg = max(1, t.ciega_grande)
    if t.pozo < 6 * cg and t.compromiso < 0.20:
        return None

    items = _ordenar_combos_river(t, rango)
    if not items:
        return None
    tot_w = sum(w for _, w in items)
    if tot_w <= 0.0:
        return None

    mio = evaluar(list(t.mano) + list(t.board))
    rival = t.rivales[0]
    st = t.stats.rivales.get(rival.get("id")) if t.stats else None
    conf = st.confianza if st else 0.0
    fold_obs = st.fold_post if st else 0.45
    if estacion:
        fold_obs = min(fold_obs, 0.25)
    elif nit:
        fold_obs = max(fold_obs, 0.62)

    pozo = float(t.pozo)
    eff = float(min(t.saldo, rival.get("saldo", 0)))

    # --- Caso 1: Nadie aposto aun -> comparar Check vs Bet (0.45p, 0.80p, 1.10p)
    if t.por_igualar == 0:
        ev_check = equity_exacta * pozo
        mejor_ev = ev_check
        mejor_frac = None

        for frac in (0.45, 0.80, 1.10):
            ap = min(eff, pozo * frac)
            if ap < cg:
                continue
            # MDF de equilibrio: el rival debe defender pot/(pot+ap)
            mdf = pozo / (pozo + ap)
            p_fold_eq = 1.0 - mdf
            # Mezcla regularizada entre fold observado y equilibrio segun tamano
            # Apuestas mas grandes generan algo mas de fold
            ajuste_sz = 0.88 if frac <= 0.5 else (1.02 if frac <= 0.85 else 1.12)
            p_fold_rival = _limitar(fold_obs * ajuste_sz, 0.05, 0.78)
            if estacion:
                p_fold_rival *= 0.65
            p_fold = (1.0 - conf) * p_fold_eq + conf * p_fold_rival
            p_call = 1.0 - p_fold

            # El rival paga con el top p_call de sus manos ordenadas
            target_w = p_call * tot_w
            w_acc = 0.0
            win_call = 0.0
            for v, w in items:
                if w_acc >= target_w:
                    break
                take = min(w, target_w - w_acc)
                w_acc += take
                if mio > v:
                    win_call += take
                elif mio == v:
                    win_call += 0.5 * take
            eq_call = (win_call / w_acc) if w_acc > 0.0 else equity_exacta

            # Riesgo implicito de recibir raise cuando apostamos marginal (descuento conservador)
            raise_penalty = 0.0
            if made_tier <= 2 and eq_call < 0.62:
                raise_penalty = 0.06 * ap

            ev_bet = p_fold * pozo + p_call * (eq_call * (pozo + 2.0 * ap) - ap) - raise_penalty
            if ev_bet > mejor_ev + 0.025 * pozo:
                mejor_ev = ev_bet
                mejor_frac = frac

        if mejor_frac is not None:
            return ("bet", mejor_frac, mejor_ev - ev_check)
        return ("check", None, 0.0)

    # --- Caso 2: Enfrentando apuesta -> comparar Fold, Call, Raise(0.80p) ---
    costo = float(min(t.por_igualar, t.saldo))
    no_ig = float(t.por_igualar) - costo
    pozo_efectivo = pozo - no_ig
    bote_final = pozo_efectivo + costo
    # Margen de seguridad frente a apuestas polarizadas en river (evita bluff-catch marginal)
    margen_call = 0.015 if loco else 0.040
    ev_call = equity_exacta * bote_final - costo
    ev_call_ajustado = (equity_exacta - margen_call) * bote_final - costo

    # Solo considerar raise por valor si vamos muy adelante contra su rango de bet/call
    if t.puede("raise") and eff > costo + cg and equity_exacta >= 0.80:
        ap_extra = min(eff - costo, bote_final * 0.80)
        mdf = bote_final / (bote_final + ap_extra)
        p_fold = (1.0 - conf) * (1.0 - mdf) + conf * _limitar(fold_obs * 0.9, 0.10, 0.65)
        p_call = 1.0 - p_fold
        target_w = p_call * tot_w
        w_acc = 0.0
        win_call = 0.0
        for v, w in items:
            if w_acc >= target_w:
                break
            take = min(w, target_w - w_acc)
            w_acc += take
            if mio > v:
                win_call += take
            elif mio == v:
                win_call += 0.5 * take
        eq_call = (win_call / w_acc) if w_acc > 0.0 else equity_exacta
        ev_raise = p_fold * pozo_efectivo + p_call * (eq_call * (pozo_efectivo + 2.0 * (costo + ap_extra)) - (costo + ap_extra))
        if ev_raise > max(0.0, ev_call) + 0.05 * pozo_efectivo:
            return ("raise", 0.80, ev_raise - max(0.0, ev_call))

    return ("call" if ev_call_ajustado > 0.0 else "fold", None, ev_call_ajustado)

"""Decisiones en flop, turn y river (Pokalatro v9): Arquitectura Hibrida.

Integra los cinco componentes:
  1. Abstraccion estrategica (`made_tier`, `draw_tier`, `nut_blocker`, `SPR`, textura).
  2. Blueprint MCCFR precomputado (`POSTFLOP_BP`) como ancla de equilibrio.
  3. Equity bayesiana contra distribuciones ponderadas de rivales (`rangos.py`).
  4. Explotacion regularizada por confianza estadistica (cobro grande a estaciones,
     presion sobre nits, bluff-catch y trampas contra locos/aleatorios).
  5. Resolucion exacta de subjuego en river HU (`subjuego.py`) y prima de
     supervivencia / presion de eliminacion (`torneo.py`).
"""

import random

from abstraccion import bucket_facing, bucket_spr, bucket_textura, clasificar_mano_postflop
from blueprint_data import POSTFLOP_BP
from motor import equity
from rangos import rango_rival, rangos_de_la_mesa
from subjuego import resolver_river_hu
from torneo import factor_presion_eliminacion, minimo_por_supervivencia

PRESUPUESTO_S = 0.065
MUESTRAS = 4500

# Umbrales base de equity para apostar por valor segun rivales activos
VALOR = {1: 0.57, 2: 0.49, 3: 0.43, 4: 0.39}


def _limitar(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _lecturas(t, rivales):
    if not t.stats or not rivales:
        return 0.45, 0.0, False, False, False
    fichas = [t.stats.de(r.get("id")) for r in rivales]
    fold = sum(f.fold_post for f in fichas) / len(fichas)
    conf = sum(f.confianza for f in fichas) / len(fichas)
    estacion = any(f.es_estacion for f in fichas)
    nits = all(f.es_nit for f in fichas)
    loco = any(f.es_loco for f in fichas)
    return fold, conf, estacion, nits, loco


def _realizacion(t, made_tier, draw_tier, river):
    if river or made_tier >= 4:
        return 1.0
    base = 0.93 if t.en_posicion else 0.86
    if draw_tier >= 2 and made_tier <= 1:
        # Un proyecto puro fuera de posicion realiza algo menos en flop/turn
        base -= 0.04 if not t.en_posicion else 0.0
    return base


def _tamano_valor(t, e, made_tier, estacion, nits, loco):
    if e >= 0.88:
        frac = 0.92
    elif e >= 0.76:
        frac = 0.78
    elif e >= 0.64:
        frac = 0.56
    else:
        frac = 0.42
    frac += 0.12 * t.humedad
    if estacion:
        frac += 0.15
    elif nits:
        frac -= 0.08
    elif loco:
        frac += 0.06
    # Geometria SPR: con SPR bajo y TPTK+ podemos comprometer mas rapido
    if t.spr <= 2.2 and made_tier >= 3 and e >= 0.68:
        frac = max(frac, 0.72)
    # Control de pozo con una pareja debil/media
    if made_tier <= 2 and e < 0.78:
        frac = min(frac, 0.52 if t.spr > 3.0 else 0.58)
    return _limitar(frac, 0.30, 1.05)


def decidir_postflop(t, cartas=None):
    rivales = t.rivales
    n = len(rivales)
    if n == 0:
        return t.pasar_o_retirarse(), "no quedan rivales"

    ablate = getattr(t, "ablate", None) or {}
    usar_bp = not ablate.get("no_blueprint")
    usar_exploit = not ablate.get("no_exploit")

    rangos = rangos_de_la_mesa(t)
    e = equity(t.mano, t.board, rangos, presupuesto_s=PRESUPUESTO_S,
               max_muestras=MUESTRAS, min_muestras=350)

    made_tier, draw_tier, nut_blocker = clasificar_mano_postflop(t.mano, t.board)
    river = len(t.board) == 5
    st_idx = 2 if river else (1 if len(t.board) == 4 else 0)
    tex_b = bucket_textura(t.humedad, t.emparejada)
    spr_b = bucket_spr(t.spr)
    pos_b = 1 if t.en_posicion else 0
    mw_b = 1 if n > 1 else 0
    ya_actuo = any(
        h.get("etapa") == t.etapa for h in t.historial
    )
    fac_b = bucket_facing(t.por_igualar, t.pozo, t.subidas_en_esta_calle, ya_actuo)
    bp_0, bp_1, bp_2 = POSTFLOP_BP.get(
        (st_idx, spr_b, pos_b, mw_b, made_tier, 0 if river else draw_tier, fac_b),
        (0.5, 0.5, 0.0),
    )

    fold_medio, conf_media, estacion, nits, loco = _lecturas(t, rivales)
    if not usar_exploit:
        fold_medio, conf_media, estacion, nits, loco = 0.45, 0.0, False, False, False

    # --- 1. Subjuego exacto en River HU de alto impacto ----------------------
    if river and n == 1:
        sub_res = resolver_river_hu(t, rangos[0], e, made_tier, estacion, nits, loco)
        if sub_res is not None:
            s_tipo, s_frac, s_ev = sub_res
            # Mezcla regularizada: si el EV extra es claro o la confianza es alta, seguir subjuego;
            # si hay poca confianza y es marginal, permitir ancla de Blueprint.
            if s_tipo == "bet" and (t.puede("bet") or t.puede("raise")):
                return t.apostar_pozo(s_frac), f"subjuego river bet {s_frac:.2f}p (eq {e:.0%})"
            if s_tipo == "raise" and t.puede("raise"):
                return t.apostar_pozo(s_frac), f"subjuego river raise (eq {e:.0%})"
            if s_tipo == "call" and t.por_igualar > 0:
                base_req = t.pot_odds + (0.02 if (made_tier <= 2 and t.compromiso > 0.20) else 0.0)
                req_s = minimo_por_supervivencia(t, made_tier, base_req)
                if e >= req_s:
                    return t.pagar(), f"subjuego river call (eq {e:.0%} vs {req_s:.0%})"
                return t.pasar_o_retirarse(), f"subjuego+torneo fold (eq {e:.0%} vs {req_s:.0%})"
            if s_tipo == "fold" and t.por_igualar > 0:
                # Si el blueprint insiste en pagar bluff-catcher con muy alta prob y rival es desconocido
                if usar_bp and conf_media < 0.25 and bp_1 >= 0.72 and e >= t.pot_odds * 0.92:
                    return t.pagar(), f"bluff-catch blueprint (eq {e:.0%})"
                return t.pasar_o_retirarse(), f"subjuego river fold (eq {e:.0%})"
            if s_tipo == "check" and t.por_igualar == 0:
                # Permitir farol ocasional con bloqueador de nuts si rival foldea mucho
                if (usar_exploit and nut_blocker and made_tier == 0 and not estacion
                        and fold_medio >= 0.50 and random.random() < 0.22):
                    return t.apostar_pozo(0.60), "farol river con nut blocker"
                return t.pasar_o_retirarse(), f"subjuego river check (eq {e:.0%})"

    # --- 2. Enfrentando apuesta ----------------------------------------------
    if t.por_igualar > 0:
        e_pago = e * _realizacion(t, made_tier, draw_tier, river)
        req = t.pot_odds
        req *= 0.97 if t.en_posicion else 1.05

        if loco:
            # Contra maniacos reducimos ligeramente el margen extra sin regalar fichas en calles caras
            req += {"flop": 0.015, "turn": 0.03, "river": 0.035}.get(t.etapa, 0.0)
        else:
            req += {"flop": 0.02, "turn": 0.045, "river": 0.05}.get(t.etapa, 0.0)
            if t.subidas_en_esta_calle >= 2 and not nits:
                req += 0.03

        if n > 1:
            req *= 1.05
        req = minimo_por_supervivencia(t, made_tier, req)

        # Subir por valor con manos fuertes (con limite contra 3-bet/4-bet postflop)
        umbral_raise = 0.75 if n == 1 else 0.84
        if made_tier <= 2:
            umbral_raise = 0.92
        elif made_tier == 3 and t.spr <= 2.2:
            umbral_raise = 0.78
        if t.subidas_en_esta_calle >= 2:
            umbral_raise = max(umbral_raise, 0.89)
        elif t.subidas_en_esta_calle >= 1 and (made_tier <= 4 or t.spr > 4.0):
            umbral_raise = max(umbral_raise, 0.83)
        if e >= umbral_raise and t.puede("raise"):
            # Contra un loco en posicion en flop/turn, a veces solo pagar para que siga faroleando
            if loco and not river and t.en_posicion and made_tier >= 4 and random.random() < 0.35:
                return t.pagar(), f"trampa slowplay vs agresivo (eq {e:.0%})"
            return t.apostar_pozo(0.80), f"subo por valor (eq {e:.0%})"

        if e_pago >= req:
            return t.pagar(), f"pago (eq {e_pago:.0%} vs {req:.0%})"

        # Apoyo del Blueprint MCCFR cerca del umbral con poca muestra
        if usar_bp and conf_media < 0.35 and bp_1 >= 0.65 and e_pago >= req * 0.92 and t.compromiso <= 0.18:
            return t.pagar(), f"pago frontera blueprint (eq {e_pago:.0%}, bp {bp_1:.2f})"

        # Semi-farol raise SOLO con proyecto fuerte real y riesgo acotado del stack
        riesgo_raise = t.por_igualar + t.pozo * 0.90
        if (not river and n == 1 and t.puede("raise") and not estacion
                and draw_tier >= 2 and made_tier <= 2
                and t.subidas_en_esta_calle <= 1
                and t.por_igualar <= 0.50 * t.pozo
                and riesgo_raise <= 0.38 * max(1, t.saldo)
                and fold_medio >= 0.45):
            p_semi = 0.24 if draw_tier == 3 else 0.14
            if usar_bp:
                p_semi = 0.5 * p_semi + 0.5 * bp_2
            if random.random() < p_semi:
                return t.apostar_pozo(0.85), f"semi-farol raise draw={draw_tier} (eq {e:.0%})"

        return t.pasar_o_retirarse(), f"fold (eq {e_pago:.0%} vs {req:.0%})"

    # --- 3. Nadie aposto aun -------------------------------------------------
    if not (t.puede("bet") or t.puede("raise")):
        return t.pasar_o_retirarse(), "no puedo apostar"

    umbral_valor = VALOR.get(n, 0.38)
    if river:
        umbral_valor += 0.04
    # Con estacion en la mesa ampliamos ligeramente el valor fino
    if estacion and n <= 2:
        umbral_valor -= 0.03

    if e >= umbral_valor:
        # Contra rival muy agresivo/loco en flop con monstruo OOP, check ocasional para check-raise
        if loco and not river and not t.en_posicion and made_tier >= 5 and random.random() < 0.28:
            return t.pasar_o_retirarse(), f"check trampa vs agresivo (eq {e:.0%})"
        frac = _tamano_valor(t, e, made_tier, estacion, nits, loco)
        return t.apostar_pozo(frac), f"apuesta por valor (eq {e:.0%})"

    # Semi-farol con proyecto real (draw_tier >= 2 o draw_tier == 1 IP en flop)
    if not river and n <= 2 and not estacion and (draw_tier >= 2 or (draw_tier == 1 and t.etapa == "flop" and t.en_posicion)):
        p_bp_bet = bp_1 + bp_2
        p_heu = (0.55 if draw_tier >= 2 else 0.30) * (1.15 if t.en_posicion else 0.78)
        p_heu *= _limitar(fold_medio / 0.45, 0.55, 1.45) * factor_presion_eliminacion(t)
        p = (0.45 * p_bp_bet + 0.55 * p_heu) if usar_bp else p_heu
        if random.random() < _limitar(p, 0.0, 0.80):
            return t.apostar_pozo(0.55), f"semi-farol draw={draw_tier} (eq {e:.0%})"

    # Apuesta de continuacion y faroles con bloqueadores (nunca convertir parejas con SDV en bluff en river!)
    if n == 1 and not estacion:
        p = 0.0
        if t.etapa == "flop" and t.fui_agresor_preflop and made_tier == 0:
            p = 0.50 if tex_b == 0 else 0.26
        elif not river and t.aposte_calle_anterior and (draw_tier >= 1 or nut_blocker):
            p = 0.18
        elif river and made_tier == 0:
            # En river solo farolear con aire puro (preferentemente con bloqueador)
            p_bp_bluff = bp_1 + bp_2
            p = 0.14 if nut_blocker else 0.05
            if usar_bp:
                p = 0.5 * p + 0.5 * min(0.25, p_bp_bluff)
        elif not river and t.en_posicion and made_tier == 0:
            p = 0.16

        p *= _limitar(fold_medio / 0.45, 0.40, 1.55) * factor_presion_eliminacion(t)
        if p > 0.0 and random.random() < _limitar(p, 0.0, 0.72):
            return t.apostar_pozo(0.50), f"cbet/farol controlado (eq {e:.0%})"

    return t.pasar_o_retirarse(), f"paso (eq {e:.0%})"

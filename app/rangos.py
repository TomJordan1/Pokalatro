"""Modelado bayesiano continuo de rangos rivales sobre 1 326 combinaciones.

Sustituye los cortes binarios de v8 por una actualizacion probabilistica secuencial:
  P(mano | acciones, stats) proporcional a Prior_preflop(mano) * prod L(accion_t | mano, board, stats)

Corrige ademas:
  - Uso del ranking de jugabilidad preflop realista (`abstraccion.COMBOS_ORDENADOS`).
  - Rangos topeados (*capped*) cuando un rival solo paga preflop sin 3-betear.
  - Diferenciacion de perfiles locos/aleatorios vs tight/conservadores y calibracion
    por `fuerza_al_apostar`, `fuerza_al_allin` y tamaño relativo de apuesta.
"""

import math

from abstraccion import COMBOS_ORDENADOS
from motor import Rango, RangoPonderado, puntajes_en_mesa

AGRESIVAS = ("bet", "raise", "allin")

# Precalculo de fraccion de rango [0..1) por combo canonico (0 = AA, ~1 = 32o)
_N_COMBOS = len(COMBOS_ORDENADOS)
_FRAC_COMBO = tuple(i / float(_N_COMBOS) for i in range(_N_COMBOS))


def _limitar(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _sigmoide(x):
    if x >= 20.0:
        return 1.0
    if x <= -20.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def _ancho_preflop(acciones_pre, st):
    subidas = sum(1 for a in acciones_pre if a in AGRESIVAS)
    pagos = sum(1 for a in acciones_pre if a == "call")

    if "allin" in acciones_pre:
        if st and st.es_loco:
            return 0.65, True
        if st and st.n_fuerza_allin >= 2 and st.fuerza_al_allin < 0.70:
            return _limitar((0.90 - st.fuerza_al_allin) * 1.6, 0.12, 0.75), True
        return 0.11, True
    if subidas >= 3:
        return 0.03, True
    if subidas == 2:
        return (0.18 if (st and st.es_loco) else 0.055), True
    if subidas == 1:
        ancho = 0.22
        if st and st.manos_eff >= 8:
            ancho = _limitar(st.pfr_pct * 1.12, 0.07, 0.55)
        return ancho, True
    if pagos >= 1:
        ancho = 0.42
        if st and st.manos_eff >= 8:
            pago = max(0.09, st.vpip_pct - st.pfr_pct * 0.55)
            ancho = _limitar(pago * 1.15, 0.16, 0.80)
        return ancho, True
    return 1.0, False


def _faroles(st):
    if st is None:
        return 0.15
    if st.n_fuerza_apuesta >= 2:
        return _limitar((0.85 - st.fuerza_al_apostar) / 0.5, 0.05, 0.50)
    return 0.35 if st.es_loco else 0.15


def rango_v8_compat(turno, rival):
    """Modo compatible con v8 (para pruebas de ablacion sin modelo bayesiano)."""
    jid = rival.get("id")
    st = turno.stats.rivales.get(jid) if turno.stats else None
    acciones = [(h.get("etapa"), (h.get("accion") or {}).get("tipo"))
                for h in turno.historial if h.get("id_jugador") == jid]
    ancho, voluntario = _ancho_preflop([t for e, t in acciones if e == "preflop"], st)
    if voluntario and st is not None:
        por_sd = st.rango_showdown
        if por_sd is not None:
            ancho = 0.70 * ancho + 0.30 * _limitar(por_sd, 0.05, 0.95)
    ancho = _limitar(ancho, 0.025, 1.0)
    estacion = bool(st and st.es_estacion)
    conserva = 1.0
    sin_techo = 1.0
    etapas = ("flop", "turn", "river")
    for i, etapa in enumerate(etapas):
        tipos = [t for e, t in acciones if e == etapa]
        if not tipos:
            continue
        agresivo = any(t in AGRESIVAS for t in tipos)
        agresivo_despues = any(t in AGRESIVAS for e, t in acciones if e in etapas[i + 1:])
        for t in tipos:
            if t == "allin":
                conserva *= 0.40
            elif t in AGRESIVAS:
                conserva *= 0.50
            elif t == "call":
                conserva *= 0.85 if estacion else 0.70
        if "check" in tipos and not agresivo and not agresivo_despues:
            sin_techo *= 0.75
    return Rango(ancho, conserva=_limitar(conserva, 0.04, 1.0),
                 techo=_limitar(1.0 - sin_techo, 0.0, 0.6), farol=_faroles(st))


def rango_rival(turno, rival):
    """Rango probabilistico bayesiano sobre 1 326 combos para este rival."""
    if getattr(turno, "ablate", None) and turno.ablate.get("no_bayes"):
        return rango_v8_compat(turno, rival)

    jid = rival.get("id")
    st = turno.stats.rivales.get(jid) if turno.stats else None

    acciones_full = [
        (h.get("etapa"), h.get("accion") or {})
        for h in turno.historial if h.get("id_jugador") == jid
    ]
    acciones_pre = [a.get("tipo") for e, a in acciones_full if e == "preflop"]
    ancho, voluntario = _ancho_preflop(acciones_pre, st)

    if voluntario and st is not None:
        por_sd = st.rango_showdown
        if por_sd is not None:
            ancho = 0.72 * ancho + 0.28 * _limitar(por_sd, 0.05, 0.95)
    ancho = _limitar(ancho, 0.025, 1.0)

    loco = bool(st and st.es_loco)
    estacion = bool(st and st.es_estacion)

    # Capeo de rango preflop: quien solo pago/limpeo sin subir rara vez tiene AA/KK
    subidas_pre = sum(1 for t in acciones_pre if t in AGRESIVAS)
    pagos_pre = sum(1 for t in acciones_pre if t == "call")
    top_cap = 0.0
    if subidas_pre == 0 and pagos_pre >= 1:
        # Si suele subir preflop (pfr >= 0.12), su rango de call esta topeado en top 3-5%
        pfr = st.pfr_pct if st else 0.16
        if pfr >= 0.10:
            top_cap = _limitar(pfr * 0.25, 0.02, 0.06)
    elif subidas_pre == 0 and pagos_pre == 0 and any(t == "check" for t in acciones_pre):
        # BB check: tampoco suele tener AA/KK/QQ/AK salvo trampa rara
        pfr = st.pfr_pct if st else 0.16
        if pfr >= 0.12:
            top_cap = 0.035

    vistas = set(turno.mano)
    vistas.update(turno.board)

    # --- 1. Prior preflop por combo ------------------------------------------
    piso_pre = 0.14 if loco else (0.04 if not voluntario else 0.012)
    pendiente_pre = 9.0 if loco else 18.0
    combos_validos = []
    pesos = []
    for idx, (a, b) in enumerate(COMBOS_ORDENADOS):
        if a in vistas or b in vistas:
            continue
        u = _FRAC_COMBO[idx]
        # Ventana suave [top_cap, ancho]
        w = _sigmoide((ancho - u) * pendiente_pre)
        if top_cap > 0.0 and u < top_cap:
            # Penalizacion al tope de rango para callers pasivos (sin hacerlo 0)
            w *= 0.22 + 0.78 * (u / top_cap)
        w = max(piso_pre if not voluntario else piso_pre * 0.5, w)
        combos_validos.append((a, b))
        pesos.append(w)

    board = turno.board
    farol = _faroles(st)
    if st is not None and any(e == "river" and (a.get("tipo") in AGRESIVAS) for e, a in acciones_full):
        exceso = st.frecuencia_apuesta_rio - 0.30
        farol = max(farol, _limitar(0.15 + 1.2 * exceso, 0.10, 0.58))

    if len(board) < 3:
        return RangoPonderado(combos_validos, pesos, farol_est=farol, techo=0.0)

    # --- 2. Actualizacion bayesiana postflop por fuerza en tablero real ------
    puntajes = puntajes_en_mesa(board)
    scores = [puntajes.get(ab, 0.5) for ab in combos_validos]

    etapas = ("flop", "turn", "river")
    calib_apuesta = st.multiplicador_apuesta if (st and st.n_fuerza_apuesta >= 2) else 0.52
    # Menor multiplicador -> mas concentrado en valor; mayor -> mas bluffs
    concentracion = _limitar(0.52 / max(0.30, calib_apuesta), 0.55, 1.75)
    if loco:
        concentracion *= 0.65

    # Calcular apuestas previas para estimar tamano relativo
    pozo_ref = max(float(turno.ciega_grande), float(turno.pozo) * 0.45)
    sin_techo = 1.0

    agresivas_prev = 0
    for i_et, etapa in enumerate(etapas):
        acc_et = [a for e, a in acciones_full if e == etapa]
        if not acc_et:
            continue
        agresivo_despues = any(
            (a.get("tipo") in AGRESIVAS)
            for e, a in acciones_full if e in etapas[i_et + 1:]
        )
        hizo_agresiva = False
        for acc in acc_et:
            tipo = acc.get("tipo")
            if tipo in AGRESIVAS:
                hizo_agresiva = True
                # Sizing relativo aumenta la polarizacion/fuerza requerida
                monto = acc.get("monto") or 0
                sizing_boost = 1.0
                if tipo == "allin" or monto > pozo_ref * 1.6:
                    sizing_boost = 1.60 if not loco else 1.05
                elif monto > pozo_ref * 0.8:
                    sizing_boost = 1.25
                if agresivas_prev >= 1:
                    sizing_boost *= 1.15
                umbral_v = 0.66 if tipo != "allin" else 0.78
                if st and st.n_fuerza_apuesta >= 2:
                    umbral_v = _limitar(st.fuerza_al_apostar - 0.08, 0.48, 0.86)
                if agresivas_prev >= 1:
                    umbral_v = max(umbral_v, 0.74 if not loco else 0.60)
                if loco and agresivas_prev == 0 and tipo != "allin":
                    umbral_v = min(umbral_v, 0.54)
                k = 11.0 * concentracion * sizing_boost
                escala_bluff = 0.55 if loco else 0.22
                if agresivas_prev >= 1 or tipo == "allin":
                    escala_bluff *= 0.50
                piso_bluff = max(0.01, farol * escala_bluff)
                agresivas_prev += 1
                for idx in range(len(pesos)):
                    p = scores[idx]
                    like = piso_bluff + (1.0 - piso_bluff) * _sigmoide((p - umbral_v) * k)
                    pesos[idx] *= like

            elif tipo == "call":
                # Pagar descarta aire puro y reduce ligeramente los monstruos extremos
                # si el rival suele subir con ellos.
                piso_call = 0.18 if estacion else 0.06
                umbral_c = 0.28 if estacion else 0.40
                capeo_nuts = 0.68 if (st and st.agresion >= 0.32 and not estacion) else 0.96
                for idx in range(len(pesos)):
                    p = scores[idx]
                    like = piso_call + (1.0 - piso_call) * _sigmoide((p - umbral_c) * 6.0)
                    if p > 0.90 and capeo_nuts < 0.95:
                        like *= capeo_nuts
                    pesos[idx] *= like

        if any(a.get("tipo") == "check" for a in acc_et) and not hizo_agresiva and not agresivo_despues:
            sin_techo *= 0.75
            # Paso y no aposto luego: atenua monstruos del top 12%
            for idx in range(len(pesos)):
                p = scores[idx]
                if p > 0.86:
                    pesos[idx] *= 0.42

    # Normalizar para estabilidad numerica sin destruir rangos estrechos
    mx = max(pesos) if pesos else 1.0
    if mx > 0.0:
        inv = 1.0 / mx
        pesos = [w * inv for w in pesos]
    return RangoPonderado(combos_validos, pesos, farol_est=farol,
                          techo=_limitar(1.0 - sin_techo, 0.0, 0.6))


def rangos_de_la_mesa(turno):
    return [rango_rival(turno, r) for r in turno.rivales]

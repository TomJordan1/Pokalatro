"""Que manos puede tener cada rival.

Dos etapas:

1. Antes del flop, el rango es un ANCHO: la fraccion de las mejores manos
   iniciales con que juega (0.10 = solo el 10% mejor). Sale de lo que hizo
   en la mano, de sus estadisticas en todo el torneo y de lo que mostro en
   showdowns anteriores.

2. Despues del flop, cada accion filtra ese rango por la fuerza de cada mano
   EN ESTA MESA (motor.Rango / motor.combos_de_rango):
     - apostar o subir: quedan sus manos fuertes en la mesa, mas algunos faroles;
     - pagar: quedan las manos con algo (pareja, proyecto), sin las peores;
     - pasar pudiendo apostar: se descartan sus mejores manos, porque casi
       todos los bots apuestan cuando tienen algo grande.

Ese segundo paso es la diferencia con estrechar por percentil preflop: quien
paga tres calles y resube el river tiene escalera o trio en esta mesa, aunque
sus cartas iniciales no fueran premium.
"""

from motor import Rango

AGRESIVAS = ("bet", "raise", "allin")


def _limitar(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _ancho_preflop(acciones_pre, st):
    """Ancho del rango segun lo que hizo antes del flop."""
    subidas = sum(1 for a in acciones_pre if a in AGRESIVAS)
    pagos = sum(1 for a in acciones_pre if a == "call")

    if "allin" in acciones_pre:
        return 0.13, True
    if subidas >= 3:
        return 0.025, True                      # 5-bet: AA/KK/AK
    if subidas == 2:
        return 0.05, True                       # 4-bet o resubida: manos grandes
    if subidas == 1:
        ancho = 0.20
        if st and st.manos >= 10:
            ancho = _limitar(st.pfr_pct * 1.10, 0.07, 0.45)
        return ancho, True
    if pagos >= 1:
        ancho = 0.40
        if st and st.manos >= 10:
            pago = max(0.08, st.vpip_pct - st.pfr_pct * 0.5)
            ancho = _limitar(pago * 1.15, 0.15, 0.75)
        return ancho, True
    return 1.0, False                            # solo puso la ciega: cualquier mano


def _faroles(st):
    """Que parte de sus apuestas son faroles o proyectos."""
    if st is None:
        return 0.15
    if st.n_fuerza_apuesta >= 2:
        # Calibrado con lo que mostro en showdowns despues de apostar.
        return _limitar((0.85 - st.fuerza_al_apostar) / 0.5, 0.05, 0.45)
    return 0.30 if st.es_loco else 0.15


def rango_rival(turno, rival):
    """El Rango probable de este rival en este momento de la mano."""
    jid = rival.get("id")
    st = turno.stats.rivales.get(jid) if turno.stats else None

    acciones = [(h.get("etapa"), (h.get("accion") or {}).get("tipo"))
                for h in turno.historial if h.get("id_jugador") == jid]
    ancho, voluntario = _ancho_preflop([t for e, t in acciones if e == "preflop"], st)

    if voluntario and st is not None:
        por_showdown = st.rango_showdown
        if por_showdown is not None:
            ancho = 0.70 * ancho + 0.30 * _limitar(por_showdown, 0.05, 0.95)
    ancho = _limitar(ancho, 0.025, 1.0)

    estacion = bool(st and st.es_estacion)
    conserva = 1.0
    sin_techo = 1.0          # fraccion de sus mejores manos que SIGUE en el rango
    etapas = ("flop", "turn", "river")
    for i, etapa in enumerate(etapas):
        tipos = [t for e, t in acciones if e == etapa]
        if not tipos:
            continue
        agresivo = any(t in AGRESIVAS for t in tipos)
        # Si despues de pasar volvio a apostar en una calle posterior, el
        # paso pudo ser una trampa con un monstruo: no se descarta nada.
        agresivo_despues = any(t in AGRESIVAS for e, t in acciones if e in etapas[i + 1:])
        for t in tipos:
            if t == "allin":
                conserva *= 0.40
            elif t in AGRESIVAS:
                conserva *= 0.50
            elif t == "call":
                # Una estacion paga con cualquier cosa: pagar dice poco.
                conserva *= 0.85 if estacion else 0.70
        if "check" in tipos and not agresivo and not agresivo_despues:
            # Paso y no volvio a subir en esa calle: no tiene un monstruo.
            sin_techo *= 0.75

    farol = _faroles(st)
    if st is not None and any(e == "river" and t in AGRESIVAS for e, t in acciones):
        # Apuesta en el river de un rival que apuesta el river muy seguido:
        # parte tienen que ser faroles, aunque nunca los haya mostrado.
        exceso = st.frecuencia_apuesta_rio - 0.30
        farol = max(farol, _limitar(0.15 + 1.2 * exceso, 0.10, 0.55))

    return Rango(ancho,
                 conserva=_limitar(conserva, 0.04, 1.0),
                 techo=_limitar(1.0 - sin_techo, 0.0, 0.6),
                 farol=farol)


def rangos_de_la_mesa(turno):
    """Los rangos de todos los rivales que siguen en la mano."""
    return [rango_rival(turno, r) for r in turno.rivales]

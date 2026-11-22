"""Lectura estadistica de rivales con priores Beta y decaimiento temporal.

El id de cada rival es estable durante todo el torneo (`j-xxxxxx`).
En v9 se separan contextos criticos (open vs 3-bet, c-bet, all-in pre/post,
showdowns voluntarios vs checks gratis en BB) y se aplica un factor de olvido
por mano (`DECAY = 0.996`) para seguir a rivales adaptativos o cambiantes.
"""

AGRESIVAS = ("bet", "raise", "allin")
DECAY = 0.996


class Rival:
    __slots__ = (
        "manos", "manos_eff", "vpip", "pfr", "three_bet_oport", "three_bet",
        "apuestas", "pagos",
        "enfrento_pre", "folds_pre",
        "enfrento_3bet", "folds_3bet",
        "enfrento_post", "folds_post",
        "cbets_oport", "cbets",
        "enfrento_cbet", "folds_cbet",
        "showdowns", "suma_pct",
        "subidas_postflop", "allins", "manos_allin",
        "n_fuerza_apuesta", "suma_fuerza_apuesta",
        "n_fuerza_allin", "suma_fuerza_allin",
        "rios", "apuestas_rio",
    )

    def __init__(self):
        self.manos = 0
        self.manos_eff = 0.0
        self.vpip = 0.0
        self.pfr = 0.0
        self.three_bet_oport = 0.0
        self.three_bet = 0.0
        self.apuestas = 0.0
        self.pagos = 0.0
        self.enfrento_pre = 0.0
        self.folds_pre = 0.0
        self.enfrento_3bet = 0.0
        self.folds_3bet = 0.0
        self.enfrento_post = 0.0
        self.folds_post = 0.0
        self.cbets_oport = 0.0
        self.cbets = 0.0
        self.enfrento_cbet = 0.0
        self.folds_cbet = 0.0
        self.showdowns = 0
        self.suma_pct = 0.0
        self.subidas_postflop = 0.0
        self.allins = 0.0
        self.manos_allin = 0.0
        self.n_fuerza_apuesta = 0
        self.suma_fuerza_apuesta = 0.0
        self.n_fuerza_allin = 0
        self.suma_fuerza_allin = 0.0
        self.rios = 0.0
        self.apuestas_rio = 0.0

    def decaer(self, factor=DECAY):
        self.manos_eff *= factor
        self.vpip *= factor
        self.pfr *= factor
        self.three_bet_oport *= factor
        self.three_bet *= factor
        self.apuestas *= factor
        self.pagos *= factor
        self.enfrento_pre *= factor
        self.folds_pre *= factor
        self.enfrento_3bet *= factor
        self.folds_3bet *= factor
        self.enfrento_post *= factor
        self.folds_post *= factor
        self.cbets_oport *= factor
        self.cbets *= factor
        self.enfrento_cbet *= factor
        self.folds_cbet *= factor
        self.subidas_postflop *= factor
        self.allins *= factor
        self.manos_allin *= factor
        self.rios *= factor
        self.apuestas_rio *= factor

    @property
    def confianza(self):
        """Peso de confianza en [0, 1) segun muestra efectiva observada."""
        return self.manos_eff / (self.manos_eff + 16.0)

    @property
    def vpip_pct(self):
        return (self.vpip + 0.36 * 10.0) / (self.manos_eff + 10.0)

    @property
    def pfr_pct(self):
        return (self.pfr + 0.18 * 10.0) / (self.manos_eff + 10.0)

    @property
    def three_bet_pct(self):
        return (self.three_bet + 0.06 * 8.0) / (self.three_bet_oport + 8.0)

    @property
    def fold_pre(self):
        return (self.folds_pre + 0.55 * 6.0) / (self.enfrento_pre + 6.0)

    @property
    def fold_to_3bet(self):
        return (self.folds_3bet + 0.50 * 5.0) / (self.enfrento_3bet + 5.0)

    @property
    def fold_post(self):
        return (self.folds_post + 0.45 * 6.0) / (self.enfrento_post + 6.0)

    @property
    def fold_to_cbet(self):
        return (self.folds_cbet + 0.48 * 5.0) / (self.enfrento_cbet + 5.0)

    @property
    def cbet_pct(self):
        return (self.cbets + 0.52 * 5.0) / (self.cbets_oport + 5.0)

    @property
    def agresion(self):
        total = self.apuestas + self.pagos
        return (self.apuestas + 0.28 * 8.0) / (total + 8.0)

    @property
    def rango_showdown(self):
        if self.showdowns < 3:
            return None
        return min(1.0, 2.0 * (self.suma_pct / self.showdowns))

    @property
    def tasa_allin(self):
        return (self.manos_allin + 0.04 * 10.0) / (self.manos_eff + 10.0)

    @property
    def fuerza_al_apostar(self):
        return (self.suma_fuerza_apuesta + 0.80 * 4.0) / (self.n_fuerza_apuesta + 4.0)

    @property
    def fuerza_al_allin(self):
        return (self.suma_fuerza_allin + 0.84 * 3.0) / (self.n_fuerza_allin + 3.0)

    @property
    def multiplicador_apuesta(self):
        f = self.fuerza_al_apostar
        return max(0.30, min(1.0, 0.52 * (1.0 - f) / 0.20))

    @property
    def frecuencia_apuesta_rio(self):
        return (self.apuestas_rio + 0.30 * 6.0) / (self.rios + 6.0)

    @property
    def es_estacion(self):
        return self.enfrento_post >= 7.0 and self.fold_post < 0.31

    @property
    def es_nit(self):
        return (self.enfrento_post >= 7.0 and self.fold_post > 0.62) or \
               (self.manos_eff >= 16.0 and self.vpip_pct < 0.22)

    @property
    def es_loco(self):
        return self.manos_eff >= 12.0 and (
            self.agresion > 0.52
            or self.vpip_pct > 0.68
            or (self.n_fuerza_apuesta >= 3 and self.fuerza_al_apostar < 0.64)
            or (self.n_fuerza_allin >= 2 and self.fuerza_al_allin < 0.62)
        )


class Estadisticas:
    def __init__(self):
        self.rivales = {}

    def de(self, jugador_id):
        r = self.rivales.get(jugador_id)
        if r is None:
            r = self.rivales[jugador_id] = Rival()
        return r

    def registrar_mano(self, historial, mi_id):
        """Procesa el historial de una mano terminada con decaimiento exponencial."""
        vio, subio, actores, fue_allin = set(), set(), set(), set()
        en_rio, aposto_rio = set(), set()
        apuesta_abierta = {}
        subidas_pre = 0
        ultimo_agresor_pre = None
        flop_abierto = False

        for h in historial or []:
            jid = h.get("id_jugador")
            if jid is not None and jid != mi_id:
                actores.add(jid)

        for jid in actores:
            self.de(jid).decaer()

        for h in historial or []:
            etapa = h.get("etapa")
            tipo = (h.get("accion") or {}).get("tipo")
            jid = h.get("id_jugador")
            if jid != mi_id and jid is not None:
                r = self.de(jid)
                if etapa == "preflop":
                    if subidas_pre == 1:
                        r.three_bet_oport += 1.0
                        if tipo in AGRESIVAS:
                            r.three_bet += 1.0
                    if jid in subio and subidas_pre >= 2 and apuesta_abierta.get("preflop"):
                        r.enfrento_3bet += 1.0
                        if tipo == "fold":
                            r.folds_3bet += 1.0
                    if apuesta_abierta.get("preflop"):
                        r.enfrento_pre += 1.0
                        if tipo == "fold":
                            r.folds_pre += 1.0
                else:
                    if etapa == "flop" and not flop_abierto and jid == ultimo_agresor_pre:
                        r.cbets_oport += 1.0
                        if tipo in AGRESIVAS:
                            r.cbets += 1.0
                    if apuesta_abierta.get(etapa):
                        r.enfrento_post += 1.0
                        if tipo == "fold":
                            r.folds_post += 1.0
                        if etapa == "flop":
                            r.enfrento_cbet += 1.0
                            if tipo == "fold":
                                r.folds_cbet += 1.0
                if etapa == "river":
                    en_rio.add(jid)
                    if tipo in AGRESIVAS:
                        aposto_rio.add(jid)
                if tipo in AGRESIVAS:
                    r.apuestas += 1.0
                    if tipo == "allin":
                        r.allins += 1.0
                        fue_allin.add(jid)
                    if etapa == "preflop":
                        subio.add(jid)
                        vio.add(jid)
                    else:
                        r.subidas_postflop += 1.0
                elif tipo == "call":
                    r.pagos += 1.0
                    if etapa == "preflop":
                        vio.add(jid)
            if tipo in AGRESIVAS:
                apuesta_abierta[etapa] = True
                if etapa == "preflop":
                    subidas_pre += 1
                    ultimo_agresor_pre = jid
                elif etapa == "flop":
                    flop_abierto = True

        for jid in actores:
            r = self.de(jid)
            r.manos += 1
            r.manos_eff += 1.0
            if jid in vio:
                r.vpip += 1.0
            if jid in subio:
                r.pfr += 1.0
            if jid in fue_allin:
                r.manos_allin += 1.0
            if jid in en_rio:
                r.rios += 1.0
            if jid in aposto_rio:
                r.apuestas_rio += 1.0

    def registrar_showdown(self, mostradas, mi_id, percentil, board=None,
                           historial=None, fuerza=None):
        """Calibra rangos con showdowns evitando contaminar con BB checks gratis."""
        voluntarios = set()
        apostadores_post = set()
        apostadores_allin = set()
        for h in historial or []:
            etapa = h.get("etapa")
            tipo = (h.get("accion") or {}).get("tipo")
            jid = h.get("id_jugador")
            if etapa == "preflop" and tipo in ("call", "bet", "raise", "allin"):
                voluntarios.add(jid)
            if etapa != "preflop" and tipo in AGRESIVAS:
                apostadores_post.add(jid)
            if tipo == "allin":
                apostadores_allin.add(jid)

        for jid, cartas in (mostradas or {}).items():
            if jid == mi_id or not cartas or len(cartas) < 2:
                continue
            r = self.de(jid)
            if jid in voluntarios:
                r.showdowns += 1
                r.suma_pct += percentil(cartas)
            if fuerza is not None and board is not None and len(board) == 5:
                f = None
                if jid in apostadores_post:
                    f = fuerza(cartas, board)
                    r.n_fuerza_apuesta += 1
                    r.suma_fuerza_apuesta += f
                if jid in apostadores_allin:
                    if f is None:
                        f = fuerza(cartas, board)
                    r.n_fuerza_allin += 1
                    r.suma_fuerza_allin += f


def guardar(estadisticas, ruta):
    import json
    import os
    datos = {jid: {k: getattr(r, k) for k in Rival.__slots__}
             for jid, r in estadisticas.rivales.items()}
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f)
    os.replace(tmp, ruta)


def cargar(ruta):
    import json
    est = Estadisticas()
    try:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
    except (OSError, ValueError):
        return est
    for jid, campos in datos.items():
        r = est.de(jid)
        for k, v in campos.items():
            if k in Rival.__slots__:
                setattr(r, k, v)
    return est

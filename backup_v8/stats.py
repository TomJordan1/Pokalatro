"""Lectura de rivales: contadores por id_jugador.

El id de cada rival es estable durante TODO el torneo (son alias como
j-a1b2c3), asi que lo aprendido en la ronda 1 sigue sirviendo en la 10. Con
10 rondas de 50 manos hay tiempo de sobra para leer a un rival.

Todo son contadores enteros: cuesta microsegundos y no se cae nunca.
"""

AGRESIVAS = ("bet", "raise", "allin")


class Rival:
    __slots__ = ("manos", "vpip", "pfr", "apuestas", "pagos",
                 "enfrento_pre", "folds_pre", "enfrento_post", "folds_post",
                 "showdowns", "suma_pct", "subidas_postflop", "allins",
                 "manos_allin", "n_fuerza_apuesta", "suma_fuerza_apuesta",
                 "rios", "apuestas_rio")

    def __init__(self):
        self.manos = 0
        self.vpip = 0              # entro al pozo antes del flop
        self.pfr = 0               # subio antes del flop
        self.apuestas = 0          # bet/raise/allin en cualquier calle
        self.pagos = 0
        self.enfrento_pre = 0      # veces que enfrento una subida preflop
        self.folds_pre = 0
        self.enfrento_post = 0     # veces que enfrento una apuesta postflop
        self.folds_post = 0
        self.showdowns = 0
        self.suma_pct = 0.0        # suma de percentiles de manos mostradas
        self.subidas_postflop = 0
        self.allins = 0
        self.manos_allin = 0       # manos en que fue all-in al menos una vez
        self.rios = 0              # manos en que actuo en el river
        self.apuestas_rio = 0      # manos en que aposto o subio en el river
        self.n_fuerza_apuesta = 0  # showdowns en que habia apostado despues del flop
        self.suma_fuerza_apuesta = 0.0

    # Todas las propiedades llevan un "prior" (valor tipico) con peso, para
    # que con pocas manos observadas no saquemos conclusiones absurdas.

    @property
    def vpip_pct(self):
        """Fraccion de manos en que entra al pozo. Prior 40% con peso 10."""
        return (self.vpip + 0.40 * 10) / (self.manos + 10)

    @property
    def pfr_pct(self):
        """Fraccion de manos en que sube antes del flop. Prior 18%, peso 10."""
        return (self.pfr + 0.18 * 10) / (self.manos + 10)

    @property
    def fold_pre(self):
        """Con que frecuencia se retira ante una subida preflop. Prior 55%."""
        return (self.folds_pre + 0.55 * 6) / (self.enfrento_pre + 6)

    @property
    def fold_post(self):
        """Con que frecuencia se retira ante una apuesta postflop. Prior 45%."""
        return (self.folds_post + 0.45 * 6) / (self.enfrento_post + 6)

    @property
    def agresion(self):
        """Fraccion de sus acciones con fichas que son apuestas. Prior 28%."""
        total = self.apuestas + self.pagos
        return (self.apuestas + 0.28 * 8) / (total + 8)

    @property
    def rango_showdown(self):
        """Ancho de rango estimado por las manos que mostro, o None.

        Si muestra manos cuyo percentil promedio es p y su rango fuera
        uniforme en [0, R], entonces p = R/2, o sea R = 2p. Las manos que
        llegan al showdown son mas fuertes que su rango completo, asi que
        esto subestima el ancho: se usa como senal, no como verdad.
        """
        if self.showdowns < 3:
            return None
        return min(1.0, 2.0 * (self.suma_pct / self.showdowns))

    @property
    def tasa_allin(self):
        """Fraccion de manos en que va all-in. Prior 4% con peso 10.

        Un bot normal va all-in en pocas manos y casi siempre con algo; uno
        que lo hace en una de cada tres manos lo hace con cualquier cosa.
        """
        return (self.manos_allin + 0.04 * 10) / (self.manos + 10)

    @property
    def fuerza_al_apostar(self):
        """Fuerza promedio (contra una mano al azar, en la mesa final) de las
        manos con que aposto despues del flop y llego al showdown.

        Prior 0.80 con peso 4: lo tipico de un bot que apuesta por valor.
        """
        return (self.suma_fuerza_apuesta + 0.80 * 4) / (self.n_fuerza_apuesta + 4)

    @property
    def multiplicador_apuesta(self):
        """Cuanto se estrecha su rango cada vez que apuesta despues del flop.

        Calibrado con lo que mostro: si apuesta con manos de fuerza 0.80 (lo
        tipico), su rango se reduce a la mitad (0.52); si apuesta con manos
        de 0.65 (farolea mucho), casi no se reduce; si solo apuesta con
        monstruos, se reduce mucho mas.
        """
        f = self.fuerza_al_apostar
        return max(0.30, min(1.0, 0.52 * (1.0 - f) / 0.20))

    @property
    def frecuencia_apuesta_rio(self):
        """Fraccion de los rivers que jugo en que aposto o subio. Prior 30%.

        Ningun jugador llega con mano fuerte a la mitad de los rivers: si
        apuesta el river mucho mas seguido que eso, parte son faroles, aunque
        nunca los muestre (porque todos se retiran).
        """
        return (self.apuestas_rio + 0.30 * 6) / (self.rios + 6)

    @property
    def es_estacion(self):
        """Paga demasiado: no hay que farolearle, hay que cobrarle."""
        return self.enfrento_post >= 8 and self.fold_post < 0.30

    @property
    def es_nit(self):
        """Se retira demasiado: hay que robarle los pozos."""
        return (self.enfrento_post >= 8 and self.fold_post > 0.62) or \
               (self.manos >= 20 and self.vpip_pct < 0.22)

    @property
    def es_loco(self):
        """Apuesta con cualquier cosa: sus apuestas valen poco."""
        return self.manos >= 15 and (self.agresion > 0.52 or self.vpip_pct > 0.70)


class Estadisticas:
    def __init__(self):
        self.rivales = {}

    def de(self, jugador_id):
        r = self.rivales.get(jugador_id)
        if r is None:
            r = self.rivales[jugador_id] = Rival()
        return r

    def registrar_mano(self, historial, mi_id):
        """Procesa el historial de una mano terminada."""
        vio, subio, actores, fue_allin = set(), set(), set(), set()
        en_rio, aposto_rio = set(), set()
        apuesta_abierta = {}
        for h in historial or []:
            etapa = h.get("etapa")
            tipo = (h.get("accion") or {}).get("tipo")
            jid = h.get("id_jugador")
            if jid != mi_id and jid is not None:
                actores.add(jid)
                r = self.de(jid)
                if apuesta_abierta.get(etapa):
                    if etapa == "preflop":
                        r.enfrento_pre += 1
                        if tipo == "fold":
                            r.folds_pre += 1
                    else:
                        r.enfrento_post += 1
                        if tipo == "fold":
                            r.folds_post += 1
                if etapa == "river":
                    en_rio.add(jid)
                    if tipo in AGRESIVAS:
                        aposto_rio.add(jid)
                if tipo in AGRESIVAS:
                    r.apuestas += 1
                    if tipo == "allin":
                        r.allins += 1
                        fue_allin.add(jid)
                    if etapa == "preflop":
                        subio.add(jid)
                        vio.add(jid)
                    else:
                        r.subidas_postflop += 1
                elif tipo == "call":
                    r.pagos += 1
                    if etapa == "preflop":
                        vio.add(jid)
            if tipo in AGRESIVAS:
                apuesta_abierta[etapa] = True
        for jid in actores:
            r = self.de(jid)
            r.manos += 1
            if jid in vio:
                r.vpip += 1
            if jid in subio:
                r.pfr += 1
            if jid in fue_allin:
                r.manos_allin += 1
            if jid in en_rio:
                r.rios += 1
            if jid in aposto_rio:
                r.apuestas_rio += 1

    def registrar_showdown(self, mostradas, mi_id, percentil, board=None,
                           historial=None, fuerza=None):
        """Aprende de las cartas reveladas: es la unica informacion legitima
        sobre lo que realmente juega un rival.

        Ademas del percentil preflop, si la mesa llego al river y el rival
        habia apostado despues del flop, se mide que tan fuerte era de verdad
        la mano con que aposto. Asi se sabe si sus apuestas son de valor o
        faroles, rival por rival.
        """
        apostadores = set()
        for h in historial or []:
            if h.get("etapa") != "preflop" and (h.get("accion") or {}).get("tipo") in AGRESIVAS:
                apostadores.add(h.get("id_jugador"))
        for jid, cartas in (mostradas or {}).items():
            if jid == mi_id or not cartas or len(cartas) < 2:
                continue
            r = self.de(jid)
            r.showdowns += 1
            r.suma_pct += percentil(cartas)
            if fuerza is not None and board is not None and len(board) == 5 and jid in apostadores:
                r.n_fuerza_apuesta += 1
                r.suma_fuerza_apuesta += fuerza(cartas, board)


# --- Persistencia opcional ---------------------------------------------------------
# El id de cada rival es estable en todo el torneo. Guardar lo aprendido en un
# archivo hace que un reinicio del bot no borre la lectura de los rivales.

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

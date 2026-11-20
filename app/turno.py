"""Turno: todo lo que el bot sabe al decidir, ya masticado.

Corrige el orden posicional real cuando hay jugadores eliminados (sillas vacias)
y calcula pot odds exactas descontando excesos no igualables por nuestro stack.
"""

from motor import a_enteros, textura

AGRESIVAS = ("bet", "raise", "allin")


class Turno:
    def __init__(self, conexion, mensaje, mano, estadisticas=None):
        self.estado = mensaje.get("estado") or {}
        self.validas = mensaje.get("acciones_validas") or []
        self.timeout_ms = mensaje.get("timeout_ms", 5000) or 5000
        self.cartas = mano
        self.mano = a_enteros(mano)
        self.mi_id = conexion.id_jugador
        self.stats = estadisticas
        self.ablate = {}  # flags opcionales para ablation testing

        self.yo = {"saldo": 0, "apuesta_ronda": 0,
                   "posicion_silla": getattr(conexion, "silla", 0)}
        for j in self.estado.get("jugadores", []):
            if j.get("id") == self.mi_id:
                self.yo = j
                break
        self.comunitarias_dict = self.estado.get("comunitarias") or []
        self.board = a_enteros(self.comunitarias_dict)
        self.historial = self.estado.get("historial_acciones") or []
        self._humedad, self._emparejada = textura(self.board)

    # --- Lo que se ve en la mesa ---------------------------------------------
    @property
    def etapa(self):
        return self.estado.get("etapa", "preflop")

    @property
    def comunitarias(self):
        return self.comunitarias_dict

    @property
    def pozo(self):
        return self.estado.get("pozo", 0)

    @property
    def ciega_grande(self):
        return self.estado.get("ciega_grande", 100) or 100

    @property
    def ciega_chica(self):
        return self.estado.get("ciega_chica", 50) or 50

    @property
    def apuesta_actual(self):
        return self.estado.get("apuesta_actual", 0)

    @property
    def saldo(self):
        return self.yo.get("saldo", 0)

    @property
    def mi_apuesta(self):
        return self.yo.get("apuesta_ronda", 0)

    @property
    def humedad(self):
        return self._humedad

    @property
    def emparejada(self):
        return self._emparejada

    @property
    def en_mano(self):
        """Todos los que siguen en la mano, yo incluido."""
        return [j for j in self.estado.get("jugadores", [])
                if j.get("activo") and j.get("en_mano", True)]

    @property
    def con_cartas(self):
        """Todos los que recibieron cartas en esta mano (incluye quienes hicieron fold)."""
        return [j for j in self.estado.get("jugadores", []) if j.get("en_mano", True)]

    @property
    def rivales(self):
        return [j for j in self.en_mano if j.get("id") != self.mi_id]

    @property
    def rivales_en_mano(self):
        return len(self.rivales)

    @property
    def stack_bb(self):
        return (self.saldo + self.mi_apuesta) / float(self.ciega_grande)

    @property
    def stack_efectivo(self):
        """Lo maximo que se puede ganar o perder contra el rival activo mas grande."""
        mios = self.saldo + self.mi_apuesta
        otros = [r.get("saldo", 0) + r.get("apuesta_ronda", 0) for r in self.rivales]
        return min(mios, max(otros)) if otros else mios

    @property
    def stack_efectivo_bb(self):
        return self.stack_efectivo / float(self.ciega_grande)

    @property
    def spr(self):
        """Stack-to-Pot Ratio efectivo al inicio de la calle actual."""
        pozo_base = max(self.ciega_grande, self.pozo)
        # Stack restante efectivo (sin contar apuesta ya puesta al centro)
        mios = self.saldo
        otros = [r.get("saldo", 0) for r in self.rivales if not r.get("allin")]
        eff = min(mios, max(otros)) if otros else 0
        return eff / float(pozo_base)

    # --- Historial -------------------------------------------------------------
    def acciones_de(self, jugador_id, etapa=None):
        return [h.get("accion") or {} for h in self.historial
                if h.get("id_jugador") == jugador_id
                and (etapa is None or h.get("etapa") == etapa)]

    def _contar(self, etapa, tipos):
        return sum(1 for h in self.historial
                   if h.get("etapa") == etapa
                   and (h.get("accion") or {}).get("tipo") in tipos)

    @property
    def subidas_preflop(self):
        return self._contar("preflop", AGRESIVAS)

    @property
    def subidas_en_esta_calle(self):
        return self._contar(self.etapa, AGRESIVAS)

    @property
    def limpers(self):
        """Cuantos pagaron la ciega sin que nadie subiera."""
        if self.etapa != "preflop" or self.subidas_preflop:
            return 0
        return self._contar("preflop", ("call",))

    @property
    def fui_agresor_preflop(self):
        return any(a.get("tipo") in AGRESIVAS for a in self.acciones_de(self.mi_id, "preflop"))

    @property
    def aposte_calle_anterior(self):
        etapas = ("preflop", "flop", "turn", "river")
        try:
            anterior = etapas[etapas.index(self.etapa) - 1]
        except (ValueError, IndexError):
            return False
        return any(a.get("tipo") in AGRESIVAS for a in self.acciones_de(self.mi_id, anterior))

    @property
    def ultimo_agresor(self):
        for h in reversed(self.historial):
            if (h.get("accion") or {}).get("tipo") in AGRESIVAS:
                return h.get("id_jugador")
        return None

    # --- Posicion exacta (sin sesgo por sillas vacias / eliminados) ----------
    def _lista_ordenada(self, preflop):
        """Lista de sillas que recibieron cartas en el orden real de accion."""
        jugs = self.con_cartas
        if not jugs:
            return []
        boton = self.estado.get("posicion_boton", 0)
        total = max((j.get("posicion_silla", 0) for j in self.estado.get("jugadores", [])),
                    default=0) + 1
        total = max(total, 2)
        sillas = sorted(
            {j.get("posicion_silla", 0) for j in jugs},
            key=lambda s: (s - boton - 1) % total,
        )
        # sillas[0] es el primero a la izquierda del boton (SB en 3+, BB en HU).
        if not preflop:
            return sillas
        if len(sillas) <= 2:
            # Heads-up: boton (SB) abre preflop -> ultimo elemento de `sillas` es boton!
            if len(sillas) == 2:
                return [sillas[1], sillas[0]]
            return sillas
        # 3+: sillas[0]=SB, sillas[1]=BB, abre sillas[2], luego ... SB, BB al final.
        return sillas[2:] + sillas[:2]

    def _orden_mapa(self, preflop):
        return {s: i for i, s in enumerate(self._lista_ordenada(preflop))}

    def detras(self):
        """Cuantos rivales ACTIVOS en la mano hablan DESPUES de mi en esta ronda."""
        preflop = self.etapa == "preflop"
        mapa = self._orden_mapa(preflop)
        mi_silla = self.yo.get("posicion_silla", 0)
        mio = mapa.get(mi_silla, 0)
        return sum(1 for r in self.rivales
                   if mapa.get(r.get("posicion_silla", 0), -1) > mio)

    def rivales_detras(self):
        """Los rivales activos que hablan despues de mi en esta ronda."""
        preflop = self.etapa == "preflop"
        mapa = self._orden_mapa(preflop)
        mi_silla = self.yo.get("posicion_silla", 0)
        mio = mapa.get(mi_silla, 0)
        return [r for r in self.rivales
                if mapa.get(r.get("posicion_silla", 0), -1) > mio]

    @property
    def posicion(self):
        """0 = hablo primero postflop, 1 = hablo ultimo entre los activos en mano."""
        jugadores = self.en_mano
        if len(jugadores) <= 1:
            return 1.0
        mapa = self._orden_mapa(False)
        mio = mapa.get(self.yo.get("posicion_silla", 0), 0)
        antes = sum(1 for j in jugadores
                    if j.get("id") != self.mi_id
                    and mapa.get(j.get("posicion_silla", 0), 0) < mio)
        return antes / float(len(jugadores) - 1)

    @property
    def hablo_ultimo(self):
        return self.detras() == 0

    @property
    def en_posicion(self):
        return self.posicion >= 0.999

    @property
    def es_sb(self):
        orden = self._lista_ordenada(True)
        mi_s = self.yo.get("posicion_silla", 0)
        if len(orden) == 2:
            return mi_s == orden[0]
        return len(orden) >= 3 and mi_s == orden[-2]

    @property
    def es_bb(self):
        orden = self._lista_ordenada(True)
        mi_s = self.yo.get("posicion_silla", 0)
        return bool(orden) and mi_s == orden[-1]

    # --- Cuentas de apuestas -------------------------------------------------
    def puede(self, tipo):
        return tipo in self.validas

    @property
    def por_igualar(self):
        return max(0, self.apuesta_actual - self.mi_apuesta)

    @property
    def techo(self):
        return self.saldo + self.mi_apuesta

    @property
    def subida_minima(self):
        return self.apuesta_actual + self.estado.get("subida_minima", 0)

    @property
    def pot_odds(self):
        """Equity minima para que pagar sea rentable, descontando exceso no igualable."""
        por_ig = self.por_igualar
        if por_ig <= 0:
            return 0.0
        costo = min(por_ig, self.saldo)
        if costo <= 0:
            return 0.0
        no_igualable = por_ig - costo
        pozo_final = self.pozo - no_igualable + costo
        if pozo_final <= 0:
            return 1.0
        return costo / float(pozo_final)

    @property
    def compromiso(self):
        total = self.techo
        return min(1.0, min(self.por_igualar, self.saldo) / float(total)) if total > 0 else 1.0

    # --- Acciones ------------------------------------------------------------
    def apostar(self, total):
        total = int(total)
        tipo = "bet" if self.apuesta_actual <= self.mi_apuesta else "raise"
        if total >= self.techo and self.puede("allin"):
            return {"tipo": "allin"}
        if not self.puede(tipo):
            return self.pagar()
        total = max(total, self.subida_minima)
        if total >= self.techo or (self.techo - total) < 0.25 * max(self.pozo, self.ciega_grande):
            return {"tipo": "allin"} if self.puede("allin") else self.pagar()
        return {"tipo": tipo, "monto": total}

    def apostar_pozo(self, fraccion):
        return self.apostar(self.apuesta_actual + (self.pozo + self.por_igualar) * fraccion)

    def pagar(self):
        if self.puede("call"):
            return {"tipo": "call"}
        if self.puede("check"):
            return {"tipo": "check"}
        if self.puede("allin"):
            return {"tipo": "allin"}
        return {"tipo": "fold"}

    def todo(self):
        if self.puede("allin"):
            return {"tipo": "allin"}
        return self.pagar()

    def pasar_o_retirarse(self):
        if self.puede("check"):
            return {"tipo": "check"}
        return {"tipo": "fold"}

"""Turno: todo lo que el bot sabe al decidir, ya masticado.

Los ayudantes de apuestas son los mismos que trae el SDK de Go del kit
(pkg/botsdk), para no perder manos por un error de aritmetica con los
montos. Lo propio de este bot son la posicion exacta y la lectura del
historial.

Ojo con `monto`: en el protocolo es el TOTAL de la apuesta en la ronda, no
el incremento. `apostar()` se encarga de eso.
"""

from motor import a_enteros, textura

AGRESIVAS = ("bet", "raise", "allin")


class Turno:
    def __init__(self, conexion, mensaje, mano, estadisticas=None):
        self.estado = mensaje.get("estado") or {}
        self.validas = mensaje.get("acciones_validas") or []
        self.timeout_ms = mensaje.get("timeout_ms", 5000) or 5000
        self.cartas = mano                       # cartas del protocolo (dicts)
        self.mano = a_enteros(mano)              # las mismas, como enteros
        self.mi_id = conexion.id_jugador
        self.stats = estadisticas

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
        """0 = mesa seca, 1 = mesa muy peligrosa."""
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
        """Lo maximo que se puede ganar o perder contra el rival mas grande."""
        mios = self.saldo + self.mi_apuesta
        otros = [r.get("saldo", 0) + r.get("apuesta_ronda", 0) for r in self.rivales]
        return min(mios, max(otros)) if otros else mios

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
        """Quien hizo la ultima apuesta o subida de la mano."""
        for h in reversed(self.historial):
            if (h.get("accion") or {}).get("tipo") in AGRESIVAS:
                return h.get("id_jugador")
        return None

    # --- Posicion --------------------------------------------------------------
    # Reglas del kit (docs/reglas.md): con 2 jugadores el boton es ciega chica
    # y abre preflop, y postflop abre el otro. Con 3 o mas, preflop abre el
    # siguiente a la ciega grande y postflop el primero a la izquierda del boton.

    def _orden(self, silla, preflop):
        boton = self.estado.get("posicion_boton", 0)
        total = max((j.get("posicion_silla", 0) for j in self.estado.get("jugadores", [])),
                    default=0) + 1
        total = max(total, 2)
        if len(self.en_mano) == 2:
            desplazamiento = 0 if preflop else 1
        else:
            desplazamiento = 3 if preflop else 1
        return (silla - boton - desplazamiento) % total

    def detras(self):
        """Cuantos rivales hablan DESPUES de mi en la ronda actual."""
        preflop = self.etapa == "preflop"
        mio = self._orden(self.yo.get("posicion_silla", 0), preflop)
        return sum(1 for r in self.rivales
                   if self._orden(r.get("posicion_silla", 0), preflop) > mio)

    @property
    def posicion(self):
        """0 = hablo primero despues del flop, 1 = hablo ultimo."""
        jugadores = self.en_mano
        if len(jugadores) <= 1:
            return 1.0
        mio = self._orden(self.yo.get("posicion_silla", 0), False)
        antes = sum(1 for j in jugadores
                    if j.get("id") != self.mi_id
                    and self._orden(j.get("posicion_silla", 0), False) < mio)
        return antes / float(len(jugadores) - 1)

    @property
    def hablo_ultimo(self):
        return self.detras() == 0

    @property
    def en_posicion(self):
        """True si despues del flop hablamos al final o casi."""
        return self.posicion >= 0.999

    # --- Cuentas de apuestas ----------------------------------------------------
    def puede(self, tipo):
        return tipo in self.validas

    @property
    def por_igualar(self):
        return max(0, self.apuesta_actual - self.mi_apuesta)

    @property
    def techo(self):
        """Total maximo al que podemos llevar la apuesta: el all-in."""
        return self.saldo + self.mi_apuesta

    @property
    def subida_minima(self):
        return self.apuesta_actual + self.estado.get("subida_minima", 0)

    @property
    def pot_odds(self):
        """Equity minima para que pagar sea rentable."""
        if self.por_igualar <= 0:
            return 0.0
        return self.por_igualar / float(self.pozo + self.por_igualar)

    @property
    def compromiso(self):
        """Que fraccion de nuestras fichas cuesta pagar. 1 = todo el stack."""
        total = self.techo
        return min(1.0, self.por_igualar / float(total)) if total > 0 else 1.0

    # --- Acciones (estas funciones nunca devuelven algo invalido) ---------------
    def apostar(self, total):
        """Lleva la apuesta de la ronda a `total` fichas (total, no incremento)."""
        total = int(total)
        tipo = "bet" if self.apuesta_actual <= self.mi_apuesta else "raise"
        if total >= self.techo and self.puede("allin"):
            return {"tipo": "allin"}
        if not self.puede(tipo):
            return self.pagar()
        total = max(total, self.subida_minima)
        # Si tras subir queda menos de un cuarto del pozo, es mas claro ir all-in.
        if total >= self.techo or (self.techo - total) < 0.25 * max(self.pozo, self.ciega_grande):
            return {"tipo": "allin"} if self.puede("allin") else self.pagar()
        return {"tipo": tipo, "monto": total}

    def apostar_pozo(self, fraccion):
        """Apuesta una fraccion del pozo por encima de lo que haya que igualar."""
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
        """Nunca retirarse si pasar es gratis."""
        if self.puede("check"):
            return {"tipo": "check"}
        return {"tipo": "fold"}

    def rivales_detras(self):
        """Los rivales que todavia no hablaron en esta ronda."""
        preflop = self.etapa == "preflop"
        mio = self._orden(self.yo.get("posicion_silla", 0), preflop)
        return [r for r in self.rivales
                if self._orden(r.get("posicion_silla", 0), preflop) > mio]

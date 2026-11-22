"""Fiel replica en Python de los 8 bots del kit de PokerFight + bots rivales de estres.

Usa el evaluador rapido `fast_eval.dll` (via ctypes) para que estadistico, espia y
darwin corran en <0.2 ms por turno sin alterar su comportamiento exacto respecto al
codigo Go del kit.
"""

import ctypes
import json
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(ROOT, "app")
for _p in (ROOT, APP_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from motor import a_entero, a_enteros, categoria, evaluar

# Cargar DLL compilada para simulacion rapida
_DLL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fast_eval.dll")
_LIB = None


def _get_lib():
    global _LIB
    if _LIB is None and os.path.exists(_DLL_PATH):
        lib = ctypes.CDLL(_DLL_PATH)
        lib.eval_cards.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
        lib.eval_cards.restype = ctypes.c_int
        lib.mc_equity_random.argtypes = [
            ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
        ]
        lib.mc_equity_random.restype = ctypes.c_double
        _LIB = lib
    return _LIB


def fast_eval(cards):
    lib = _get_lib()
    if lib is None:
        return evaluar(cards)
    arr = (ctypes.c_int * len(cards))(*cards)
    return lib.eval_cards(arr, len(cards))


def fast_mc_random(c0, c1, board, n_rivals, samples, seed):
    lib = _get_lib()
    if lib is None:
        # Fallback puro Python
        rng = random.Random(seed)
        used = {c0, c1} | set(board)
        deck = [c for c in range(52) if c not in used]
        missing = 5 - len(board)
        need = missing + 2 * n_rivals
        total = 0.0
        for _ in range(samples):
            picks = rng.sample(deck, need)
            tab = list(board) + picks[:missing]
            my_s = evaluar([c0, c1] + tab)
            ties = 1
            lost = False
            for r in range(n_rivals):
                sc = evaluar([picks[missing + 2 * r], picks[missing + 2 * r + 1]] + tab)
                if sc > my_s:
                    lost = True
                    break
                if sc == my_s:
                    ties += 1
            if not lost:
                total += 1.0 / ties
        return total / samples
    arr_b = (ctypes.c_int * max(1, len(board)))(*(board if board else [0]))
    return lib.mc_equity_random(c0, c1, arr_b, len(board), n_rivals, samples, seed & 0xFFFFFFFFFFFFFFFF)


# --- Helpers compartidos (espejo de pkg/botsdk) ---------------------------------

class _MockConn:
    def __init__(self, jid, silla):
        self.id_jugador = jid
        self.silla = silla


class BotAdapter:
    """Interfaz comun para cualquier bot en el simulador."""
    nombre = "base"

    def iniciar(self, bot_id, seed=1):
        self.id = bot_id
        self.rng = random.Random(seed)

    def mano_inicio(self, estado, cartas):
        pass

    def estado_actualizado(self, estado):
        pass

    def mano_fin(self, resultado):
        pass

    def decidir(self, mensaje, cartas):
        raise NotImplementedError


def _por_igualar(estado, yo):
    return max(0, estado["apuesta_actual"] - yo["apuesta_ronda"])


def _techo(yo):
    return yo["saldo"] + yo["apuesta_ronda"]


def _subida_minima(estado):
    return estado["apuesta_actual"] + estado["subida_minima"]


def _apostar(estado, yo, validas, total):
    total = int(total)
    tipo = "bet" if estado["apuesta_actual"] <= yo["apuesta_ronda"] else "raise"
    techo = _techo(yo)
    if total >= techo and "allin" in validas:
        return {"tipo": "allin"}
    if tipo not in validas:
        return _pagar(validas)
    total = max(total, _subida_minima(estado))
    if total >= techo:
        return {"tipo": "allin"} if "allin" in validas else _pagar(validas)
    return {"tipo": tipo, "monto": total}


def _apostar_pozo(estado, yo, validas, fraccion):
    por_ig = _por_igualar(estado, yo)
    return _apostar(estado, yo, validas, estado["apuesta_actual"] + (estado["pozo"] + por_ig) * fraccion)


def _pagar(validas):
    if "call" in validas:
        return {"tipo": "call"}
    if "check" in validas:
        return {"tipo": "check"}
    if "allin" in validas:
        return {"tipo": "allin"}
    return {"tipo": "fold"}


def _pasar_o_fold(validas):
    if "check" in validas:
        return {"tipo": "check"}
    return {"tipo": "fold"}


def _yo(estado, my_id):
    for j in estado["jugadores"]:
        if j["id"] == my_id:
            return j
    return estado["jugadores"][0]


# --- 1. Aleatorio ---------------------------------------------------------------

class BotAleatorio(BotAdapter):
    nombre = "aleatorio"

    def decidir(self, mensaje, cartas):
        validas = mensaje["acciones_validas"]
        estado = mensaje["estado"]
        yo = _yo(estado, self.id)
        tipo = self.rng.choice(validas)
        if tipo in ("bet", "raise"):
            lo, hi = _subida_minima(estado), _techo(yo)
            if hi <= lo:
                return _apostar(estado, yo, validas, hi)
            return _apostar(estado, yo, validas, self.rng.randint(lo, hi))
        return {"tipo": tipo}


# --- 2. Conservador -------------------------------------------------------------

class BotConservador(BotAdapter):
    nombre = "conservador"

    def decidir(self, mensaje, cartas):
        validas = mensaje["acciones_validas"]
        estado = mensaje["estado"]
        yo = _yo(estado, self.id)
        if "check" in validas:
            return {"tipo": "check"}
        if "call" in validas and _por_igualar(estado, yo) <= estado["ciega_grande"] * 3:
            return {"tipo": "call"}
        return {"tipo": "fold"}


# --- 3. Base (Chen + categoria hecha) -------------------------------------------

def _chen(cartas):
    r0, p0 = cartas[0]["rango"], cartas[0]["palo"]
    r1, p1 = cartas[1]["rango"], cartas[1]["palo"]
    alta, baja = (r0, r1) if r0 >= r1 else (r1, r0)
    val = {14: 10.0, 13: 8.0, 12: 7.0, 11: 6.0}.get(alta, alta / 2.0)
    if alta == baja:
        val = max(5.0, val * 2.0)
    if p0 == p1:
        val += 2.0
    hueco = alta - baja - 1
    if hueco == 1:
        val -= 1.0
    elif hueco == 2:
        val -= 2.0
    elif hueco == 3:
        val -= 4.0
    elif hueco >= 4:
        val -= 5.0
    if alta != baja and hueco <= 1 and alta < 12:
        val += 1.0
    return val


class BotBase(BotAdapter):
    nombre = "base"

    def decidir(self, mensaje, cartas):
        estado = mensaje["estado"]
        validas = mensaje["acciones_validas"]
        yo = _yo(estado, self.id)
        com = estado["comunitarias"]
        if len(com) < 3:
            pts = _chen(cartas)
            cg = estado["ciega_grande"]
            if pts >= 9.0:
                return _apostar(estado, yo, validas, cg * 3)
            if pts >= 5.0:
                if "check" in validas:
                    return {"tipo": "check"}
                if "call" in validas and _por_igualar(estado, yo) <= cg * 3:
                    return {"tipo": "call"}
                return {"tipo": "fold"}
            return _pasar_o_fold(validas)

        cat = categoria(fast_eval(a_enteros(cartas + com)))
        if cat >= 3:
            if "bet" in validas or "raise" in validas:
                return _apostar_pozo(estado, yo, validas, 0.75)
            return _pagar(validas) if "call" in validas else _pasar_o_fold(validas)
        if cat >= 1:
            if "check" in validas and "bet" in validas:
                return _apostar_pozo(estado, yo, validas, 0.4)
            if "call" in validas and _por_igualar(estado, yo) * 2 <= estado["pozo"]:
                return {"tipo": "call"}
            return _pasar_o_fold(validas)
        return _pasar_o_fold(validas)


# --- 4. Novato (puerto directo de bots/novato) ----------------------------------

def _novato_grupo(mano):
    alta = max(mano[0]["rango"], mano[1]["rango"])
    baja = min(mano[0]["rango"], mano[1]["rango"])
    mp = mano[0]["palo"] == mano[1]["palo"]
    if alta == baja and alta >= 11:
        return "premium"
    if alta == 14 and baja == 13:
        return "premium"
    if alta == baja and alta >= 8:
        return "fuerte"
    if alta == 14 and baja >= 11:
        return "fuerte"
    if alta == 13 and baja == 12 and mp:
        return "fuerte"
    if alta == baja:
        return "jugable"
    if alta == 14 and (mp or baja >= 10):
        return "jugable"
    if alta >= 11 and baja >= 10:
        return "jugable"
    if mp and alta - baja == 1 and baja >= 5:
        return "especulativa"
    return "basura"


def _novato_leer(mano, com):
    mia = categoria(fast_eval(a_enteros(mano + com)))
    mesa_cat = categoria(fast_eval(a_enteros(com))) if len(com) >= 5 else 0
    if len(com) < 5:
        rc = {}
        for c in com:
            rc[c["rango"]] = rc.get(c["rango"], 0) + 1
        reps = sorted(rc.values(), reverse=True) + [0]
        if reps[0] == 3:
            mesa_cat = 3
        elif reps[0] == 2 and reps[1] == 2:
            mesa_cat = 2
        elif reps[0] == 2:
            mesa_cat = 1
        else:
            mesa_cat = 0

    mis_r = [c["rango"] for c in mano]
    mesa_r = [c["rango"] for c in com]
    max_mesa = max(mesa_r)
    par_mano = mis_r[0] == mis_r[1]

    if mia >= 3 and mia > mesa_cat:
        nivel = "monstruo"
    elif mia == 2 and mesa_cat == 0:
        nivel = "buena"
    elif par_mano and mis_r[0] > max_mesa:
        nivel = "buena"
    elif max_mesa in mis_r:
        nivel = "buena"
    elif par_mano or any(r in mesa_r for r in mis_r):
        nivel = "media"
    else:
        nivel = "nada"

    outs = 0
    if len(com) < 5 and nivel != "monstruo":
        for p in range(4):
            en_tot = sum(1 for c in mano + com if c["palo"] == p)
            mias = sum(1 for c in mano if c["palo"] == p)
            if en_tot == 4 and mias >= 1:
                outs += 9
                break
        rangos = {c["rango"] for c in mano + com}
        mios = {c["rango"] for c in mano}
        if 14 in rangos:
            rangos.add(1)
        if 14 in mios:
            mios.add(1)
        st_type = ""
        for bajo in range(1, 11):
            win = [bajo + i for i in range(5)]
            pres = [r for r in win if r in rangos]
            if len(pres) != 4 or not any(r in mios for r in pres):
                continue
            falta = [r for r in win if r not in rangos][0]
            if falta in (win[0], win[4]) and pres[0] - 1 >= 1 and pres[-1] + 1 <= 14:
                st_type = "abierta"
                break
            st_type = "interna"
        if st_type == "abierta":
            outs += 8
        elif st_type == "interna":
            outs += 4
    return nivel, min(outs, 15)


class BotNovato(BotAdapter):
    nombre = "novato"

    def decidir(self, mensaje, cartas):
        estado = mensaje["estado"]
        validas = mensaje["acciones_validas"]
        yo = _yo(estado, self.id)
        com = estado["comunitarias"]
        cg = estado["ciega_grande"]
        por_ig = _por_igualar(estado, yo)

        # orden desde boton para hablo_ultimo
        boton = estado["posicion_boton"]
        total = max((j["posicion_silla"] for j in estado["jugadores"]), default=0) + 1
        en_mano = [j for j in estado["jugadores"] if j["activo"] and j["en_mano"]]
        rivales = len([j for j in en_mano if j["id"] != self.id])

        def _ord(silla):
            desp = (0 if len(en_mano) == 2 else 3) if len(com) < 3 else 1
            return (silla - boton - desp) % max(total, 2)

        mio = _ord(yo["posicion_silla"])
        hablo_ultimo = all(_ord(j["posicion_silla"]) <= mio for j in en_mano if j["id"] != self.id)

        if len(com) < 3:
            grupo = _novato_grupo(cartas)
            nadie_subio = estado["apuesta_actual"] <= cg
            if grupo == "premium":
                if nadie_subio:
                    return _apostar(estado, yo, validas, 3 * cg)
                if estado["apuesta_actual"] * 3 >= _techo(yo):
                    return {"tipo": "allin"} if "allin" in validas else _pagar(validas)
                return _apostar(estado, yo, validas, 3 * estado["apuesta_actual"])
            if grupo == "fuerte":
                if nadie_subio:
                    return _apostar(estado, yo, validas, 3 * cg)
                if por_ig <= 4 * cg:
                    return _pagar(validas)
                return _pasar_o_fold(validas)
            if grupo == "jugable":
                if nadie_subio and hablo_ultimo:
                    return _apostar(estado, yo, validas, 3 * cg)
                if por_ig <= cg:
                    return _pagar(validas)
                return _pasar_o_fold(validas)
            if grupo == "especulativa":
                if por_ig <= cg and rivales >= 2:
                    return _pagar(validas)
                return _pasar_o_fold(validas)
            return _pasar_o_fold(validas)

        nivel, outs = _novato_leer(cartas, com)
        muchos = rivales >= 3
        if nivel == "monstruo":
            if por_ig == 0:
                return _apostar_pozo(estado, yo, validas, 0.75)
            if "raise" in validas:
                return _apostar(estado, yo, validas, estado["apuesta_actual"] * 3)
            return _pagar(validas)
        if nivel == "buena":
            if por_ig == 0:
                return _apostar_pozo(estado, yo, validas, 0.33 if muchos else 0.5)
            lim = 0.5 if muchos else 0.75
            if por_ig <= estado["pozo"] * lim:
                return _pagar(validas)
            return _pasar_o_fold(validas)
        if outs > 0:
            prob = outs * (4 if 5 - len(com) == 2 else 2)
            if por_ig == 0:
                if hablo_ultimo and rivales == 1:
                    return _apostar_pozo(estado, yo, validas, 0.5)
                return _pagar(validas)
            pct_pagar = 100.0 * por_ig / (estado["pozo"] + por_ig) if por_ig > 0 else 0.0
            if prob >= pct_pagar:
                return _pagar(validas)
            return _pasar_o_fold(validas)
        if nivel == "media":
            if por_ig == 0:
                return _pagar(validas)
            if por_ig <= estado["pozo"] / 3.0 and not muchos:
                return _pagar(validas)
            return _pasar_o_fold(validas)
        subidas_calle = sum(
            1 for h in estado["historial_acciones"]
            if h["etapa"] == estado["etapa"] and h["accion"]["tipo"] in ("bet", "raise", "allin")
        )
        if por_ig == 0 and estado["etapa"] == "river" and rivales == 1 and hablo_ultimo and subidas_calle == 0:
            return _apostar_pozo(estado, yo, validas, 0.5)
        return _pasar_o_fold(validas)


# --- 5. Estadistico -------------------------------------------------------------

class BotEstadistico(BotAdapter):
    nombre = "estadistico"

    def __init__(self, muestras=1200):
        self.muestras = muestras  # en Go son 10000; 1200 tiene error <0.9%

    def decidir(self, mensaje, cartas):
        estado = mensaje["estado"]
        validas = mensaje["acciones_validas"]
        yo = _yo(estado, self.id)
        rivales = sum(1 for j in estado["jugadores"] if j["id"] != self.id and j["activo"] and j["en_mano"])
        c0, c1 = a_enteros(cartas)
        board = a_enteros(estado["comunitarias"])
        eq = fast_mc_random(c0, c1, board, rivales, self.muestras, self.rng.getrandbits(64))

        parte_justa = 1.0 / (rivales + 1) if rivales > 0 else 1.0
        ventaja = (eq - parte_justa) / (1.0 - parte_justa) if rivales > 0 else 1.0
        subidas = sum(1 for h in estado["historial_acciones"]
                      if h["etapa"] == estado["etapa"] and h["accion"]["tipo"] in ("bet", "raise", "allin"))
        umbral = 0.25 + 0.15 * subidas

        def _supera(val, u):
            if val >= u + 0.05:
                return True
            if val <= u - 0.05:
                return False
            return self.rng.random() < (val - (u - 0.05)) / 0.10

        puede_subir = any(x in validas for x in ("bet", "raise", "allin"))
        if puede_subir and _supera(ventaja, umbral):
            av = max(0.0, min(1.0, (ventaja - umbral) / max(1e-9, 1.0 - umbral)))
            return _apostar_pozo(estado, yo, validas, 0.5 + 0.5 * av)

        por_ig = _por_igualar(estado, yo)
        if por_ig == 0:
            if len(board) >= 3 and rivales <= 1 and "bet" in validas and self.rng.random() < 0.08:
                return _apostar_pozo(estado, yo, validas, 0.5)
            if "check" in validas:
                return {"tipo": "check"}

        costo = min(por_ig, yo["saldo"])
        no_ig = por_ig - costo
        pozo_fin = estado["pozo"] - no_ig + costo
        precio = costo / pozo_fin if (costo > 0 and pozo_fin > 0) else (0.0 if costo <= 0 else 1.0)
        if _supera(eq, precio + 0.04):
            return _pagar(validas)
        return _pasar_o_fold(validas)


# --- 6. Bandido (4-arm UCB1 + olvido) -------------------------------------------

_ESTILOS_BANDIDO = [
    {"nombre": "roca", "entrar": 0.47, "abrir": 0.57, "valor": 0.66, "farol": 0.00, "margen": 0.25},
    {"nombre": "tiburon", "entrar": 0.38, "abrir": 0.50, "valor": 0.58, "farol": 0.12, "margen": 0.18},
    {"nombre": "maniaco", "entrar": 0.28, "abrir": 0.40, "valor": 0.42, "farol": 0.35, "margen": 0.10},
    {"nombre": "pagador", "entrar": 0.24, "abrir": 0.70, "valor": 0.75, "farol": 0.00, "margen": 0.02},
]


class BotBandido(BotAdapter):
    nombre = "bandido"

    def iniciar(self, bot_id, seed=1):
        super().iniciar(bot_id, seed)
        self.jugadas = [0.0] * 4
        self.recompensas = [0.0] * 4
        self.brazo = 0
        self.decidio = False
        self.saldo_inicial = 0
        self.saldo_actual = 0
        self.cg = 100

    def mano_inicio(self, estado, cartas):
        # UCB1 elegir
        chosen = None
        total = 0.0
        for i, n in enumerate(self.jugadas):
            if n < 0.5:
                chosen = i
                break
            total += n
        if chosen is None:
            best, best_p = 0, -1e9
            for i, n in enumerate(self.jugadas):
                p = self.recompensas[i] / n + 0.2 * math.sqrt(math.log(total) / n)
                if p > best_p:
                    best, best_p = i, p
            chosen = best
        self.brazo = chosen
        self.decidio = False

    def mano_fin(self, resultado):
        if not self.decidio or self.cg <= 0:
            return
        ganado = sum(r["monto"] for r in resultado.get("repartos", []) if r["id_jugador"] == self.id)
        neto = ganado - (self.saldo_inicial - self.saldo_actual)
        ciegas = max(-20.0, min(20.0, neto / float(self.cg)))
        recomp = (ciegas + 20.0) / 40.0
        for i in range(4):
            self.jugadas[i] *= 0.997
            self.recompensas[i] *= 0.997
        self.jugadas[self.brazo] += 1.0
        self.recompensas[self.brazo] += recomp

    def decidir(self, mensaje, cartas):
        estado = mensaje["estado"]
        validas = mensaje["acciones_validas"]
        yo = _yo(estado, self.id)
        if not self.decidio:
            self.saldo_inicial = yo["saldo"] + yo["apuesta_ronda"]
            self.cg = estado["ciega_grande"]
            self.decidio = True

        e = _ESTILOS_BANDIDO[self.brazo]
        com = estado["comunitarias"]
        if len(com) < 3:
            f = max(0.0, min(1.0, (_chen(cartas) + 1.0) / 21.0))
            hubo_sub = estado["apuesta_actual"] > self.cg
            if f >= e["abrir"] + 0.20:
                acc = _apostar(estado, yo, validas, max(3 * self.cg, 3 * estado["apuesta_actual"]))
            elif f >= e["abrir"] and not hubo_sub:
                acc = _apostar(estado, yo, validas, 3 * self.cg)
            elif not hubo_sub and self.rng.random() < e["farol"] / 2.0:
                acc = _apostar(estado, yo, validas, 3 * self.cg)
            elif f >= e["entrar"] and _por_igualar(estado, yo) <= 4 * self.cg:
                acc = _pagar(validas)
            else:
                acc = _pasar_o_fold(validas)
        else:
            rivales = max(1, sum(1 for j in estado["jugadores"] if j["id"] != self.id and j["activo"] and j["en_mano"]))
            f = self._fuerza_postflop(cartas, com, rivales)
            por_ig = _por_igualar(estado, yo)
            if por_ig == 0:
                if f >= e["valor"]:
                    acc = _apostar_pozo(estado, yo, validas, 0.66)
                elif self.rng.random() < e["farol"]:
                    acc = _apostar_pozo(estado, yo, validas, 0.5)
                else:
                    acc = _pasar_o_fold(validas)
            else:
                precio = por_ig / float(estado["pozo"] + por_ig) if (estado["pozo"] + por_ig) > 0 else 1.0
                if f >= 0.85:
                    acc = _apostar_pozo(estado, yo, validas, 1.0)
                elif f >= precio + e["margen"]:
                    acc = _pagar(validas)
                else:
                    acc = _pasar_o_fold(validas)

        t = acc["tipo"]
        if t == "call":
            self.saldo_actual = yo["saldo"] - min(_por_igualar(estado, yo), yo["saldo"])
        elif t in ("bet", "raise"):
            self.saldo_actual = yo["saldo"] - (acc["monto"] - yo["apuesta_ronda"])
        elif t == "allin":
            self.saldo_actual = 0
        else:
            self.saldo_actual = yo["saldo"]
        return acc

    def _fuerza_postflop(self, mano, mesa, rivales):
        en_mesa = {}
        mas_alta = 0
        for c in mesa:
            r = c["rango"]
            en_mesa[r] = en_mesa.get(r, 0) + 1
            mas_alta = max(mas_alta, r)
        a, b = mano[0]["rango"], mano[1]["rango"]
        par_mano = a == b
        liga = en_mesa.get(a, 0) > 0 or en_mesa.get(b, 0) > 0
        punt = fast_eval(a_enteros(mano + mesa))
        cat = categoria(punt)
        if cat == 0:
            f = 0.18 if max(a, b) >= 13 else 0.10
        elif cat == 1:
            if par_mano and a > mas_alta:
                f = 0.68
            elif par_mano:
                f = 0.42
            elif (en_mesa.get(a, 0) > 0 and a == mas_alta) or (en_mesa.get(b, 0) > 0 and b == mas_alta):
                f = 0.60
            elif liga:
                f = 0.38
            else:
                f = 0.15
        elif cat == 2:
            if not par_mano and en_mesa.get(a, 0) > 0 and en_mesa.get(b, 0) > 0:
                f = 0.78
            elif liga or par_mano:
                f = 0.55
            else:
                f = 0.18
        elif cat == 3:
            f = 0.86 if (liga or par_mano) else 0.25
        elif cat == 4:
            f = 0.90
        elif cat == 5:
            f = 0.93
        elif cat == 6:
            f = 0.96
        else:
            f = 0.99

        if len(mesa) == 5:
            if fast_eval(a_enteros(mesa)) == punt:
                f = min(f, 0.30)
        else:
            por_palo = {}
            for c in mesa + mano:
                por_palo[c["palo"]] = por_palo.get(c["palo"], 0) + 1
            if por_palo.get(mano[0]["palo"], 0) == 4 or por_palo.get(mano[1]["palo"], 0) == 4:
                f += 0.12
        f -= 0.04 * (rivales - 1)
        return max(0.0, min(1.0, f))


# --- 7. Espia (modelado bayesiano + MC) -----------------------------------------

class BotEspia(BotAdapter):
    nombre = "espia"

    def iniciar(self, bot_id, seed=1):
        super().iniciar(bot_id, seed)
        self.perfiles = {}
        self.id_mano = None
        self.historial = []
        self.en_mano = []

    def _p(self, jid):
        p = self.perfiles.get(jid)
        if p is None:
            p = {"manos": 0, "entro": 0, "agr": 0, "pas": 0, "enf": 0, "ret": 0}
            self.perfiles[jid] = p
        return p

    def _entra(self, jid):
        p = self._p(jid)
        return (p["entro"] + 2.0) / (p["manos"] + 6.0)

    def _agr(self, jid):
        p = self._p(jid)
        return (p["agr"] + 3.5) / (p["agr"] + p["pas"] + 10.0)

    def _se_retira(self, jid):
        p = self._p(jid)
        return (p["ret"] + 2.0) / (p["enf"] + 4.0)

    def _recordar(self, estado):
        if estado["id_mano"] != self.id_mano:
            self.id_mano = estado["id_mano"]
            self.historial = []
        if len(estado["historial_acciones"]) >= len(self.historial):
            self.historial = estado["historial_acciones"]
        self.en_mano = [j["id"] for j in estado["jugadores"] if j["en_mano"]]

    def mano_inicio(self, estado, cartas):
        self._recordar(estado)

    def estado_actualizado(self, estado):
        self._recordar(estado)

    def mano_fin(self, resultado):
        for jid in self.en_mano:
            if jid != self.id:
                self._p(jid)["manos"] += 1
        ya = set()
        ap = {}
        for h in self.historial:
            t = h["accion"]["tipo"]
            jid = h["id_jugador"]
            et = h["etapa"]
            if jid != self.id:
                p = self._p(jid)
                if t in ("bet", "raise", "allin"):
                    p["agr"] += 1
                elif t == "call":
                    p["pas"] += 1
                if et == "preflop" and t in ("bet", "raise", "allin", "call") and jid not in ya:
                    ya.add(jid)
                    p["entro"] += 1
                if et != "preflop" and ap.get(et):
                    p["enf"] += 1
                    if t == "fold":
                        p["ret"] += 1
            if t in ("bet", "raise", "allin"):
                ap[et] = True
        self.historial = []

    def decidir(self, mensaje, cartas):
        estado = mensaje["estado"]
        validas = mensaje["acciones_validas"]
        yo = _yo(estado, self.id)
        self._recordar(estado)
        rivales = [j["id"] for j in estado["jugadores"] if j["id"] != self.id and j["activo"] and j["en_mano"]]
        c0, c1 = a_enteros(cartas)
        board = a_enteros(estado["comunitarias"])
        if len(board) < 3:
            eq = fast_mc_random(c0, c1, [], 1, 400, self.rng.getrandbits(64))
            cg = estado["ciega_grande"]
            hubo_sub = estado["apuesta_actual"] > cg
            exig = 0.02 * (len(rivales) - 1)
            todos_poco = len(rivales) > 0 and all(
                self._p(r)["manos"] >= 15 and self._entra(r) < 0.22 for r in rivales
            )
            if eq >= 0.70:
                return _apostar(estado, yo, validas, max(3 * cg, 3 * estado["apuesta_actual"]))
            if eq >= 0.58 + exig and not hubo_sub:
                return _apostar(estado, yo, validas, 3 * cg)
            if not hubo_sub and eq >= 0.42 and todos_poco:
                return _apostar(estado, yo, validas, 3 * cg)
            if eq >= 0.50 + exig and _por_igualar(estado, yo) <= 4 * cg:
                return _pagar(validas)
            return _pasar_o_fold(validas)

        sim = min(len(rivales), 3)
        eq = fast_mc_random(c0, c1, board, sim, 400, self.rng.getrandbits(64))
        parte_justa = 1.0 / (sim + 1)
        por_ig = _por_igualar(estado, yo)
        if por_ig == 0:
            se_ret = 1.0
            for r in rivales:
                se_ret *= self._se_retira(r)
            if eq >= parte_justa + 0.22:
                return _apostar_pozo(estado, yo, validas, 0.9 if se_ret < 0.30 else 0.6)
            if eq >= parte_justa + 0.10 and se_ret < 0.35:
                return _apostar_pozo(estado, yo, validas, 0.5)
            if se_ret > 0.55 and self.rng.random() < 0.7:
                return _apostar_pozo(estado, yo, validas, 0.5)
            return _pasar_o_fold(validas)

        ult = ""
        for h in reversed(estado["historial_acciones"]):
            if h["etapa"] != estado["etapa"]:
                break
            if h["id_jugador"] != self.id and h["accion"]["tipo"] in ("bet", "raise", "allin"):
                ult = h["id_jugador"]
                break
        agr = self._agr(ult)
        creible = 0.10 - 0.4 * (agr - 0.35)
        precio = por_ig / float(estado["pozo"] + por_ig) if (estado["pozo"] + por_ig) > 0 else 1.0
        if eq >= parte_justa + 0.35:
            return _apostar_pozo(estado, yo, validas, 1.0)
        if eq - creible >= precio + 0.03:
            return _pagar(validas)
        return _pasar_o_fold(validas)


# --- 8. Darwin (genoma.json + preflop.json exactos del kit) ---------------------

_DARWIN_GENOMA = {
    "aversion_riesgo": 0.0125,
    "bono_posicion": 0.0302,
    "farol_post": 0.1795,
    "farol_pre": 0.2814,
    "farol_subida": 0.0066,
    "margen_pago_post": 1.1816,
    "margen_pago_pre": 0.695,
    "multijugador": 0.3453,
    "presion": 0.1357,
    "tam_apertura": 2.8084,
    "tam_farol": 0.7933,
    "tam_subida": 0.9561,
    "tam_valor": 0.9259,
    "umbral_allin": 0.8926,
    "umbral_apuesta_post": 0.6803,
    "umbral_apuesta_pre": 0.59,
    "umbral_subida_post": 0.6523,
    "umbral_subida_pre": 0.6513,
}

# Preflop table de Darwin (cargada una vez o calculada desde tabla_preflop)
_DARWIN_PREFLOP = None


def _cargar_darwin_preflop():
    global _DARWIN_PREFLOP
    if _DARWIN_PREFLOP is not None:
        return _DARWIN_PREFLOP
    # Intentar leer del scratch si existe, si no usar tabla_preflop.EQUITY[0]
    ruta = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tools", "darwin_preflop.json"
    )
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8") as f:
            _DARWIN_PREFLOP = json.load(f)
            return _DARWIN_PREFLOP
    from tabla_preflop import EQUITY
    letras = "--23456789TJQKA"
    tbl = {}
    for (hi, lo, mp), (eq1, _) in EQUITY.items():
        if hi == lo:
            tbl[letras[hi] + letras[lo]] = eq1
        elif mp:
            tbl[letras[hi] + letras[lo] + "s"] = eq1
        else:
            tbl[letras[hi] + letras[lo] + "o"] = eq1
    _DARWIN_PREFLOP = tbl
    return _DARWIN_PREFLOP


class BotDarwin(BotAdapter):
    nombre = "darwin"

    def decidir(self, mensaje, cartas):
        estado = mensaje["estado"]
        validas = mensaje["acciones_validas"]
        yo = _yo(estado, self.id)
        com = estado["comunitarias"]
        cg = float(max(estado["ciega_grande"], 1))
        g = _DARWIN_GENOMA

        if len(com) < 3:
            r0, p0 = cartas[0]["rango"], cartas[0]["palo"]
            r1, p1 = cartas[1]["rango"], cartas[1]["palo"]
            if r1 > r0:
                r0, r1, p0, p1 = r1, r0, p1, p0
            letras = "--23456789TJQKA"
            if r0 == r1:
                cl = letras[r0] + letras[r1]
            elif p0 == p1:
                cl = letras[r0] + letras[r1] + "s"
            else:
                cl = letras[r0] + letras[r1] + "o"
            fuerza = _cargar_darwin_preflop().get(cl, 0.5)
            calle = 0
        else:
            c0, c1 = a_enteros(cartas)
            fuerza = fast_mc_random(c0, c1, a_enteros(com), 1, 1500, self.rng.getrandbits(64))
            calle = 3 if len(com) >= 5 else (2 if len(com) == 4 else 1)

        sillas = []
        max_s = 0
        rivales = 0
        for j in estado["jugadores"]:
            max_s = max(max_s, j["posicion_silla"])
            if j["activo"] and j["en_mano"]:
                sillas.append(j["posicion_silla"])
                if j["id"] != self.id:
                    rivales += 1
        total_s = max_s + 1
        boton = estado["posicion_boton"]
        if len(sillas) <= 1 or total_s <= 0:
            pos = 1.0
        else:
            def dist(s):
                return (s - boton - 1) % total_s
            mia = dist(yo["posicion_silla"])
            antes = sum(1 for s in sillas if s != yo["posicion_silla"] and dist(s) < mia)
            pos = antes / float(len(sillas) - 1)

        subidas = sum(1 for h in estado["historial_acciones"]
                      if h["etapa"] == estado["etapa"] and h["accion"]["tipo"] in ("bet", "raise", "allin"))
        pozo = estado["pozo"] / cg
        por_ig = _por_igualar(estado, yo) / cg
        ap_act = estado["apuesta_actual"] / cg
        techo = _techo(yo) / cg
        puede_sub = "bet" in validas or "raise" in validas

        f = pow(fuerza, 1.0 + (max(rivales, 1) - 1) * g["multijugador"])
        f += g["bono_posicion"] * (2.0 * pos - 1.0)
        f -= g["presion"] * subidas

        if calle == 0:
            u_ap, u_sub, m_pag, farol = g["umbral_apuesta_pre"], g["umbral_subida_pre"], g["margen_pago_pre"], g["farol_pre"]
        else:
            u_ap, u_sub, m_pag, farol = g["umbral_apuesta_post"], g["umbral_subida_post"], g["margen_pago_post"], g["farol_post"]

        def riesgo(tot):
            mi_ap = ap_act - por_ig
            rest = techo - mi_ap
            if rest <= 0:
                return 1.0
            return min(max((tot - mi_ap) / rest, 0.0), 1.0)

        if f >= g["umbral_allin"] + g["aversion_riesgo"]:
            return {"tipo": "allin"} if "allin" in validas else _apostar(estado, yo, validas, _techo(yo))

        if por_ig <= 0:
            tot = max(g["tam_apertura"], ap_act + g["tam_apertura"] - 1) if calle == 0 else ap_act + g["tam_valor"] * pozo
            if f >= u_ap + g["aversion_riesgo"] * riesgo(tot) and puede_sub:
                return _apostar(estado, yo, validas, round(tot * cg))
            if puede_sub and self.rng.random() < farol * (0.5 + pos):
                tf = max(g["tam_apertura"], ap_act + g["tam_apertura"] - 1) if calle == 0 else ap_act + g["tam_farol"] * pozo
                return _apostar(estado, yo, validas, round(tf * cg))
            return _pasar_o_fold(validas)

        pot_odds = por_ig / (pozo + por_ig)
        sub_tot = g["tam_apertura"] if (calle == 0 and ap_act <= 1) else ap_act + g["tam_subida"] * (pozo + por_ig)
        if f >= u_sub + g["aversion_riesgo"] * riesgo(sub_tot) and puede_sub:
            return _apostar(estado, yo, validas, round(sub_tot * cg))
        if f >= pot_odds * m_pag + g["aversion_riesgo"] * riesgo(ap_act):
            return _pagar(validas)
        if puede_sub and self.rng.random() < g["farol_subida"]:
            return _apostar(estado, yo, validas, round(sub_tot * cg))
        return _pasar_o_fold(validas)


# --- Bots adicionales de estres (Explotador de patrones + Cambiante) -----------

class BotExplotador(BotAdapter):
    """Bot adverso que mide fold-to-cbet / fold-to-steal y ataca frecuencias extremas."""
    nombre = "explotador"

    def iniciar(self, bot_id, seed=1):
        super().iniciar(bot_id, seed)
        self.stats = {}
        self.id_mano = None
        self.historial = []

    def _s(self, jid):
        if jid not in self.stats:
            self.stats[jid] = {"oport": 0, "folds": 0, "bets": 0, "calls": 0}
        return self.stats[jid]

    def mano_inicio(self, estado, cartas):
        self.id_mano = estado["id_mano"]
        self.historial = []

    def estado_actualizado(self, estado):
        if len(estado["historial_acciones"]) >= len(self.historial):
            self.historial = estado["historial_acciones"]

    def mano_fin(self, resultado):
        ap = {}
        for h in self.historial:
            jid = h["id_jugador"]
            et = h["etapa"]
            t = h["accion"]["tipo"]
            if jid != self.id:
                s = self._s(jid)
                if ap.get(et):
                    s["oport"] += 1
                    if t == "fold":
                        s["folds"] += 1
                if t in ("bet", "raise", "allin"):
                    s["bets"] += 1
                elif t == "call":
                    s["calls"] += 1
            if t in ("bet", "raise", "allin"):
                ap[et] = True

    def decidir(self, mensaje, cartas):
        estado = mensaje["estado"]
        validas = mensaje["acciones_validas"]
        yo = _yo(estado, self.id)
        com = estado["comunitarias"]
        cg = estado["ciega_grande"]
        rivales = [j["id"] for j in estado["jugadores"] if j["id"] != self.id and j["activo"] and j["en_mano"]]
        c0, c1 = a_enteros(cartas)
        eq = fast_mc_random(c0, c1, a_enteros(com), max(1, len(rivales)), 400, self.rng.getrandbits(64))
        por_ig = _por_igualar(estado, yo)

        # Estimar fold promedio de rivales activos
        folds = []
        for r in rivales:
            s = self._s(r)
            folds.append((s["folds"] + 2.0) / (s["oport"] + 4.0))
        fold_prod = 1.0
        for f in folds:
            fold_prod *= f

        if por_ig == 0:
            # Ataca agresivamente a quienes foldean >52%, pero nunca regala vs estaciones
            if eq >= (0.65 if len(com) < 3 else 0.35 + 0.30 / max(1, len(rivales))):
                return _apostar(estado, yo, validas, 3 * cg) if len(com) < 3 else _apostar_pozo(estado, yo, validas, 0.70)
            if fold_prod > 0.45 and self.rng.random() < 0.55 and ("bet" in validas or "raise" in validas):
                return _apostar(estado, yo, validas, 2.5 * cg) if len(com) < 3 else _apostar_pozo(estado, yo, validas, 0.60)
            return _pasar_o_fold(validas)

        precio = por_ig / float(estado["pozo"] + por_ig) if (estado["pozo"] + por_ig) > 0 else 1.0
        if eq >= max(0.68, precio + 0.25) and ("raise" in validas):
            return _apostar_pozo(estado, yo, validas, 0.85)
        if eq >= precio + 0.05:
            return _pagar(validas)
        return _pasar_o_fold(validas)


class BotCambiante(BotAdapter):
    """Bot que cambia de estrategia agresiva (nit <-> maniaco) cada 15 manos."""
    nombre = "cambiante"

    def iniciar(self, bot_id, seed=1):
        super().iniciar(bot_id, seed)
        self.mano_n = 0

    def mano_inicio(self, estado, cartas):
        self.mano_n += 1

    def decidir(self, mensaje, cartas):
        estado = mensaje["estado"]
        validas = mensaje["acciones_validas"]
        yo = _yo(estado, self.id)
        fase = (self.mano_n // 15) % 2
        c0, c1 = a_enteros(cartas)
        rivales = max(1, sum(1 for j in estado["jugadores"] if j["id"] != self.id and j["activo"] and j["en_mano"]))
        eq = fast_mc_random(c0, c1, a_enteros(estado["comunitarias"]), rivales, 300, self.rng.getrandbits(64))
        por_ig = _por_igualar(estado, yo)

        if fase == 0:
            # Nit extremo
            if por_ig == 0:
                if eq >= 0.65 / (0.6 + 0.4 * rivales) and ("bet" in validas or "raise" in validas):
                    return _apostar_pozo(estado, yo, validas, 0.75) if estado["comunitarias"] else _apostar(estado, yo, validas, 3 * estado["ciega_grande"])
                return _pasar_o_fold(validas)
            precio = por_ig / float(estado["pozo"] + por_ig)
            if eq >= precio + 0.14:
                return _pagar(validas)
            return _pasar_o_fold(validas)
        else:
            # Maniaco
            if por_ig == 0:
                if (eq >= 0.40 / max(1, rivales) or self.rng.random() < 0.45) and ("bet" in validas or "raise" in validas):
                    return _apostar_pozo(estado, yo, validas, 0.8) if estado["comunitarias"] else _apostar(estado, yo, validas, 3.5 * estado["ciega_grande"])
                return _pasar_o_fold(validas)
            precio = por_ig / float(estado["pozo"] + por_ig)
            if eq >= precio - 0.02:
                return _pagar(validas)
            return _pasar_o_fold(validas)


KIT_BOTS = {
    "aleatorio": BotAleatorio,
    "conservador": BotConservador,
    "novato": BotNovato,
    "base": BotBase,
    "estadistico": BotEstadistico,
    "bandido": BotBandido,
    "espia": BotEspia,
    "darwin": BotDarwin,
}

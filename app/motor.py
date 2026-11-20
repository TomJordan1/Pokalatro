"""Motor: evaluador de manos, percentiles preflop, puntajes en mesa y equity Monte Carlo.

Python puro, sin dependencias externas. Una carta es un entero 0..51:

    carta = (rango - 2) * 4 + palo        rango 2..14, palo 0..3 (c d h s)

Incluye en v9:
  - Correccion del bug de proyectos de escalera comunitarios en `puntajes_en_mesa`.
  - Precalculo de mano propia y valores de combos en river (`faltan == 0`), con
    enumeracion exacta cuando hay 1 rival (0 varianza, ~1-2 ms).
  - Soporte nativo para rangos ponderados bayesianos (`RangoPonderado` / pares
    (combo, peso)) mediante muestreo estratificado por CDF (`bisect`).
  - Eliminacion del fallback que ampliaba rangos ultra-estrechos al top 15%.
"""

import bisect
import random
import time

from tabla_preflop import EQUITY, ORDEN

# --- Tablas del evaluador (se arman al importar, ~10 ms) ---------------------

STRAIGHT = [-1] * 8192
TOP5 = [0] * 8192


def _precomputar():
    for m in range(8192):
        for alto in range(12, 3, -1):
            ventana = 0x1F << (alto - 4)
            if m & ventana == ventana:
                STRAIGHT[m] = alto
                break
        else:
            if m & 0x100F == 0x100F:
                STRAIGHT[m] = 3
        valor, n = 0, 0
        for r in range(12, -1, -1):
            if m >> r & 1:
                valor = valor * 16 + r
                n += 1
                if n == 5:
                    break
        TOP5[m] = valor


_precomputar()


def _top_n(mascara, n):
    valor = 0
    for r in range(12, -1, -1):
        if n == 0:
            break
        if mascara >> r & 1:
            valor = valor * 16 + r
            n -= 1
    while n > 0:
        valor *= 16
        n -= 1
    return valor


def evaluar(cartas):
    """Evalua 5, 6 o 7 cartas (enteros). Mayor puntaje = mejor mano."""
    cnt = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    sc = [0, 0, 0, 0]
    sm = [0, 0, 0, 0]
    mascara = 0
    for c in cartas:
        r = c >> 2
        s = c & 3
        cnt[r] += 1
        sc[s] += 1
        sm[s] |= 1 << r
        mascara |= 1 << r

    for s in range(4):
        if sc[s] >= 5:
            m = sm[s]
            st = STRAIGHT[m]
            if st >= 0:
                return (8 << 20) | st
            return (5 << 20) | TOP5[m]

    cuatro = -1
    t0 = -1
    t1 = -1
    p0 = -1
    p1 = -1
    for r in range(12, -1, -1):
        k = cnt[r]
        if k == 4:
            cuatro = r
        elif k == 3:
            if t0 < 0:
                t0 = r
            elif t1 < 0:
                t1 = r
        elif k == 2:
            if p0 < 0:
                p0 = r
            elif p1 < 0:
                p1 = r

    if cuatro >= 0:
        return (7 << 20) | (cuatro << 4) | _top_n(mascara & ~(1 << cuatro), 1)
    if t0 >= 0 and (t1 >= 0 or p0 >= 0):
        p = t1 if (t1 >= 0 and (p0 < 0 or t1 > p0)) else p0
        return (6 << 20) | (t0 << 4) | p
    st = STRAIGHT[mascara]
    if st >= 0:
        return (4 << 20) | st
    if t0 >= 0:
        return (3 << 20) | (t0 << 8) | _top_n(mascara & ~(1 << t0), 2)
    if p1 >= 0:
        resto = mascara & ~(1 << p0) & ~(1 << p1)
        return (2 << 20) | (p0 << 8) | (p1 << 4) | _top_n(resto, 1)
    if p0 >= 0:
        return (1 << 20) | (p0 << 12) | _top_n(mascara & ~(1 << p0), 3)
    return TOP5[mascara]


CARTA_ALTA, PAR, DOBLE_PAR, TRIO, ESCALERA, COLOR, FULL, POKER, ESCALERA_COLOR = range(9)


def categoria(puntaje):
    return puntaje >> 20


def a_entero(carta):
    return (carta["rango"] - 2) * 4 + carta["palo"]


def a_enteros(cartas):
    return [a_entero(c) for c in cartas]


# --- Combinaciones y percentiles ---------------------------------------------

def _clase(c1, c2):
    r1, r2 = (c1 >> 2) + 2, (c2 >> 2) + 2
    return (max(r1, r2), min(r1, r2), (c1 & 3) == (c2 & 3) and r1 != r2)


def _combos_de_clase(clave):
    alto, bajo, mp = clave
    salida = []
    if alto == bajo:
        cartas = [(alto - 2) * 4 + p for p in range(4)]
        for i in range(4):
            for j in range(i + 1, 4):
                salida.append((cartas[i], cartas[j]))
    elif mp:
        for p in range(4):
            salida.append(((alto - 2) * 4 + p, (bajo - 2) * 4 + p))
    else:
        for pa in range(4):
            for pb in range(4):
                if pa != pb:
                    salida.append(((alto - 2) * 4 + pa, (bajo - 2) * 4 + pb))
    return salida


def _construir():
    combos = []
    percentil = {}
    total = 1326
    for clave in ORDEN:
        percentil[clave] = len(combos) / total
        combos.extend(_combos_de_clase(clave))
    return combos, percentil


_COMBOS, _PCT_CLASE = _construir()
N_COMBOS = len(_COMBOS)

# Todos los 1326 pares canonicos (a < b) para indexado rapido
TODOS_COMBOS = []
COMBO_A_IDX = {}
for _a in range(52):
    for _b in range(_a + 1, 52):
        COMBO_A_IDX[(_a, _b)] = len(TODOS_COMBOS)
        COMBO_A_IDX[(_b, _a)] = len(TODOS_COMBOS)
        TODOS_COMBOS.append((_a, _b))


def percentil_mano(c1, c2):
    return _PCT_CLASE[_clase(c1, c2)]


def equity_inicial(c1, c2):
    return EQUITY[_clase(c1, c2)]


def combos_top(fraccion):
    return _COMBOS[:max(6, int(N_COMBOS * fraccion))]


# --- Textura de la mesa ------------------------------------------------------

def textura(board):
    if len(board) < 3:
        return 0.0, False
    palos = [0, 0, 0, 0]
    rangos = []
    for c in board:
        palos[c & 3] += 1
        rangos.append(c >> 2)
    unicos = sorted(set(rangos))
    emparejada = len(unicos) < len(board)

    humedad = 0.0
    max_palo = max(palos)
    if max_palo >= 4:
        humedad += 0.5
    elif max_palo == 3:
        humedad += 0.35
    mejor = 0
    for base in range(0, 13):
        cuantas = sum(1 for r in unicos if base <= r < base + 5)
        mejor = max(mejor, cuantas)
    if mejor >= 4:
        humedad += 0.4
    elif mejor == 3:
        humedad += 0.25
    if max(unicos) >= 10:
        humedad += 0.1
    return min(1.0, humedad), emparejada


# --- Puntajes en la mesa (con correccion de proyectos propios) ---------------

def _puntajes_en_mesa(board):
    """Fuerza (0..1) y valor bruto de cada combinacion posible en `board`."""
    board_set = set(board)
    libres = [c for c in range(52) if c not in board_set]
    tablero = list(board)
    combos = []
    valores = []
    for i in range(len(libres)):
        a = libres[i]
        for j in range(i + 1, len(libres)):
            b = libres[j]
            v = evaluar([a, b] + tablero)
            combos.append((a, b))
            valores.append(v)
    ordenados = sorted(valores)
    total = len(ordenados)
    hay_cartas = len(board) < 5
    palos_mesa = [0, 0, 0, 0]
    mascara_mesa = 0
    for c in board:
        palos_mesa[c & 3] += 1
        mascara_mesa |= 1 << (c >> 2)

    puntajes = {}
    valores_map = {}
    for (a, b), v in zip(combos, valores):
        p = (bisect.bisect_left(ordenados, v) + bisect.bisect_right(ordenados, v)) / (2.0 * total)
        valores_map[(a, b)] = v
        valores_map[(b, a)] = v
        if hay_cartas and p < 0.85:
            bonus = 0.0
            for s in {a & 3, b & 3}:
                if palos_mesa[s] + ((a & 3) == s) + ((b & 3) == s) == 4:
                    bonus = 0.22
            ra, rb = a >> 2, b >> 2
            mascara = mascara_mesa | (1 << ra) | (1 << rb)
            if STRAIGHT[mascara] < 0 and (((mascara_mesa >> ra) & 1 == 0) or ((mascara_mesa >> rb) & 1 == 0)):
                salidas = 0
                for r in range(13):
                    if not (mascara >> r & 1):
                        st_all = STRAIGHT[mascara | (1 << r)]
                        if st_all >= 0 and st_all > STRAIGHT[mascara_mesa | (1 << r)]:
                            salidas += 1
                if salidas >= 2:
                    bonus = max(bonus, 0.15)
                elif salidas == 1:
                    bonus = max(bonus, 0.06)
            p = min(0.97, p + bonus)
        puntajes[(a, b)] = p
        puntajes[(b, a)] = p
    return puntajes, valores_map


_CACHE_MESA = {"clave": None, "puntajes": None, "valores": None}


def _info_mesa(board):
    clave = tuple(sorted(board))
    if _CACHE_MESA["clave"] != clave:
        _CACHE_MESA["clave"] = clave
        _CACHE_MESA["puntajes"], _CACHE_MESA["valores"] = _puntajes_en_mesa(board)
    return _CACHE_MESA["puntajes"], _CACHE_MESA["valores"]


def puntajes_en_mesa(board):
    return _info_mesa(board)[0]


def valores_en_mesa(board):
    return _info_mesa(board)[1]


# --- Estructuras de rango (v8 compatible + RangoPonderado bayesiano v9) ------

class Rango:
    __slots__ = ("ancho", "conserva", "techo", "farol")

    def __init__(self, ancho, conserva=1.0, techo=0.0, farol=0.15):
        self.ancho = ancho
        self.conserva = conserva
        self.techo = techo
        self.farol = farol

    def __float__(self):
        return float(self.ancho * self.conserva)


class RangoPonderado:
    """Distribucion bayesiana continua sobre combinaciones validas (combo, peso)."""
    __slots__ = ("combos", "pesos", "cdf", "total", "farol_est", "farol", "techo")

    def __init__(self, combos, pesos, farol_est=0.15, techo=0.0):
        self.combos = combos
        self.pesos = pesos
        self.farol_est = farol_est
        self.farol = farol_est
        self.techo = techo
        cdf = []
        acc = 0.0
        for w in pesos:
            acc += w
            cdf.append(acc)
        self.cdf = cdf
        self.total = acc


def combos_de_rango(rango, board, vistas, rng=random):
    if isinstance(rango, RangoPonderado):
        return [ab for ab, w in zip(rango.combos, rango.pesos)
                if w > 0.0 and ab[0] not in vistas and ab[1] not in vistas]
    if not isinstance(rango, Rango):
        rango = Rango(float(rango))
    base = [ab for ab in combos_top(rango.ancho) if ab[0] not in vistas and ab[1] not in vistas]
    if len(board) < 3 or (rango.conserva >= 0.999 and rango.techo <= 0.0) or len(base) < 6:
        return base
    puntajes = puntajes_en_mesa(board)
    ordenados = sorted(base, key=lambda ab: puntajes.get(ab, 0.0), reverse=True)
    n = len(ordenados)
    inicio = int(rango.techo * n)
    resto = ordenados[inicio:]
    quedan = max(4, int(round(rango.conserva * len(resto))))
    n_valor = int(quedan * (1.0 - rango.farol))
    valor = resto[:n_valor]
    otros = resto[n_valor:]
    faroles = quedan - n_valor
    if faroles > 0 and otros:
        paso = max(1, len(otros) // faroles)
        valor += otros[::paso][:faroles]
    return valor or base


# --- Equity exacta (river HU) y Monte Carlo optimizado -----------------------

def equity(mano, comunitarias, rangos, presupuesto_s=0.15, max_muestras=6000,
           min_muestras=350, rng=random):
    """Probabilidad de ganar el showdown contra los `rangos` rivales."""
    mano = list(mano)
    base = list(comunitarias)
    vistas = set(mano)
    vistas.update(base)
    faltan = 5 - len(base)
    if not rangos:
        return 1.0

    # Caso exacto: River (faltan == 0) con 1 rival -> 0 varianza, ~1-2 ms!
    if faltan == 0:
        tablero = base
        mio = evaluar(mano + tablero)
        valores_map = valores_en_mesa(tablero)
        if len(rangos) == 1:
            r0 = rangos[0]
            gana = 0.0
            tot_w = 0.0
            if isinstance(r0, RangoPonderado):
                for (a, b), w in zip(r0.combos, r0.pesos):
                    if w <= 0.0 or a in vistas or b in vistas:
                        continue
                    v = valores_map.get((a, b))
                    if v is None:
                        v = evaluar([a, b] + tablero)
                    tot_w += w
                    if mio > v:
                        gana += w
                    elif mio == v:
                        gana += 0.5 * w
            else:
                lista = combos_de_rango(r0, base, vistas, rng)
                for a, b in lista:
                    if a in vistas or b in vistas:
                        continue
                    v = valores_map.get((a, b))
                    if v is None:
                        v = evaluar([a, b] + tablero)
                    tot_w += 1.0
                    if mio > v:
                        gana += 1.0
                    elif mio == v:
                        gana += 0.5
            if tot_w > 0.0:
                return gana / tot_w
            return 0.5

    # Preparar muestreadores para cada rival (ponderado o uniforme)
    samplers = []  # lista de (combos, cdf_o_none, total_w)
    for rango in rangos:
        if isinstance(rango, RangoPonderado):
            combos = []
            cdf = []
            acc = 0.0
            for ab, w in zip(rango.combos, rango.pesos):
                if w > 0.0 and ab[0] not in vistas and ab[1] not in vistas:
                    combos.append(ab)
                    acc += w
                    cdf.append(acc)
            if not combos:
                combos = [ab for ab in _COMBOS if ab[0] not in vistas and ab[1] not in vistas][:60]
                samplers.append((combos, None, 0.0))
            else:
                samplers.append((combos, cdf, acc))
        else:
            lista = combos_de_rango(rango, base, vistas, rng)
            if not lista:
                lista = [ab for ab in _COMBOS if ab[0] not in vistas and ab[1] not in vistas][:60]
            samplers.append((lista, None, 0.0))

    choice = rng.choice
    randf = rng.random
    randrange = rng.randrange
    bisect_left = bisect.bisect_left
    reloj = time.perf_counter
    inicio = reloj()
    victorias = 0.0
    n = 0
    fallos = 0

    mio_fijo = evaluar(mano + base) if faltan == 0 else 0
    valores_map = valores_en_mesa(base) if faltan == 0 else None

    while n < max_muestras:
        if n >= min_muestras and (n & 63) == 0 and reloj() - inicio > presupuesto_s:
            break
        bloqueadas = set(vistas)
        manos_riv = []
        completo = True
        for combos, cdf, tot_w in samplers:
            if cdf is None:
                for _ in range(12):
                    a, b = choice(combos)
                    if a not in bloqueadas and b not in bloqueadas:
                        bloqueadas.add(a)
                        bloqueadas.add(b)
                        manos_riv.append((a, b))
                        break
                else:
                    completo = False
                    break
            else:
                for _ in range(14):
                    idx = bisect_left(cdf, randf() * tot_w)
                    if idx >= len(combos):
                        idx = len(combos) - 1
                    a, b = combos[idx]
                    if a not in bloqueadas and b not in bloqueadas:
                        bloqueadas.add(a)
                        bloqueadas.add(b)
                        manos_riv.append((a, b))
                        break
                else:
                    completo = False
                    break
        if not completo:
            fallos += 1
            if fallos > 300:
                break
            continue

        if faltan == 0:
            mio = mio_fijo
            empatados = 1
            gano = True
            for a, b in manos_riv:
                suyo = valores_map.get((a, b))
                if suyo is None:
                    suyo = evaluar([a, b] + base)
                if suyo > mio:
                    gano = False
                    break
                if suyo == mio:
                    empatados += 1
        else:
            tablero = base[:]
            puestas = 0
            while puestas < faltan:
                c = randrange(52)
                if c not in bloqueadas:
                    bloqueadas.add(c)
                    tablero.append(c)
                    puestas += 1

            mio = evaluar(mano + tablero)
            empatados = 1
            gano = True
            for a, b in manos_riv:
                suyo = evaluar([a, b] + tablero)
                if suyo > mio:
                    gano = False
                    break
                if suyo == mio:
                    empatados += 1

        if gano:
            victorias += 1.0 / empatados
        n += 1

    return victorias / n if n else 0.5


def categoria_propia(mano, board):
    total = categoria(evaluar(list(mano) + list(board)))
    cuenta = {}
    for c in board:
        cuenta[c >> 2] = cuenta.get(c >> 2, 0) + 1
    pares_mesa = sum(1 for k in cuenta.values() if k >= 2)
    trio_mesa = any(k >= 3 for k in cuenta.values())
    if total == DOBLE_PAR:
        return PAR if pares_mesa == 1 else CARTA_ALTA if pares_mesa >= 2 else total
    if total == PAR and pares_mesa >= 1:
        return CARTA_ALTA
    if total == TRIO and trio_mesa:
        return CARTA_ALTA
    return total


def fuerza_vs_azar(mano, board):
    usadas = set(mano) | set(board)
    libres = [c for c in range(52) if c not in usadas]
    tablero = list(board)
    mio = evaluar(list(mano) + tablero)
    gana = 0.0
    total = 0
    for i in range(len(libres)):
        a = libres[i]
        for j in range(i + 1, len(libres)):
            v = evaluar([a, libres[j]] + tablero)
            if v < mio:
                gana += 1.0
            elif v == mio:
                gana += 0.5
            total += 1
    return gana / total if total else 0.5

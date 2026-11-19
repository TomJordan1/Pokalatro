"""Motor: evaluador de manos, percentiles preflop y equity Monte Carlo.

Python puro, sin dependencias. Una carta es un entero 0..51:

    carta = (rango - 2) * 4 + palo        rango 2..14, palo 0..3 (c d h s)

`evaluar` devuelve un entero: mayor = mejor mano. Tarda ~2 us, asi que se
pueden simular miles de manos dentro del plazo de 5 segundos de la mesa.
"""

import random
import time

from tabla_preflop import EQUITY, ORDEN

# --- Tablas del evaluador (se arman al importar, ~10 ms) ------------------------

STRAIGHT = [-1] * 8192   # mascara de rangos -> rango alto de la escalera, o -1
TOP5 = [0] * 8192        # mascara de rangos -> los 5 rangos mas altos en base 16


def _precomputar():
    for m in range(8192):
        for alto in range(12, 3, -1):
            ventana = 0x1F << (alto - 4)
            if m & ventana == ventana:
                STRAIGHT[m] = alto
                break
        else:
            if m & 0x100F == 0x100F:      # rueda A-2-3-4-5: el as vale 1
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
    cnt = [0] * 13
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
                return (8 << 20) | st          # escalera de color
            return (5 << 20) | TOP5[m]         # color

    cuatro = -1
    trios = []
    pares = []
    for r in range(12, -1, -1):
        k = cnt[r]
        if k == 4:
            cuatro = r
        elif k == 3:
            trios.append(r)
        elif k == 2:
            pares.append(r)

    if cuatro >= 0:
        return (7 << 20) | (cuatro << 4) | _top_n(mascara & ~(1 << cuatro), 1)
    if trios and (len(trios) > 1 or pares):
        t = trios[0]
        if len(trios) > 1:
            p = trios[1] if not pares or trios[1] > pares[0] else pares[0]
        else:
            p = pares[0]
        return (6 << 20) | (t << 4) | p        # full house
    st = STRAIGHT[mascara]
    if st >= 0:
        return (4 << 20) | st
    if trios:
        t = trios[0]
        return (3 << 20) | (t << 8) | _top_n(mascara & ~(1 << t), 2)
    if len(pares) >= 2:
        p1, p2 = pares[0], pares[1]
        resto = mascara & ~(1 << p1) & ~(1 << p2)
        return (2 << 20) | (p1 << 8) | (p2 << 4) | _top_n(resto, 1)
    if pares:
        p = pares[0]
        return (1 << 20) | (p << 12) | _top_n(mascara & ~(1 << p), 3)
    return TOP5[mascara]


CARTA_ALTA, PAR, DOBLE_PAR, TRIO, ESCALERA, COLOR, FULL, POKER, ESCALERA_COLOR = range(9)


def categoria(puntaje):
    return puntaje >> 20


# --- Cartas del protocolo <-> enteros -------------------------------------------

def a_entero(carta):
    return (carta["rango"] - 2) * 4 + carta["palo"]


def a_enteros(cartas):
    return [a_entero(c) for c in cartas]


# --- Percentiles preflop (desde la tabla generada) ------------------------------

def _clase(c1, c2):
    r1, r2 = (c1 >> 2) + 2, (c2 >> 2) + 2
    return (max(r1, r2), min(r1, r2), (c1 & 3) == (c2 & 3) and r1 != r2)


def _combos_de_clase(clave):
    """Todas las combinaciones concretas de una clase de mano."""
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
    """Arma el orden de combinaciones y el percentil de cada clase.

    El percentil de una clase es la fraccion de manos ESTRICTAMENTE mejores
    que ella (ponderada por cantidad de combinaciones, que es lo correcto:
    hay 6 formas de tener AA y 12 de tener AKo). 0.0 = la mejor.
    """
    combos = []
    percentil = {}
    total = 1326
    for clave in ORDEN:
        percentil[clave] = len(combos) / total
        combos.extend(_combos_de_clase(clave))
    return combos, percentil


_COMBOS, _PCT_CLASE = _construir()
N_COMBOS = len(_COMBOS)


def percentil_mano(c1, c2):
    """0.0 = la mejor mano inicial, 1.0 = la peor."""
    return _PCT_CLASE[_clase(c1, c2)]


def equity_inicial(c1, c2):
    """(eq1, eq4): equity contra 1 y contra 4 manos al azar."""
    return EQUITY[_clase(c1, c2)]


def combos_top(fraccion):
    """Las combinaciones del mejor `fraccion` de manos."""
    return _COMBOS[:max(8, int(N_COMBOS * fraccion))]


# --- Textura de la mesa ----------------------------------------------------------

def textura(board):
    """(humedad, emparejada): cuanto se mueve la mesa y si tiene pareja.

    humedad 0 = mesa seca (arcoiris, sin conexion), 1 = muy peligrosa
    (tres del mismo palo o muy conectada). Sirve para elegir el tamano de
    la apuesta: en mesa humeda hay que cobrar mas y proteger.
    """
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
    # Conectividad: la mayor cantidad de cartas distintas dentro de 5 rangos.
    mejor = 0
    for base in range(0, 13):
        cuantas = sum(1 for r in unicos if base <= r < base + 5)
        mejor = max(mejor, cuantas)
    if mejor >= 4:
        humedad += 0.4
    elif mejor == 3:
        humedad += 0.25
    if max(unicos) >= 10:            # cartas altas: mas manos las conectan
        humedad += 0.1
    return min(1.0, humedad), emparejada


# --- Equity por Monte Carlo contra rangos ----------------------------------------

def equity(mano, comunitarias, rangos, presupuesto_s=0.35, max_muestras=20000,
           min_muestras=400, rng=random):
    """Probabilidad de ganar el showdown (los empates valen su fraccion).

    mano: 2 enteros. comunitarias: 0, 3, 4 o 5 enteros.
    rangos: un elemento por rival: un numero (fraccion de manos iniciales,
            1.0 = cualquier mano) o un Rango (ver mas abajo), que ademas se
            estrecha por la fuerza de cada mano en esta mesa.

    Reparte cartas al azar respetando los rangos y cuenta cuantas veces
    ganamos. Corta por tiempo (`presupuesto_s`) para no arriesgar el plazo.
    """
    mano = list(mano)
    base = list(comunitarias)
    vistas = set(mano)
    vistas.update(base)
    faltan = 5 - len(base)
    if not rangos:
        return 1.0

    # Para cada rival, sus combinaciones posibles (sin las cartas ya vistas).
    permitidos = []
    for rango in rangos:
        lista = combos_de_rango(rango, base, vistas, rng)
        if len(lista) < 4:                           # rango imposible: lo abrimos
            lista = [ab for ab in _COMBOS
                     if ab[0] not in vistas and ab[1] not in vistas][:200]
        permitidos.append(lista)

    choice = rng.choice
    randrange = rng.randrange
    reloj = time.perf_counter
    inicio = reloj()
    victorias = 0.0
    n = 0
    fallos = 0

    while n < max_muestras:
        if n >= min_muestras and (n & 63) == 0 and reloj() - inicio > presupuesto_s:
            break
        bloqueadas = set(vistas)
        manos_riv = []
        completo = True
        for combos in permitidos:
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
        if not completo:
            fallos += 1
            if fallos > 300:
                break
            continue

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


# --- Rangos estrechados por la fuerza en ESTA mesa -------------------------------
#
# Quien paga tres calles y resube el river no tiene "una de las mejores manos
# iniciales": tiene una mano fuerte en esta mesa en particular. QT no es una
# mano inicial premium, pero en J-J-A-K es escalera. Por eso, despues del flop,
# el rango de cada rival se filtra por la fuerza de cada combinacion en las
# cartas comunitarias reales, no por su percentil preflop.

class Rango:
    """Lo que puede tener un rival.

    ancho:    fraccion de manos iniciales con que llego al flop (percentil preflop).
    conserva: fraccion de esas manos que siguen siendo coherentes con sus
              apuestas y pagos despues del flop (1.0 = no se estrecha).
    techo:    fraccion de las MEJORES manos que se descartan porque paso
              cuando podia apostar (un bot que pasa casi nunca tiene un monstruo).
    farol:    parte del rango estrecho que son faroles o proyectos (manos que
              no son fuertes en la mesa pero igual apostaron).
    """
    __slots__ = ("ancho", "conserva", "techo", "farol")

    def __init__(self, ancho, conserva=1.0, techo=0.0, farol=0.15):
        self.ancho = ancho
        self.conserva = conserva
        self.techo = techo
        self.farol = farol

    def __float__(self):
        return float(self.ancho * self.conserva)


def _puntajes_en_mesa(board, vistas):
    """Fuerza (0..1) de cada combinacion posible en esta mesa.

    Percentil de la mano hecha entre todas las combinaciones, mas un premio
    por proyectos de color o escalera si quedan cartas por venir: un bot que
    paga con un proyecto tambien "tiene algo" para seguir.
    """
    libres = [c for c in range(52) if c not in board]
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
    import bisect
    hay_cartas = len(board) < 5
    palos_mesa = [0, 0, 0, 0]
    mascara_mesa = 0
    for c in board:
        palos_mesa[c & 3] += 1
        mascara_mesa |= 1 << (c >> 2)
    puntajes = {}
    for (a, b), v in zip(combos, valores):
        p = (bisect.bisect_left(ordenados, v) + bisect.bisect_right(ordenados, v)) / (2.0 * total)
        if hay_cartas and p < 0.85:
            bonus = 0.0
            # Proyecto de color: 4 del mismo palo usando al menos una carta propia.
            for s in {a & 3, b & 3}:
                if palos_mesa[s] + ((a & 3) == s) + ((b & 3) == s) == 4:
                    bonus = 0.22
            # Proyecto de escalera: cuantos rangos distintos la completarian.
            mascara = mascara_mesa | (1 << (a >> 2)) | (1 << (b >> 2))
            if STRAIGHT[mascara] < 0:
                salidas = sum(1 for r in range(13)
                              if not mascara >> r & 1 and STRAIGHT[mascara | (1 << r)] >= 0)
                if salidas >= 2:
                    bonus = max(bonus, 0.15)
                elif salidas == 1:
                    bonus = max(bonus, 0.06)
            p = min(0.97, p + bonus)
        puntajes[(a, b)] = p
    return puntajes


_CACHE_MESA = {"clave": None, "puntajes": None}


def puntajes_en_mesa(board):
    clave = tuple(sorted(board))
    if _CACHE_MESA["clave"] != clave:
        _CACHE_MESA["clave"] = clave
        _CACHE_MESA["puntajes"] = _puntajes_en_mesa(board, set(board))
    return _CACHE_MESA["puntajes"]


def combos_de_rango(rango, board, vistas, rng=random):
    """Las combinaciones concretas que puede tener un rival."""
    if not isinstance(rango, Rango):
        rango = Rango(float(rango))
    base = [ab for ab in combos_top(rango.ancho) if ab[0] not in vistas and ab[1] not in vistas]
    if len(board) < 3 or (rango.conserva >= 0.999 and rango.techo <= 0.0) or len(base) < 8:
        return base
    puntajes = puntajes_en_mesa(board)
    ordenados = sorted(base, key=lambda ab: puntajes.get(ab, puntajes.get((ab[1], ab[0]), 0.0)),
                       reverse=True)
    n = len(ordenados)
    inicio = int(rango.techo * n)
    resto = ordenados[inicio:]
    quedan = max(6, int(round(rango.conserva * len(resto))))
    n_valor = int(quedan * (1.0 - rango.farol))
    valor = resto[:n_valor]
    otros = resto[n_valor:]
    faroles = quedan - n_valor
    if faroles > 0 and otros:
        # Faroles repartidos de forma pareja entre las manos que no son fuertes.
        paso = max(1, len(otros) // faroles)
        valor += otros[::paso][:faroles]
    return valor or base


def categoria_propia(mano, board):
    """Categoria de la jugada, sin contar lo que pone la mesa sola.

    Con 6-6 en la mesa y un 2 en la mano, evaluar() dice "doble par", pero
    nuestra contribucion es una pareja de 2: cualquiera con una carta mas
    alta nos gana. Esta funcion descuenta las parejas y trios de la mesa.
    """
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
    """Fraccion de manos rivales posibles a las que `mano` le gana en `board`
    (5 cartas). Enumeracion exacta de las ~990 combinaciones: unos 5 ms.

    Sirve para aprender de los showdowns: si un rival aposto y mostro una
    mano que solo le ganaba al 60% de las manos, sus apuestas valen menos.
    """
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


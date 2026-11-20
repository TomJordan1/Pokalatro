"""Abstraccion estrategica de manos, texturas y situaciones para Pokalatro v9.

Distingue explicitamente:
  1. Ranking preflop realista (multiway + jugabilidad suited/conectada, sin el
     sesgo 1v1 all-in de tabla_preflop.ORDEN).
  2. Fuerza hecha real relativa al tablero (`made_tier` 0..6), descontando
     cartas comunitarias repetidas.
  3. Potencial de proyecto real (`draw_tier` 0..3), exigiendo contribucion de
     al menos una carta propia (corrige el bug de proyectos comunitarios de v8).
  4. Geometria del pozo (SPR), textura de mesa y mapeo de acciones.
"""

from blueprint_data import HAND_ORDER
from motor import (
    CARTA_ALTA, COLOR, DOBLE_PAR, ESCALERA, ESCALERA_COLOR, FULL, PAR, POKER,
    STRAIGHT, TRIO, categoria, categoria_propia, evaluar,
)
from tabla_preflop import EQUITY

# --- 1. Ranking de jugabilidad preflop (sincronizado con blueprint_data) -----

def _construir_ranking_preflop():
    claves = list(HAND_ORDER)
    combos = []
    pct = {}
    rank_idx = {}
    total = 1326.0
    for idx, cl in enumerate(claves):
        rank_idx[cl] = idx
        pct[cl] = len(combos) / total
        alto, bajo, mp = cl
        if alto == bajo:
            cs = [(alto - 2) * 4 + p for p in range(4)]
            for i in range(4):
                for j in range(i + 1, 4):
                    combos.append((cs[i], cs[j]))
        elif mp:
            for p in range(4):
                combos.append(((alto - 2) * 4 + p, (bajo - 2) * 4 + p))
        else:
            for pa in range(4):
                for pb in range(4):
                    if pa != pb:
                        combos.append(((alto - 2) * 4 + pa, (bajo - 2) * 4 + pb))
    return claves, combos, pct, rank_idx


CLAVES_ORDENADAS, COMBOS_ORDENADOS, _PCT_JUGABILIDAD, _INDICE_CLASE = _construir_ranking_preflop()


def clase_mano(c1, c2):
    r1, r2 = (c1 >> 2) + 2, (c2 >> 2) + 2
    return (max(r1, r2), min(r1, r2), (c1 & 3) == (c2 & 3) and r1 != r2)


def indice_clase(c1, c2):
    """Indice 0..168 en el ranking de jugabilidad (0 = AA, 168 = 32o)."""
    return _INDICE_CLASE[clase_mano(c1, c2)]


def percentil_jugabilidad(c1, c2):
    """0.0 = AA, ~1.0 = 32o segun jugabilidad real en NLHE."""
    return _PCT_JUGABILIDAD[clase_mano(c1, c2)]


# --- 2. Clasificacion postflop: Made Tier + Draw Potential --------------------

def clasificar_mano_postflop(mano, board):
    """-> (made_tier 0..6, draw_tier 0..3, nut_flush_blocker bool).

    made_tier:
      0: carta alta / solo lo de la mesa
      1: par debil / underpair / 2da o 3ra pareja
      2: top pair kicker < J, o par de mano entre top y 2da carta
      3: top pair kicker >= J, o overpair
      4: doble par propio
      5: trio / set propio
      6: escalera, color, full, poker, escalera de color propios
    draw_tier (0..3, siempre 0 en river):
      0: sin proyecto
      1: gutshot (4 outs propios) o 2 overcards limpias
      2: flush draw (9 outs propios) u OESD (8 outs propios)
      3: combo draw (FD + escalera, FD + overcards >= J, o FD + par)
    """
    c0, c1 = mano[0], mano[1]
    r0, r1 = c0 >> 2, c1 >> 2
    s0, s1 = c0 & 3, c1 & 3
    hi_r, lo_r = (r0, r1) if r0 >= r1 else (r1, r0)

    ev_total = evaluar(list(mano) + list(board))
    cat_total = categoria(ev_total)
    cat_prop = categoria_propia(mano, board)

    board_ranks = [c >> 2 for c in board]
    max_board = max(board_ranks) if board_ranks else 0
    board_count = {}
    palos_board = [0, 0, 0, 0]
    mask_board = 0
    for c in board:
        br = c >> 2
        board_count[br] = board_count.get(br, 0) + 1
        palos_board[c & 3] += 1
        mask_board |= 1 << br

    # Bloqueador al color de las nuts (As del palo con >=3 en mesa o 2 en flop)
    nut_blocker = False
    for s in range(4):
        if palos_board[s] >= 2:
            if (r0 == 12 and s0 == s) or (r1 == 12 and s1 == s):
                nut_blocker = True
                break

    # Determinar si mano mejora al board cuando len(board) == 5
    solo_mesa = False
    if len(board) == 5 and cat_total >= ESCALERA:
        if evaluar(board) == ev_total:
            solo_mesa = True

    if cat_prop >= ESCALERA and not solo_mesa:
        made_tier = 6
    elif cat_prop == TRIO:
        made_tier = 5
    elif cat_prop == DOBLE_PAR:
        made_tier = 4
    elif cat_prop == PAR:
        # Identificar que pareja tenemos realmente
        if r0 == r1 and r0 > max_board:
            made_tier = 3  # Overpair
        elif (r0 == max_board and board_count.get(r0, 0) == 1) or (r1 == max_board and board_count.get(r1, 0) == 1):
            kicker = r1 if r0 == max_board else r0
            made_tier = 3 if kicker >= 9 else 2  # J (r=9) o mayor = TPTK/good kicker
        elif r0 == r1:
            # Pocket pair debajo de la mas alta: 2 si > 2da carta del board, else 1
            unicos_desc = sorted(set(board_ranks), reverse=True)
            segunda = unicos_desc[1] if len(unicos_desc) > 1 else -1
            made_tier = 2 if r0 > segunda else 1
        elif (board_count.get(r0, 0) == 1) or (board_count.get(r1, 0) == 1):
            made_tier = 1
        else:
            made_tier = 0
    else:
        made_tier = 0

    # Proyectos (solo con cartas por venir)
    draw_tier = 0
    if len(board) < 5 and made_tier < 5:
        flush_draw = False
        two_card_flush = False
        for s in (s0, s1):
            c_prop = (1 if s0 == s else 0) + (1 if s1 == s else 0)
            if palos_board[s] + c_prop == 4:
                if c_prop == 2:
                    flush_draw = True
                    two_card_flush = True
                    break
                # Una sola carta al color sobre 3 del palo en mesa: solo es draw fuerte si >= Q
                carta_r = r0 if s0 == s else r1
                if carta_r >= 10:
                    flush_draw = True
                    break

        # Proyecto de escalera PROPIO: debe usar al menos una carta nuestra en la ventana
        straight_outs = 0
        mask_all = mask_board | (1 << r0) | (1 << r1)
        if STRAIGHT[mask_all] < 0:
            # Contar rangos que completan escalera donde la escalera resultante usa
            # al menos un rango propio que NO estaba en el board (o mejora al board).
            propios = set()
            if (mask_board >> r0) & 1 == 0:
                propios.add(r0)
            if (mask_board >> r1) & 1 == 0:
                propios.add(r1)
            if propios:
                for cand in range(13):
                    if (mask_all >> cand) & 1:
                        continue
                    nueva_all = mask_all | (1 << cand)
                    st_all = STRAIGHT[nueva_all]
                    if st_all >= 0:
                        # Verificar que sin nuestras cartas propias no habia esa escalera
                        st_board = STRAIGHT[mask_board | (1 << cand)]
                        if st_all > st_board:
                            straight_outs += 1

        overcards = (hi_r > max_board and lo_r > max_board and hi_r >= 10)
        if flush_draw and (straight_outs >= 1 or overcards or (two_card_flush and made_tier >= 2)):
            draw_tier = 3
        elif flush_draw or straight_outs >= 2:
            draw_tier = 2
        elif straight_outs == 1 or (overcards and made_tier == 0):
            draw_tier = 1

    return made_tier, draw_tier, nut_blocker


def bucket_textura(humedad, emparejada):
    if emparejada:
        return 2
    return 1 if humedad >= 0.40 else 0


def bucket_spr(spr):
    if spr <= 2.5:
        return 0
    if spr <= 6.5:
        return 1
    return 2


def bucket_facing(por_igualar, pozo_total, subidas_calle, ya_actuo_alguien):
    if por_igualar <= 0:
        return 1 if ya_actuo_alguien else 0
    pozo_antes = max(1, pozo_total - por_igualar)
    frac = por_igualar / float(pozo_antes)
    if subidas_calle >= 2:
        return 4
    return 2 if frac <= 0.55 else 3

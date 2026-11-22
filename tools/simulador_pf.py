"""Simulador fiel del motor de PokerFight (crupier, pozo, mesa, turnos y reglas).

Replica exactamente `internal/crupier/` y `internal/mesa/` del kit oficial:
- Orden de reparto de privadas (2 vueltas desde silla 0) y quemado de cartas.
- Posiciones de ciegas (2 jugadores: boton=SB; 3+: SB=boton+1, BB=boton+2).
- Orden de accion preflop y postflop, all-in corto sin reabrir accion,
  acumulacion de varios all-in cortos.
- Descomposicion de pozos laterales y fusion de dinero muerto sin elegibles.
- Desempate de fichas sobrantes al primer ganador a la izquierda del boton.
- Orden de eliminacion (menor saldo al inicio de la mano cae primero).
- Semillas deterministas por mesa para comparaciones pareadas exactas (v8 vs v9).
"""

import os
import random
import sys
import time

from tools.bots_kit import BotAdapter, fast_eval

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _a_carta_proto(c):
    return {"rango": (c >> 2) + 2, "palo": c & 3}


class PokalatroAdapter(BotAdapter):
    """Envuelve una carpeta de Pokalatro (`backup_v8` o raiz v9) usando sus propios modulos."""

    def __init__(self, pkg_dir, label="pokalatro", ablate=None):
        self.pkg_dir = os.path.abspath(pkg_dir)
        self.nombre = label
        self.ablate = ablate or {}
        self.tiempos_ms = []
        self.invalidas = 0

    def iniciar(self, bot_id, seed=1):
        super().iniciar(bot_id, seed)
        # Importar modulos aislados desde self.pkg_dir
        self._mods = self._cargar_modulos()
        self.stats = self._mods["stats"].Estadisticas()
        self.mano = []
        self.historial = []
        self.ultimo_estado = {}
        self.silla = 0
        self.tiempos_ms = []
        self.invalidas = 0

    def _cargar_modulos(self):
        # Cache por (pkg_dir, frozenset(ablate)) para evitar recargar en cada mesa
        key = (self.pkg_dir, tuple(sorted(self.ablate.items())))
        if key in _MOD_CACHE:
            return _MOD_CACHE[key]
        import importlib.util
        saved_sys_path = list(sys.path)
        saved_mods = {}
        mod_names = [
            "tabla_preflop", "motor", "turno", "stats", "rangos",
            "abstraccion", "blueprint_data", "torneo", "subjuego",
            "preflop", "postflop", "estrategia",
        ]
        for mn in mod_names:
            if mn in sys.modules:
                saved_mods[mn] = sys.modules.pop(mn)
        sys.path.insert(0, self.pkg_dir)
        loaded = {}
        try:
            for mn in mod_names:
                fp = os.path.join(self.pkg_dir, mn + ".py")
                if os.path.exists(fp):
                    spec = importlib.util.spec_from_file_location(mn, fp)
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[mn] = mod
                    spec.loader.exec_module(mod)
                    loaded[mn] = mod
        finally:
            for mn in mod_names:
                sys.modules.pop(mn, None)
            sys.modules.update(saved_mods)
            sys.path[:] = saved_sys_path
        _MOD_CACHE[key] = loaded
        return loaded

    def mano_inicio(self, estado, cartas):
        self.mano = cartas
        self.historial = []
        self.ultimo_estado = estado
        for j in estado.get("jugadores", []):
            if j["id"] == self.id:
                self.silla = j["posicion_silla"]
                break

    def estado_actualizado(self, estado):
        self.ultimo_estado = estado
        if estado.get("historial_acciones"):
            self.historial = estado["historial_acciones"]

    def mano_fin(self, resultado):
        stats_mod = self._mods["stats"]
        motor_mod = self._mods["motor"]
        ab_mod = self._mods.get("abstraccion")
        self.stats.registrar_mano(self.historial, self.id)
        board = motor_mod.a_enteros(resultado.get("comunitarias") or [])

        def _pct(cartas):
            e = motor_mod.a_enteros(cartas[:2])
            if ab_mod is not None:
                return ab_mod.percentil_jugabilidad(e[0], e[1])
            return motor_mod.percentil_mano(e[0], e[1])

        def _fuerza(cartas, brd):
            return motor_mod.fuerza_vs_azar(motor_mod.a_enteros(cartas[:2]), brd)

        self.stats.registrar_showdown(
            resultado.get("mostradas"), self.id, _pct,
            board=board, historial=self.historial, fuerza=_fuerza,
        )
        self.historial = []

    def decidir(self, mensaje, cartas):
        Turno = self._mods["turno"].Turno
        decidir_fn = self._mods["estrategia"].decidir
        conn = type("C", (), {"id_jugador": self.id, "silla": self.silla})()
        t0 = time.perf_counter()
        t = Turno(conn, mensaje, cartas, self.stats)
        if self.ablate:
            t.ablate = self.ablate
        accion, _ = decidir_fn(t)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        self.tiempos_ms.append(dt_ms)
        self.ultimo_estado = mensaje.get("estado") or self.ultimo_estado
        self.historial = self.ultimo_estado.get("historial_acciones") or self.historial
        return accion


_MOD_CACHE = {}


class JugadorMesa:
    __slots__ = (
        "id", "nombre", "bot", "saldo", "apuesta_ronda", "cartas",
        "activo", "retirado", "allin", "silla", "en_mano",
        "timeouts", "manos_jugadas", "saldo_inicio_mano", "neto_acumulado",
    )

    def __init__(self, jid, nombre, bot, saldo, silla):
        self.id = jid
        self.nombre = nombre
        self.bot = bot
        self.saldo = int(saldo)
        self.apuesta_ronda = 0
        self.cartas = []
        self.activo = True
        self.retirado = False
        self.allin = False
        self.silla = silla
        self.en_mano = False
        self.timeouts = 0
        self.manos_jugadas = 0
        self.saldo_inicio_mano = int(saldo)
        self.neto_acumulado = 0


def _es_accion_valida(j, accion, apuesta_actual, subida_minima, puede_subir, validas):
    if not isinstance(accion, dict):
        return False
    tipo = accion.get("tipo")
    if tipo not in validas:
        return False
    if tipo in ("bet", "raise"):
        monto = accion.get("monto")
        if not isinstance(monto, int) or monto <= 0:
            return False
        techo = j.saldo + j.apuesta_ronda
        if monto > techo or monto <= apuesta_actual:
            return False
        if monto != techo and monto - apuesta_actual < subida_minima:
            return False
    return True


def _acciones_validas(j, apuesta_actual, puede_subir):
    validas = ["fold"]
    pendiente = 0
    if apuesta_actual <= j.apuesta_ronda:
        validas.append("check")
        if j.saldo > 0 and puede_subir:
            validas.append("bet")
    else:
        pendiente = apuesta_actual - j.apuesta_ronda
        validas.append("call")
        if j.saldo > pendiente and puede_subir:
            validas.append("raise")
    if j.saldo > 0 and (puede_subir or j.saldo <= pendiente):
        validas.append("allin")
    return validas


def jugar_mesa(
    bots_con_id,
    max_manos=50,
    stack_inicial=10000,
    ciega_chica=50,
    ciega_grande=100,
    seed=1,
    reponer_stack=False,
    stacks_iniciales=None,
    iniciar_bots=True,
    stats_externas=None,
):
    """Ejecuta una mesa completa siguiendo exactamente las reglas de PokerFight.

    `bots_con_id`: lista de `(jid, BotAdapter)` en orden de silla 0..N-1.
    """
    rng = random.Random(seed)
    n_sillas = len(bots_con_id)
    jugadores = []
    for s, (jid, bot) in enumerate(bots_con_id):
        if iniciar_bots:
            bot.iniciar(jid, seed=seed * 1009 + s * 97 + 7)
        st0 = stacks_iniciales.get(jid, stack_inicial) if stacks_iniciales else stack_inicial
        jugadores.append(JugadorMesa(jid, bot.nombre, bot, st0, s))

    boton = 0
    orden_eliminacion = []
    manos_jugadas = 0

    def activos_torneo():
        return [j for j in jugadores if j.activo]

    def siguiente_si(desde, pred):
        for i in range(1, n_sillas + 1):
            s = (desde + i) % n_sillas
            if pred(jugadores[s]):
                return s
        return None

    for ronda in range(1, max_manos + 1):
        activos = activos_torneo()
        if len(activos) <= 1:
            break

        id_mano = f"mesa-mano-{ronda}"
        mazo = list(range(52))
        rng.shuffle(mazo)
        idx_mazo = 0

        contrib = {j.id: 0 for j in activos}
        activos_pozo = {j.id for j in activos}

        for j in jugadores:
            j.en_mano = False
        for j in activos:
            j.retirado = False
            j.allin = False
            j.apuesta_ronda = 0
            j.en_mano = True
            j.manos_jugadas += 1
            if reponer_stack:
                j.neto_acumulado += j.saldo - stack_inicial
                j.saldo = stack_inicial
            j.saldo_inicio_mano = j.saldo

        # Repartir privadas en 2 vueltas (igual que crupier.go)
        for j in activos:
            j.cartas = [mazo[idx_mazo]]
            idx_mazo += 1
        for j in activos:
            j.cartas.append(mazo[idx_mazo])
            idx_mazo += 1

        # Posiciones de ciegas
        if len(activos) == 2:
            silla_sb = boton if jugadores[boton].en_mano else siguiente_si(boton, lambda x: x.en_mano)
            silla_bb = siguiente_si(silla_sb, lambda x: x.en_mano)
            primer_pre = silla_sb
        else:
            silla_sb = siguiente_si(boton, lambda x: x.en_mano)
            silla_bb = siguiente_si(silla_sb, lambda x: x.en_mano)
            primer_pre = siguiente_si(silla_bb, lambda x: x.en_mano)

        def aportar(j, monto):
            m = min(monto, j.saldo)
            j.saldo -= m
            j.apuesta_ronda += m
            if j.saldo == 0:
                j.allin = True
            if m > 0:
                contrib[j.id] += m
            return m

        aportar(jugadores[silla_sb], ciega_chica)
        aportar(jugadores[silla_bb], ciega_grande)
        apuesta_actual = ciega_grande
        subida_minima = ciega_grande
        comunitarias = []
        historial = []
        etapa = "preflop"

        def hacer_estado():
            return {
                "id_mano": id_mano,
                "etapa": etapa,
                "comunitarias": [_a_carta_proto(c) for c in comunitarias],
                "pozo": sum(contrib.values()),
                "apuesta_actual": apuesta_actual,
                "subida_minima": subida_minima,
                "ciega_chica": ciega_chica,
                "ciega_grande": ciega_grande,
                "posicion_boton": boton,
                "jugadores": [
                    {
                        "id": j.id,
                        "nombre": j.nombre,
                        "saldo": j.saldo,
                        "apuesta_ronda": j.apuesta_ronda,
                        "activo": j.en_mano and not j.retirado,
                        "en_torneo": j.activo,
                        "en_mano": j.en_mano,
                        "allin": j.allin,
                        "posicion_silla": j.silla,
                    }
                    for j in jugadores
                ],
                "historial_acciones": list(historial),
            }

        est_ini = hacer_estado()
        for j in activos:
            j.bot.mano_inicio(est_ini, [_a_carta_proto(c) for c in j.cartas])

        def contar_en_mano():
            return sum(1 for j in jugadores if j.en_mano and not j.retirado)

        def contar_pueden_actuar():
            return sum(1 for j in jugadores if j.en_mano and not j.retirado and not j.allin)

        def ronda_apuestas(et, primer_silla):
            nonlocal apuesta_actual, subida_minima
            vista = {}
            pendientes = {
                j.silla for j in jugadores
                if j.en_mano and not j.retirado and not j.allin
            }
            silla = primer_silla
            while pendientes:
                if contar_en_mano() <= 1:
                    return
                j = jugadores[silla]
                debe = (silla in pendientes) and j.en_mano and not j.retirado and not j.allin
                if debe and j.apuesta_ronda >= apuesta_actual and contar_pueden_actuar() <= 1:
                    debe = False
                pendientes.discard(silla)
                if not debe:
                    silla = siguiente_si(silla, lambda x: x.en_mano)
                    if silla is None:
                        return
                    continue

                puede_subir = True
                if silla in vista:
                    puede_subir = (apuesta_actual - vista[silla]) >= subida_minima
                validas = _acciones_validas(j, apuesta_actual, puede_subir)
                est = hacer_estado()
                msg = {
                    "tipo": "solicitar_accion",
                    "version": "1.0.0",
                    "acciones_validas": validas,
                    "timeout_ms": 5000,
                    "estado": est,
                }
                cartas_p = [_a_carta_proto(c) for c in j.cartas]
                try:
                    rec = j.bot.decidir(msg, cartas_p)
                except Exception:
                    rec = None

                if _es_accion_valida(j, rec, apuesta_actual, subida_minima, puede_subir, validas):
                    acc = rec
                else:
                    j.timeouts += 1
                    if hasattr(j.bot, "invalidas"):
                        j.bot.invalidas += 1
                    pend = apuesta_actual - j.apuesta_ronda
                    acc = {"tipo": "check"} if pend <= 0 else {"tipo": "fold"}

                ap_antes = apuesta_actual
                t = acc["tipo"]
                if t == "fold":
                    j.retirado = True
                    activos_pozo.discard(j.id)
                elif t == "call":
                    aportar(j, apuesta_actual - j.apuesta_ronda)
                elif t in ("bet", "raise"):
                    obj = acc["monto"]
                    inc = obj - apuesta_actual
                    aportar(j, obj - j.apuesta_ronda)
                    if inc >= subida_minima:
                        subida_minima = inc
                    apuesta_actual = j.apuesta_ronda
                elif t == "allin":
                    previa = apuesta_actual
                    aportar(j, j.saldo)
                    if j.apuesta_ronda > previa:
                        inc = j.apuesta_ronda - previa
                        if inc >= subida_minima:
                            subida_minima = inc
                        apuesta_actual = j.apuesta_ronda

                vista[silla] = apuesta_actual
                reg = {"id_jugador": j.id, "etapa": et, "accion": dict(acc)}
                historial.append(reg)
                if apuesta_actual > ap_antes:
                    for jj in jugadores:
                        if jj.silla != silla and jj.en_mano and not jj.retirado and not jj.allin and jj.apuesta_ronda < apuesta_actual:
                            pendientes.add(jj.silla)

                est_post = hacer_estado()
                for jj in activos:
                    if jj.silla != silla:
                        jj.bot.estado_actualizado(est_post)

                silla = siguiente_si(silla, lambda x: x.en_mano)
                if silla is None:
                    return

        ronda_apuestas("preflop", primer_pre)

        for et, cant_com in (("flop", 3), ("turn", 1), ("river", 1)):
            if contar_en_mano() <= 1:
                break
            idx_mazo += 1  # quemar 1 carta
            comunitarias.extend(mazo[idx_mazo:idx_mazo + cant_com])
            idx_mazo += cant_com
            etapa = et
            for j in activos:
                j.apuesta_ronda = 0
            apuesta_actual = 0
            subida_minima = ciega_grande
            primer_post = siguiente_si(boton, lambda x: x.en_mano and not x.retirado)
            hay_quien = primer_post is not None and contar_pueden_actuar() > 1
            est_c = hacer_estado()
            for j in activos:
                j.bot.estado_actualizado(est_c)
            if hay_quien:
                ronda_apuestas(et, primer_post)

        # Repartir pozo (en orden horario desde boton+1)
        participantes = []
        for i in range(1, n_sillas + 1):
            j = jugadores[(boton + i) % n_sillas]
            if j.en_mano and not j.retirado:
                participantes.append(j)

        if len(participantes) == 1:
            p0 = participantes[0]
            tot = sum(contrib.values())
            p0.saldo += tot
            resultado = {
                "id_mano": id_mano,
                "comunitarias": [_a_carta_proto(c) for c in comunitarias],
                "repartos": [{"id_jugador": p0.id, "monto": tot}],
            }
        else:
            evals = {j.id: fast_eval(j.cartas + comunitarias) for j in participantes}
            niveles = sorted({v for v in contrib.values() if v > 0})
            subpozos = []
            ant = 0
            for tope in niveles:
                tramo = tope - ant
                fichas = sum(max(0, min(v - ant, tramo)) for v in contrib.values())
                eleg = [jid for jid in activos_pozo if contrib[jid] >= tope]
                if fichas > 0:
                    if not eleg and subpozos:
                        subpozos[-1][0] += fichas
                    else:
                        subpozos.append([fichas, eleg])
                ant = tope

            repartos_map = {}
            for monto_sp, eleg in subpozos:
                if not eleg:
                    continue
                eleg_set = set(eleg)
                max_pts = max(evals[jid] for jid in eleg)
                ganadores = [j.id for j in participantes if j.id in eleg_set and evals[j.id] == max_pts]
                if not ganadores:
                    continue
                premio = monto_sp // len(ganadores)
                resto = monto_sp % len(ganadores)
                for idx_g, gid in enumerate(ganadores):
                    repartos_map[gid] = repartos_map.get(gid, 0) + premio + (1 if idx_g < resto else 0)

            for j in activos:
                if j.id in repartos_map:
                    j.saldo += repartos_map[j.id]

            resultado = {
                "id_mano": id_mano,
                "comunitarias": [_a_carta_proto(c) for c in comunitarias],
                "repartos": [{"id_jugador": j.id, "monto": repartos_map[j.id]} for j in participantes if j.id in repartos_map],
                "mostradas": {j.id: [_a_carta_proto(c) for c in j.cartas] for j in participantes},
            }

        if not reponer_stack:
            caidos = [j for j in activos if j.saldo == 0 and j.activo]
            for j in caidos:
                j.activo = False
            caidos.sort(key=lambda x: x.saldo_inicio_mano)
            for j in caidos:
                orden_eliminacion.append(j.id)

        for j in activos:
            j.bot.mano_fin(resultado)

        sig_btn = siguiente_si(boton, lambda x: x.activo)
        if sig_btn is not None:
            boton = sig_btn
        manos_jugadas = ronda

    # Clasificacion final (igual que resumenFinal en mesa.go)
    elim_pos = {jid: i + 1 for i, jid in enumerate(orden_eliminacion)}

    def neto_de(j):
        if reponer_stack:
            return j.neto_acumulado + j.saldo - stack_inicial
        st0 = stacks_iniciales.get(j.id, stack_inicial) if stacks_iniciales else stack_inicial
        return j.saldo - st0

    ordenados = sorted(
        jugadores,
        key=lambda j: (
            neto_de(j) if reponer_stack else j.saldo,
            elim_pos.get(j.id, 0),
        ),
        reverse=True,
    )
    res = []
    for puesto, j in enumerate(ordenados, 1):
        res.append({
            "id": j.id,
            "nombre": j.nombre,
            "puesto": puesto,
            "saldo": j.saldo,
            "neto": neto_de(j),
            "timeouts": j.timeouts,
            "manos": j.manos_jugadas,
            "quiebra": j.saldo == 0 and not reponer_stack,
            "tiempos_ms": getattr(j.bot, "tiempos_ms", []),
            "invalidas": getattr(j.bot, "invalidas", 0),
        })
    return {"manos_jugadas": manos_jugadas, "posiciones": res}

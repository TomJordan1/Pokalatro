"""Puente ACPC 2.0.0 TCP para enfrentar Pokalatro contra Slumbot2019 (bin/run_bot).

Ejecuta partidas duplicadas de Texas Hold'em No-Limit Heads-Up (50/100) entre
Pokalatro (cualquier directorio de paquete: raiz v9 o backup_v8) y el binario
compilado en C++ de ericgjackson/slumbot2019 corriendo en WSL sobre TCP local.
"""

import argparse
import math
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.bots_kit import BotAdapter
from tools.simulador_pf import PokalatroAdapter, jugar_mesa

SLUMBOT_DIR_WSL = (
    "/mnt/c/Users/Tom/.gemini/antigravity/brain/"
    "a73746aa-13b8-4606-afca-0d01a65cbeb0/scratch/slumbot2019"
)

_RANKS = "23456789TJQKA"
_SUITS = "cdhs"
_ETAPAS = ("preflop", "flop", "turn", "river")


def carta_acpc(c):
    return _RANKS[c["rango"] - 2] + _SUITS[c["palo"]]


def ordenar_cartas_desc(cartas):
    return sorted(
        cartas,
        key=lambda c: (c["rango"] - 2) * 4 + c["palo"],
        reverse=True,
    )


def construir_matchstate(estado, slumbot_id, slumbot_pos, cartas_privadas, hand_no, stack_size):
    """Convierte el estado PokerFight a una linea MATCHSTATE ACPC 2.0.0."""
    jugadores = estado.get("jugadores") or []
    boton = estado.get("posicion_boton", 0)
    # En 2 jugadores PokerFight: silla_sb = boton (pos 1 ACPC), otra = BB (pos 0 ACPC)
    sb_id = None
    bb_id = None
    for j in jugadores:
        if j["posicion_silla"] == boton:
            sb_id = j["id"]
        else:
            bb_id = j["id"]

    contrib = {sb_id: estado.get("ciega_chica", 50), bb_id: estado.get("ciega_grande", 100)}
    bet_calle = {sb_id: contrib[sb_id], bb_id: contrib[bb_id]}
    last_bet_to = estado.get("ciega_grande", 100)
    etapa_idx = 0
    tokens = []

    for reg in estado.get("historial_acciones") or []:
        et = reg.get("etapa", "preflop")
        target_idx = _ETAPAS.index(et)
        while etapa_idx < target_idx:
            tokens.append("/")
            etapa_idx += 1
            bet_calle = {sb_id: 0, bb_id: 0}

        jid = reg.get("id_jugador")
        acc = reg.get("accion") or {}
        t = acc.get("tipo")
        if t == "fold":
            tokens.append("f")
        elif t in ("check", "call"):
            tokens.append("c")
            if t == "call":
                rival = bb_id if jid == sb_id else sb_id
                falta = max(0, contrib[rival] - contrib[jid])
                contrib[jid] += falta
                bet_calle[jid] += falta
        elif t in ("bet", "raise"):
            monto = int(acc.get("monto", 0))
            delta = max(0, monto - bet_calle[jid])
            bet_calle[jid] = monto
            contrib[jid] += delta
            last_bet_to = min(stack_size, contrib[jid])
            tokens.append("r" + str(last_bet_to))
        elif t == "allin":
            rival = bb_id if jid == sb_id else sb_id
            if contrib[rival] >= stack_size or last_bet_to >= stack_size:
                tokens.append("c")
                contrib[jid] = stack_size
            else:
                contrib[jid] = stack_size
                last_bet_to = stack_size
                tokens.append("r" + str(stack_size))

    etapa_actual_idx = _ETAPAS.index(estado.get("etapa", "preflop"))
    while etapa_idx < etapa_actual_idx:
        tokens.append("/")
        etapa_idx += 1
        bet_calle = {sb_id: 0, bb_id: 0}

    betting_str = "".join(tokens)
    priv = "".join(carta_acpc(c) for c in ordenar_cartas_desc(cartas_privadas))
    cards_0 = (priv + "|") if slumbot_pos == 0 else ("|" + priv)

    com = estado.get("comunitarias") or []
    board_parts = [cards_0]
    if len(com) >= 3:
        board_parts.append("".join(carta_acpc(c) for c in ordenar_cartas_desc(com[:3])))
    if len(com) >= 4:
        board_parts.append(carta_acpc(com[3]))
    if len(com) >= 5:
        board_parts.append(carta_acpc(com[4]))
    cards_str = "/".join(board_parts)

    line = f"MATCHSTATE:{slumbot_pos}:{hand_no}:{betting_str}:{cards_str}"
    return line, last_bet_to, contrib, bet_calle


class SlumbotACPCAdapter(BotAdapter):
    """Levanta `bin/run_bot` en WSL, se conecta por TCP ACPC 2.0.0 y traduce jugadas."""

    def __init__(
        self,
        card_params="hs100otfr_params",
        betting_params="pf100_params",
        cfr_params="ecfr_params",
        it=5,
        seed=1,
        label="Slumbot2019",
    ):
        self.nombre = label
        self.card_params = card_params
        self.betting_params = betting_params
        self.cfr_params = cfr_params
        self.it = it
        self.seed = seed
        self.proc = None
        self.srv = None
        self.conn = None
        self.rfile = None
        self.hand_no = 0
        self.stack_size = 10000
        self.cartas = []
        self.tiempos_ms = []
        self.invalidas = 0
        self.stderr_path = os.path.join(ROOT, "tools", "slumbot_err.log")

    def start_process(self):
        if self.proc is not None:
            return
        relay = (
            "import socket,subprocess,sys;"
            "s=socket.socket();s.bind(('127.0.0.1',0));s.listen(1);"
            "port=s.getsockname()[1];"
            "p=subprocess.Popen(sys.argv[1:]+['127.0.0.1',str(port)]);"
            "c,_=s.accept();"
            "r=c.makefile('r',encoding='ascii',newline='\\n');"
            "v=r.readline();sys.stdout.write(v);sys.stdout.flush();"
            "\nfor ln in sys.stdin:\n"
            " c.sendall(ln.encode('ascii'));resp=r.readline();\n"
            " if not resp: break\n"
            " sys.stdout.write(resp);sys.stdout.flush()\n"
        )
        import shlex
        bot_args = [
            "../bin/run_bot",
            "holdem_params",
            self.card_params,
            self.betting_params,
            self.cfr_params,
            str(self.it),
            str(self.seed),
        ]
        cmd = (
            f"cd {SLUMBOT_DIR_WSL}/runs && "
            f"python3 -c {shlex.quote(relay)} " + " ".join(shlex.quote(a) for a in bot_args)
        )
        self._err_fh = open(self.stderr_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            ["wsl", "bash", "-c", cmd],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._err_fh,
            text=True,
            encoding="ascii",
            bufsize=1,
        )
        try:
            ver = self.proc.stdout.readline().strip()
            if not ver.startswith("VERSION:"):
                raise RuntimeError(f"Handshake inesperado de run_bot: {ver!r}")
        except Exception:
            self.close()
            raise

    def close(self):
        if self.proc is not None:
            for stream in (self.proc.stdin, self.proc.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        if hasattr(self, "_err_fh") and self._err_fh:
            try:
                self._err_fh.close()
            except Exception:
                pass

    def iniciar(self, bot_id, seed=1):
        super().iniciar(bot_id, seed)
        self.hand_no = 0
        self.tiempos_ms = []
        self.invalidas = 0
        if self.proc is None:
            self.start_process()

    def mano_inicio(self, estado, cartas):
        self.hand_no += 1
        self.cartas = cartas
        # Detectar stack total efectivo (asumimos mesas con stack constante = 10000 o 20000)
        for j in estado.get("jugadores") or []:
            if j["id"] == self.id:
                tot = j["saldo"] + j["apuesta_ronda"]
                if tot > 0:
                    self.stack_size = max(self.stack_size, tot)

    def decidir(self, mensaje, cartas):
        estado = mensaje.get("estado") or {}
        validas = mensaje.get("acciones_validas") or ["check", "fold"]
        boton = estado.get("posicion_boton", 0)
        mi_j = next(j for j in estado["jugadores"] if j["id"] == self.id)
        slumbot_pos = 1 if mi_j["posicion_silla"] == boton else 0

        line, last_bet_to, contrib, bet_calle = construir_matchstate(
            estado, self.id, slumbot_pos, cartas, self.hand_no, self.stack_size
        )

        t0 = time.perf_counter()
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        resp = self.proc.stdout.readline().strip()
        self.tiempos_ms.append((time.perf_counter() - t0) * 1000.0)

        if not resp or ":" not in resp:
            return {"tipo": "check"} if "check" in validas else {"tipo": "fold"}

        act = resp.rsplit(":", 1)[-1].strip()
        if act == "f":
            return {"tipo": "fold"} if "fold" in validas else {"tipo": "check"}
        if act == "c":
            if "check" in validas:
                return {"tipo": "check"}
            return {"tipo": "call"} if "call" in validas else {"tipo": "allin"}
        if act.startswith("r"):
            try:
                inc_sb = int(act[1:])
            except ValueError:
                inc_sb = 100
            # agent.cpp:644 devuelve el incremento (next_last_bet_to - curr_last_bet_to) * sb
            target_total = min(self.stack_size, last_bet_to + max(1, inc_sb))
            prior_streets = contrib[self.id] - bet_calle[self.id]
            target_round = target_total - prior_streets

            techo = mi_j["saldo"] + mi_j["apuesta_ronda"]
            ap_actual = estado.get("apuesta_actual", 0)
            sub_min = estado.get("subida_minima", estado.get("ciega_grande", 100))

            if target_round >= techo:
                if "allin" in validas:
                    return {"tipo": "allin"}
                return {"tipo": "call"} if "call" in validas else {"tipo": "check"}

            min_legal = ap_actual + sub_min
            monto = max(min_legal, target_round)
            if monto >= techo:
                if "allin" in validas:
                    return {"tipo": "allin"}
                return {"tipo": "call"} if "call" in validas else {"tipo": "check"}

            tipo = "bet" if "bet" in validas else ("raise" if "raise" in validas else None)
            if tipo:
                return {"tipo": tipo, "monto": int(monto)}
            if "allin" in validas:
                return {"tipo": "allin"}
        return {"tipo": "check"} if "check" in validas else {"tipo": "fold"}


def correr_duplicado(
    pkg_dir,
    n_bloques=6,
    manos_por_bloque=100,
    stack=10000,
    seed_base=2026,
    card_params="hs100otfr_params",
    betting_params="pf100_params",
    cfr_params="ecfr_params",
    it=5,
    verbose=True,
):
    slumbot = SlumbotACPCAdapter(
        card_params=card_params,
        betting_params=betting_params,
        cfr_params=cfr_params,
        it=it,
        seed=seed_base,
    )
    slumbot.start_process()
    poka = PokalatroAdapter(pkg_dir, label="Pokalatro")
    diffs = []
    tot_poka = 0
    tot_manos = 0
    bloques_ganados = 0
    try:
        for b in range(n_bloques):
            s = seed_base + b * 37
            r1 = jugar_mesa(
                [("poka", poka), ("slum", slumbot)],
                max_manos=manos_por_bloque,
                stack_inicial=stack,
                seed=s,
                reponer_stack=True,
            )
            r2 = jugar_mesa(
                [("slum", slumbot), ("poka", poka)],
                max_manos=manos_por_bloque,
                stack_inicial=stack,
                seed=s,
                reponer_stack=True,
            )
            n1 = next(x["neto"] for x in r1["posiciones"] if x["id"] == "poka")
            n2 = next(x["neto"] for x in r2["posiciones"] if x["id"] == "poka")
            diff = n1 + n2
            m = r1["manos_jugadas"] + r2["manos_jugadas"]
            diffs.append(diff)
            tot_poka += diff
            tot_manos += m
            if diff > 0:
                bloques_ganados += 1
            if verbose:
                mbb = (diff / max(1, m)) * 10.0
                print(
                    f"Bloque {b+1}/{n_bloques} ({m} manos dup): Pokalatro {diff:+d} fichas ({mbb:+.1f} mbb/h) "
                    f"[ida={n1:+d}, vuelta={n2:+d}]",
                    flush=True,
                )
    finally:
        slumbot.close()

    mbb_h = (tot_poka / max(1, tot_manos)) * 10.0
    if len(diffs) > 1:
        mean_d = sum(diffs) / len(diffs)
        var_d = sum((x - mean_d) ** 2 for x in diffs) / (len(diffs) - 1)
        se_mbb = (math.sqrt(var_d / len(diffs)) / (2 * manos_por_bloque)) * 10.0
        ic95 = 1.96 * se_mbb
    else:
        ic95 = 0.0
    print(
        f"\n=== RESUMEN HUNL vs Slumbot2019 ({tot_manos} manos totales) ===\n"
        f"Puntaje Pokalatro: {tot_poka:+d} fichas | {mbb_h:+.1f} +/- {ic95:.1f} mbb/hand\n"
        f"Bloques duplicados ganados por Pokalatro: {bloques_ganados}/{n_bloques}",
        flush=True,
    )
    return {"neto": tot_poka, "manos": tot_manos, "mbb_h": mbb_h, "ic95": ic95, "bloques_ganados": bloques_ganados}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--pkg", default=os.path.join(ROOT, "app"))
    p.add_argument("--bloques", type=int, default=6)
    p.add_argument("--manos", type=int, default=100)
    p.add_argument("--stack", type=int, default=10000)
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--card-params", default="hs100otfr_params")
    p.add_argument("--betting-params", default="pf100_params")
    p.add_argument("--cfr-params", default="ecfr_params")
    p.add_argument("--it", type=int, default=5)
    args = p.parse_args()
    correr_duplicado(
        args.pkg,
        n_bloques=args.bloques,
        manos_por_bloque=args.manos,
        stack=args.stack,
        seed_base=args.seed,
        card_params=args.card_params,
        betting_params=args.betting_params,
        cfr_params=args.cfr_params,
        it=args.it,
    )

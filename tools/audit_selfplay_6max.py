"""Auditoria de self-play 6-max: 6 instancias de Pokalatro v9 jugando entre si."""

import multiprocessing as mp
import os
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(ROOT, "app")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.simulador_pf import PokalatroAdapter, jugar_mesa


def _fmt_cartas(cs):
    if not cs:
        return "-"
    return " ".join(f"{c.get('rango', c.get('valor', '?'))}{str(c.get('palo', '?'))[0]}" for c in cs)


class AuditAdapter(PokalatroAdapter):
    def __init__(self, pkg_dir, label="audit"):
        super().__init__(pkg_dir, label=label)
        self.saldo_ini_mano = 10000
        self.traza_mano = []
        self.grandes_perdidas = []

    def mano_inicio(self, estado, cartas):
        super().mano_inicio(estado, cartas)
        self.traza_mano = []
        for j in estado.get("jugadores", []):
            if j["id"] == self.id:
                self.saldo_ini_mano = j["saldo"] + j.get("apuesta_ronda", 0)
                break

    def decidir(self, mensaje, cartas):
        Turno = self._mods["turno"].Turno
        decidir_fn = self._mods["estrategia"].decidir
        conn = type("C", (), {"id_jugador": self.id, "silla": self.silla})()
        t0 = time.perf_counter()
        t = Turno(conn, mensaje, cartas, self.stats)
        accion, motivo = decidir_fn(t)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        self.tiempos_ms.append(dt_ms)
        self.ultimo_estado = mensaje.get("estado") or self.ultimo_estado
        self.historial = self.ultimo_estado.get("historial_acciones") or self.historial
        self.traza_mano.append((t.etapa, accion.get("tipo"), accion.get("monto"), motivo, t.por_igualar, t.pozo))
        return accion

    def mano_fin(self, resultado):
        est = self.ultimo_estado or {}
        saldo_act = self.saldo_ini_mano
        for j in est.get("jugadores", []):
            if j["id"] == self.id:
                saldo_act = j["saldo"]
                break
        cobro = sum(r.get("monto", 0) for r in (resultado.get("repartos") or []) if r.get("id_jugador") == self.id)
        neto = (saldo_act + cobro) - self.saldo_ini_mano
        if neto <= -2500 and self.traza_mano:
            self.grandes_perdidas.append({
                "neto": neto,
                "yo": self.id,
                "mano": _fmt_cartas(self.mano),
                "board": _fmt_cartas(resultado.get("comunitarias") or []),
                "ultima": self.traza_mano[-1],
                "mostradas": {k: _fmt_cartas(v) for k, v in (resultado.get("mostradas") or {}).items() if k != self.id},
            })
        super().mano_fin(resultado)


def _correr_mesa(mesa_idx):
    bots = [(f"p{s}", AuditAdapter(APP_DIR, label=f"p{s}")) for s in range(6)]
    res = jugar_mesa(bots, max_manos=50, stack_inicial=10000, ciega_chica=50, ciega_grande=100, seed=9000 + mesa_idx)
    perdidas = []
    tiempos = []
    invalidas = 0
    for _, b in bots:
        perdidas.extend(b.grandes_perdidas)
        tiempos.extend(b.tiempos_ms)
        invalidas += b.invalidas
    return {
        "mesa": mesa_idx,
        "manos": res["manos_jugadas"],
        "perdidas": perdidas,
        "tiempos": tiempos,
        "invalidas": invalidas,
    }


def main():
    n_mesas = 30
    workers = max(2, min(10, (os.cpu_count() or 4) - 1))
    t0 = time.perf_counter()
    with mp.Pool(processes=workers) as pool:
        resultados = pool.map(_correr_mesa, range(n_mesas))
    dt = time.perf_counter() - t0

    tot_manos = sum(r["manos"] for r in resultados)
    tot_inv = sum(r["invalidas"] for r in resultados)
    tiempos = sorted(t for r in resultados for t in r["tiempos"])
    perdidas = sorted((p for r in resultados for p in r["perdidas"]), key=lambda x: x["neto"])

    print(f"Self-play 6-max: {n_mesas} mesas, {tot_manos} manos totales, {dt:.1f}s")
    print(f"Latencia med={tiempos[len(tiempos)//2]:.2f}ms p99={tiempos[int(0.99*len(tiempos))]:.2f}ms max={tiempos[-1]:.2f}ms | invalidas={tot_inv}")
    print(f"Manos con perdida >= 25bb: {len(perdidas)}")

    por_etapa = Counter(p["ultima"][0] for p in perdidas)
    por_accion = Counter(p["ultima"][1] for p in perdidas)
    print("Por etapa de ultima decision:", dict(por_etapa))
    print("Por ultima accion:", dict(por_accion))

    print("\nTop 15 mayores perdidas:")
    for i, p in enumerate(perdidas[:15], 1):
        et, tp, mto, mot, pig, pz = p["ultima"]
        print(f"  {i:2d}. {p['neto']:+7d} | mano=[{p['yo']}: {p['mano']}] | board=[{p['board']}] | {et}:{tp}({mto}) | {mot} | mostradas={p['mostradas']}")


if __name__ == "__main__":
    main()

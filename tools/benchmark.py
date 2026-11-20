"""Benchmark estadistico reproducible y comparador v8 vs v9 en paralelo.

Uso:
    python -m tools.benchmark --n-mesas 200 --seed 2026 --modo baseline
    python -m tools.benchmark --n-mesas 200 --seed 2026 --modo comparar
    python -m tools.benchmark --n-mesas 160 --seed 2026 --modo ablacion
"""

import argparse
import math
import multiprocessing as mp
import os
import random
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.bots_kit import KIT_BOTS, BotCambiante, BotExplotador
from tools.simulador_pf import PokalatroAdapter, jugar_mesa

ALL_KIT_NAMES = list(KIT_BOTS.keys())


def _generar_config_mesa(idx, base_seed, modo_rivales="kit"):
    rng = random.Random(base_seed * 100003 + idx * 7919)
    if modo_rivales == "kit":
        rivales = rng.sample(ALL_KIT_NAMES, 4)
    elif modo_rivales == "fuertes":
        rivales = ["darwin", "espia", "estadistico", "bandido"]
        rng.shuffle(rivales)
    elif modo_rivales == "adverso":
        rivales = ["darwin", "espia", "explotador", "cambiante"]
        rng.shuffle(rivales)
    elif modo_rivales == "duro":
        pool = ["darwin", "espia", "estadistico", "bandido", "novato", "explotador"]
        rivales = rng.sample(pool, 4)
    else:
        rivales = rng.sample(ALL_KIT_NAMES, 4)
    silla_hero = idx % 5
    seed_mesa = base_seed * 1000003 + idx * 997 + 13
    return {
        "idx": idx,
        "rivales": rivales,
        "silla_hero": silla_hero,
        "seed": seed_mesa,
    }


def _instanciar_rival(nombre):
    if nombre == "explotador":
        return BotExplotador()
    if nombre == "cambiante":
        return BotCambiante()
    return KIT_BOTS[nombre]()


def _correr_una_mesa(args):
    cfg, pkg_dir, label, ablate, stack_inicial, max_manos = args
    rivales = cfg["rivales"]
    silla_hero = cfg["silla_hero"]
    bots = []
    r_idx = 0
    for s in range(5):
        if s == silla_hero:
            bots.append(("hero", PokalatroAdapter(pkg_dir, label=label, ablate=ablate)))
        else:
            rname = rivales[r_idx]
            bots.append((f"r{r_idx}_{rname}", _instanciar_rival(rname)))
            r_idx += 1

    res = jugar_mesa(
        bots,
        max_manos=max_manos,
        stack_inicial=stack_inicial,
        ciega_chica=50,
        ciega_grande=100,
        seed=cfg["seed"],
    )
    pos_hero = next(p for p in res["posiciones"] if p["id"] == "hero")
    return {
        "idx": cfg["idx"],
        "silla": silla_hero,
        "rivales": rivales,
        "manos_jugadas": res["manos_jugadas"],
        "puesto": pos_hero["puesto"],
        "neto": pos_hero["neto"],
        "saldo": pos_hero["saldo"],
        "quiebra": pos_hero["quiebra"],
        "timeouts": pos_hero["timeouts"],
        "invalidas": pos_hero["invalidas"],
        "tiempos_ms": pos_hero["tiempos_ms"],
        "posiciones": [
            {k: p[k] for k in ("id", "nombre", "puesto", "neto", "quiebra")}
            for p in res["posiciones"]
        ],
    }


def resumir(resultados, titulo=""):
    n = len(resultados)
    netos = [r["neto"] for r in resultados]
    puestos = [r["puesto"] for r in resultados]
    quiebras = sum(1 for r in resultados if r["quiebra"])
    top1 = sum(1 for p in puestos if p == 1)
    top3 = sum(1 for p in puestos if p <= 3)
    timeouts = sum(r["timeouts"] for r in resultados)
    invalidas = sum(r["invalidas"] for r in resultados)

    media = statistics.mean(netos)
    mediana = statistics.median(netos)
    sd = statistics.stdev(netos) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 0 else 0.0
    ic95 = 1.96 * se

    todos_ms = []
    for r in resultados:
        todos_ms.extend(r["tiempos_ms"])
    med_ms = statistics.median(todos_ms) if todos_ms else 0.0
    avg_ms = statistics.mean(todos_ms) if todos_ms else 0.0
    max_ms = max(todos_ms) if todos_ms else 0.0
    p95_ms = sorted(todos_ms)[int(0.95 * len(todos_ms))] if todos_ms else 0.0

    por_silla = {}
    for s in range(5):
        sub = [r["neto"] for r in resultados if r["silla"] == s]
        if sub:
            por_silla[s] = statistics.mean(sub)

    por_rival = {}
    for r in resultados:
        for rname in set(r["rivales"]):
            por_rival.setdefault(rname, []).append(r["neto"])
    rival_means = {k: statistics.mean(v) for k, v in sorted(por_rival.items())}

    # Stats globales de rivales
    bot_tot = {}
    for r in resultados:
        for p in r["posiciones"]:
            bname = p["nombre"]
            st = bot_tot.setdefault(bname, {"mesas": 0, "neto": 0, "top1": 0, "quiebras": 0})
            st["mesas"] += 1
            st["neto"] += p["neto"]
            if p["puesto"] == 1:
                st["top1"] += 1
            if p["quiebra"]:
                st["quiebras"] += 1

    resumen = {
        "titulo": titulo,
        "mesas": n,
        "top1_pct": 100.0 * top1 / n,
        "top1": top1,
        "top3_pct": 100.0 * top3 / n,
        "media": media,
        "ic95": ic95,
        "mediana": mediana,
        "total_fichas": sum(netos),
        "quiebra_pct": 100.0 * quiebras / n,
        "timeouts": timeouts,
        "invalidas": invalidas,
        "avg_ms": avg_ms,
        "med_ms": med_ms,
        "p95_ms": p95_ms,
        "max_ms": max_ms,
        "por_silla": por_silla,
        "por_rival": rival_means,
        "tabla_bots": bot_tot,
    }
    return resumen


def imprimir_resumen(s):
    print(f"\n=== {s['titulo']} ({s['mesas']} mesas) ===")
    print(
        f"1er puesto: {s['top1']}/{s['mesas']} ({s['top1_pct']:.1f}%) | "
        f"Top-3: {s['top3_pct']:.1f}% | Quiebras: {s['quiebra_pct']:.1f}%"
    )
    print(
        f"Neto medio: {s['media']:+.1f} +/- {s['ic95']:.1f} (IC95%) | "
        f"Mediana: {s['mediana']:+.1f} | Acumulado: {s['total_fichas']:+,}"
    )
    print(
        f"Latencia ms -> avg: {s['avg_ms']:.2f} | med: {s['med_ms']:.2f} | "
        f"p95: {s['p95_ms']:.2f} | max: {s['max_ms']:.2f} | "
        f"Timeouts: {s['timeouts']} | Invalidas: {s['invalidas']}"
    )
    print("Por silla:", " | ".join(f"S{k}:{v:+.0f}" for k, v in sorted(s["por_silla"].items())))
    print("Neto hero vs rival presente:", " | ".join(f"{k}:{v:+.0f}" for k, v in s["por_rival"].items()))


def comparar_pareado(res_a, res_b, nombre_a="v8", nombre_b="v9"):
    diffs = [b["neto"] - a["neto"] for a, b in zip(res_a, res_b)]
    n = len(diffs)
    md = statistics.mean(diffs)
    sd = statistics.stdev(diffs) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 0 else 0.0
    ic = 1.96 * se
    t_stat = md / se if se > 0 else 0.0
    # Aproximacion normal/t de dos colas
    p_val = math.erfc(abs(t_stat) / math.sqrt(2.0))
    top1_a = sum(1 for r in res_a if r["puesto"] == 1)
    top1_b = sum(1 for r in res_b if r["puesto"] == 1)
    q_a = sum(1 for r in res_a if r["quiebra"])
    q_b = sum(1 for r in res_b if r["quiebra"])
    print(f"\n>>> COMPARACION PAREADA {nombre_b} vs {nombre_a} ({n} mesas identicas) <<<")
    print(f"Delta neto medio: {md:+.1f} +/- {ic:.1f} (IC95%) | t={t_stat:.2f}, p={p_val:.4f}")
    print(f"1er puesto: {nombre_a} {top1_a}/{n} ({100*top1_a/n:.1f}%) -> {nombre_b} {top1_b}/{n} ({100*top1_b/n:.1f}%)")
    print(f"Quiebras:   {nombre_a} {q_a}/{n} ({100*q_a/n:.1f}%) -> {nombre_b} {q_b}/{n} ({100*q_b/n:.1f}%)")
    return {"delta": md, "ic95": ic, "t": t_stat, "p": p_val, "top1_a": top1_a, "top1_b": top1_b}


def ejecutar_tanda(pkg_dir, label, n_mesas=200, base_seed=2026, modo_rivales="kit",
                   ablate=None, workers=None, stack_inicial=10000, max_manos=50):
    cfgs = [_generar_config_mesa(i, base_seed, modo_rivales) for i in range(n_mesas)]
    tareas = [(c, pkg_dir, label, ablate, stack_inicial, max_manos) for c in cfgs]
    workers = workers or max(1, min(mp.cpu_count() - 1, 10))
    if workers <= 1:
        return [_correr_una_mesa(t) for t in tareas]
    with mp.Pool(processes=workers) as pool:
        return pool.map(_correr_una_mesa, tareas)


def _correr_torneo_10_rondas(args):
    """Simula la clasificatoria real de PokerFight: 10 rondas de 50 manos,
    mesas de 5 con rotacion de rivales, conservacion de fichas entre rondas y
    eliminacion en 0. Hero mantiene su objeto Estadisticas durante todo el torneo.
    """
    idx_torneo, base_seed, pkg_dir, label = args[:4]
    ablate = args[4] if len(args) > 4 else None
    rng = random.Random(base_seed * 7919 + idx_torneo * 101)
    # Pool de 15 bots: 1 hero + 14 rivales (copias de los 8 del kit + explotador)
    pool_nombres = (ALL_KIT_NAMES * 2)[:14]
    participantes = [("hero", PokalatroAdapter(pkg_dir, label=label, ablate=ablate))]
    for i, nm in enumerate(pool_nombres):
        participantes.append((f"bot{i:02d}_{nm}", _instanciar_rival(nm)))

    stacks = {pid: 10000 for pid, _ in participantes}
    vivos = [pid for pid, _ in participantes]
    # Iniciar bots una sola vez para persistencia entre rondas
    for s_idx, (pid, bot) in enumerate(participantes):
        bot.iniciar(pid, seed=base_seed * 1009 + idx_torneo * 97 + s_idx)

    eliminado_en_ronda = {}
    for ronda in range(1, 11):
        vivos = [pid for pid in vivos if stacks[pid] > 0]
        if len(vivos) <= 1 or "hero" not in vivos:
            break
        orden = list(vivos)
        rng.shuffle(orden)
        # Armar mesas de 5 (o resto en ultima mesa)
        mesas = [orden[i:i + 5] for i in range(0, len(orden), 5)]
        if len(mesas) >= 2 and len(mesas[-1]) < 3:
            # Rebalancear ultimas dos mesas
            ult = mesas.pop()
            for x in ult:
                mesas[-1].append(x)

        bot_map = dict(participantes)
        for m_idx, grupo in enumerate(mesas):
            if "hero" not in grupo and len(grupo) < 2:
                continue
            bots_mesa = [(pid, bot_map[pid]) for pid in grupo]
            res = jugar_mesa(
                bots_mesa,
                max_manos=50,
                stack_inicial=10000,
                ciega_chica=50,
                ciega_grande=100,
                seed=base_seed * 100003 + idx_torneo * 1000 + ronda * 37 + m_idx,
                reponer_stack=False,
                stacks_iniciales=stacks,
                iniciar_bots=False,
            )
            for p in res["posiciones"]:
                stacks[p["id"]] = p["saldo"]
                if p["saldo"] == 0 and p["id"] not in eliminado_en_ronda:
                    eliminado_en_ronda[p["id"]] = ronda

    # Clasificacion final de los 15 participantes (top 6 pasan a la final)
    ranking = sorted(
        [pid for pid, _ in participantes],
        key=lambda pid: (stacks[pid], eliminado_en_ronda.get(pid, 99)),
        reverse=True,
    )
    puesto_hero = ranking.index("hero") + 1
    return {
        "idx": idx_torneo,
        "puesto": puesto_hero,
        "clasifico_top6": puesto_hero <= 6,
        "primero": puesto_hero == 1,
        "saldo": stacks["hero"],
        "neto": stacks["hero"] - 10000,
        "quiebra": stacks["hero"] == 0,
        "timeouts": 0,
        "invalidas": participantes[0][1].invalidas,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--n-mesas", type=int, default=200)
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--modo", default="comparar", choices=["baseline", "comparar", "ablacion", "estres", "torneo10"])
    p.add_argument("--rivales", default="kit", choices=["kit", "fuertes", "adverso", "duro"])
    p.add_argument("--workers", type=int, default=0)
    args = p.parse_args()

    t0 = time.perf_counter()
    v8_dir = os.path.join(ROOT, "backup_v8")
    v9_dir = os.path.join(ROOT, "app")

    if args.modo == "baseline":
        res = ejecutar_tanda(v8_dir, "pokalatro_v8", args.n_mesas, args.seed, args.rivales, workers=args.workers)
        s = resumir(res, f"Pokalatro v8 ({args.rivales})")
        imprimir_resumen(s)
    elif args.modo == "comparar":
        res_v8 = ejecutar_tanda(v8_dir, "pokalatro_v8", args.n_mesas, args.seed, args.rivales, workers=args.workers)
        res_v9 = ejecutar_tanda(v9_dir, "pokalatro_v9", args.n_mesas, args.seed, args.rivales, workers=args.workers)
        imprimir_resumen(resumir(res_v8, f"Pokalatro v8 ({args.rivales})"))
        imprimir_resumen(resumir(res_v9, f"Pokalatro v9 ({args.rivales})"))
        comparar_pareado(res_v8, res_v9, "v8", "v9")
    elif args.modo == "estres":
        for mr in ("fuertes", "adverso", "duro"):
            res_v8 = ejecutar_tanda(v8_dir, "pokalatro_v8", args.n_mesas, args.seed, mr, workers=args.workers)
            res_v9 = ejecutar_tanda(v9_dir, "pokalatro_v9", args.n_mesas, args.seed, mr, workers=args.workers)
            imprimir_resumen(resumir(res_v8, f"v8 [{mr}]"))
            imprimir_resumen(resumir(res_v9, f"v9 [{mr}]"))
            comparar_pareado(res_v8, res_v9, f"v8[{mr}]", f"v9[{mr}]")
    elif args.modo == "torneo10":
        workers = args.workers or max(1, min(mp.cpu_count() - 1, 10))
        with mp.Pool(processes=workers) as pool:
            r8 = pool.map(_correr_torneo_10_rondas, [(i, args.seed, v8_dir, "v8") for i in range(args.n_mesas)])
            r9 = pool.map(_correr_torneo_10_rondas, [(i, args.seed, v9_dir, "v9") for i in range(args.n_mesas)])
        for nom, rr in (("v8", r8), ("v9", r9)):
            n = len(rr)
            top1 = sum(1 for x in rr if x["primero"])
            top6 = sum(1 for x in rr if x["clasifico_top6"])
            q = sum(1 for x in rr if x["quiebra"])
            m = statistics.mean(x["neto"] for x in rr)
            ic = 1.96 * (statistics.stdev(x["neto"] for x in rr) / math.sqrt(n)) if n > 1 else 0.0
            print(f"\n=== Torneo 10 rondas ({nom}, {n} torneos) ===")
            print(f"1er lugar: {top1}/{n} ({100*top1/n:.1f}%) | Clasifica Top-6: {top6}/{n} ({100*top6/n:.1f}%) | Quiebras: {q}/{n} ({100*q/n:.1f}%) | Neto: {m:+.1f} +/- {ic:.1f}")
        comparar_pareado(r8, r9, "v8_torneo10", "v9_torneo10")
    elif args.modo == "ablacion":
        base = ejecutar_tanda(v9_dir, "v9_full", args.n_mesas, args.seed, args.rivales, workers=args.workers)
        imprimir_resumen(resumir(base, "v9 Completo"))
        ablaciones = [
            ("sin_bayes", {"no_bayes": True}),
            ("sin_blueprint", {"no_blueprint": True}),
            ("sin_explotacion", {"no_exploit": True}),
            ("sin_subjuego", {"no_subgame": True}),
            ("sin_torneo", {"no_tournament": True}),
        ]
        for nom, cfg_ab in ablaciones:
            ra = ejecutar_tanda(v9_dir, f"v9_{nom}", args.n_mesas, args.seed, args.rivales, ablate=cfg_ab, workers=args.workers)
            imprimir_resumen(resumir(ra, f"v9 {nom}"))
            comparar_pareado(base, ra, "v9_full", nom)

    print(f"\nTiempo total benchmark: {time.perf_counter() - t0:.1f}s")


if __name__ == "__main__":
    main()

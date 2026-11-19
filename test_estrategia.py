"""Pruebas: evaluador, preflop, postflop y garantia de acciones validas.

    python -m unittest -v
"""

import random
import unittest

import motor
from estrategia import accion_segura, decidir, validar
from motor import a_enteros, equity, evaluar
from stats import Estadisticas
from turno import Turno


def C(texto):
    """'As Kh' -> lista de cartas del protocolo."""
    out = []
    for x in texto.split():
        rango = {"T": 10, "J": 11, "Q": 12, "K": 13, "A": 14}.get(x[0]) or int(x[0])
        out.append({"rango": rango, "palo": "cdhs".index(x[1])})
    return out


class ConexionFalsa:
    def __init__(self, id_jugador="yo", silla=0):
        self.id_jugador = id_jugador
        self.silla = silla


def hacer_turno(mano, comunes="", validas=("fold", "call", "raise", "allin"), apuesta_actual=100,
                mi_apuesta=0, saldo=10000, pozo=150, etapa=None, historial=(), rivales=1,
                subida_minima=100, saldo_rival=10000):
    comunitarias = C(comunes) if comunes else []
    etapa = etapa or {0: "preflop", 3: "flop", 4: "turn", 5: "river"}[len(comunitarias)]
    jugadores = [{"id": "yo", "saldo": saldo, "apuesta_ronda": mi_apuesta, "activo": True, "en_torneo": True,
                  "allin": False, "en_mano": True, "posicion_silla": 0}]
    for i in range(rivales):
        jugadores.append({"id": f"r{i}", "saldo": saldo_rival, "apuesta_ronda": apuesta_actual, "activo": True,
                          "en_torneo": True, "allin": False, "en_mano": True, "posicion_silla": i + 1})
    mensaje = {"tipo": "solicitar_accion", "acciones_validas": list(validas), "timeout_ms": 5000,
               "estado": {"id_mano": "m1", "etapa": etapa, "comunitarias": comunitarias, "pozo": pozo,
                          "apuesta_actual": apuesta_actual, "subida_minima": subida_minima,
                          "ciega_chica": 50, "ciega_grande": 100, "posicion_boton": 0,
                          "jugadores": jugadores, "historial_acciones": list(historial)}}
    return Turno(ConexionFalsa(), mensaje, C(mano), Estadisticas())


class Evaluador(unittest.TestCase):
    def ev(self, texto):
        return evaluar(a_enteros(C(texto)))

    def test_orden_de_categorias(self):
        manos = ["As Ks Qs Js Ts 2c 3d", "9h 9d 9s 9c 2c 3d 4h", "Ah Ad As Kc Kd 2c 3d", "2h 5h 9h Jh Kh 3c 4d",
                 "Ah 2d 3s 4c 5h 9c Kd", "Kh Kd Ks 2c 7d 9c 4h", "Ah Ad Kc Kd 2c 3d 7h", "Ah Ad 5c 6d 9c Jd 2h",
                 "Ah Kd 5c 6d 9c Jd 2h"]
        valores = [self.ev(m) for m in manos]
        self.assertEqual(valores, sorted(valores, reverse=True))

    def test_desempates(self):
        self.assertGreater(self.ev("Ah Ad Kc Kd 2c 3d 7h"), self.ev("Ah Ad Qc Qd 2c 3d 7h"))
        self.assertGreater(self.ev("6h 2d 3s 4c 5h 9c Kd"), self.ev("Ah 2d 3s 4c 5h 9c Kd"))   # escalera a 6 > rueda
        self.assertEqual(self.ev("Ah 2d 3s 4c 5h 9c Kd"), self.ev("Ah 2d 3s 4c 5h 9c 8d"))
        self.assertGreater(self.ev("Ah Ad Ac Kd Kc 2d 7h"), self.ev("Kh Kd Kc Ad Ac 2d 7h"))

    def test_equity_razonable(self):
        self.assertAlmostEqual(equity(a_enteros(C("As Ad")), [], [1.0], presupuesto_s=0.3), 0.85, delta=0.04)
        self.assertAlmostEqual(equity(a_enteros(C("7c 2d")), [], [1.0], presupuesto_s=0.3), 0.35, delta=0.04)


class Preflop(unittest.TestCase):
    def test_basura_con_check_gratis_no_se_retira(self):
        t = hacer_turno("7c 2d", validas=("check", "bet", "allin"), apuesta_actual=100, mi_apuesta=100)
        accion, _ = decidir(t)
        self.assertEqual(accion["tipo"], "check")

    def test_premium_abre_a_3_ciegas_o_mas(self):
        t = hacer_turno("As Ad", validas=("fold", "call", "raise", "allin"), apuesta_actual=100,
                        rivales=3, historial=[])
        accion, _ = decidir(t)
        self.assertEqual(accion["tipo"], "raise")
        self.assertGreaterEqual(accion["monto"], 300)

    def test_basura_ante_subida_grande_fold(self):
        hist = [{"id_jugador": "r0", "etapa": "preflop", "accion": {"tipo": "raise", "monto": 600}}]
        t = hacer_turno("7c 2d", apuesta_actual=600, historial=hist, pozo=750)
        accion, _ = decidir(t)
        self.assertEqual(accion["tipo"], "fold")

    def test_premium_ante_subida_resube_o_all_in(self):
        hist = [{"id_jugador": "r0", "etapa": "preflop", "accion": {"tipo": "raise", "monto": 300}}]
        t = hacer_turno("Ks Kd", apuesta_actual=300, historial=hist, pozo=450)
        accion, _ = decidir(t)
        self.assertIn(accion["tipo"], ("raise", "allin"))

    def test_stack_corto_usa_allin_no_raise_corto(self):
        t = hacer_turno("As Ks", validas=("fold", "call", "allin"), apuesta_actual=100, saldo=600, pozo=150)
        accion, _ = decidir(t)
        self.assertIn(accion["tipo"], ("allin", "fold", "call"))
        self.assertNotEqual(accion["tipo"], "raise")


class Postflop(unittest.TestCase):
    def test_monstruo_apuesta(self):
        t = hacer_turno("Ah Ad", "As 7c 2d", validas=("check", "bet", "allin"), apuesta_actual=0, mi_apuesta=0,
                        pozo=600)
        accion, _ = decidir(t)
        self.assertEqual(accion["tipo"], "bet")

    def test_nada_sin_apuesta_no_se_retira(self):
        random.seed(1)
        t = hacer_turno("7c 2d", "As Kd 9h", validas=("check", "bet", "allin"), apuesta_actual=0, pozo=600,
                        rivales=2)
        accion, _ = decidir(t)
        self.assertEqual(accion["tipo"], "check")

    def test_nada_ante_apuesta_grande_fold(self):
        t = hacer_turno("7c 2d", "As Kd 9h", apuesta_actual=900, pozo=1500)
        accion, _ = decidir(t)
        self.assertEqual(accion["tipo"], "fold")


class RangosEnLaMesa(unittest.TestCase):
    def test_pareja_de_la_mesa_no_cuenta_como_nuestra(self):
        # Q2 en 3-2-8-6-6: evaluar() dice doble par, pero lo nuestro es una pareja de 2.
        self.assertEqual(motor.categoria_propia(a_enteros(C("Qd 2c")), a_enteros(C("3s 2h 8c 6h 6s"))),
                         motor.PAR)
        self.assertEqual(motor.categoria_propia(a_enteros(C("7d 2c")), a_enteros(C("Ks Kh 8c"))),
                         motor.CARTA_ALTA)

    def test_resubidas_estrechan_por_fuerza_en_la_mesa(self):
        mano, mesa = a_enteros(C("As Kh")), a_enteros(C("8h Ah 5c 8s"))
        por_percentil = equity(mano, mesa, [0.40 * 0.125], presupuesto_s=0.3)
        por_mesa = equity(mano, mesa, [motor.Rango(0.40, conserva=0.125)], presupuesto_s=0.3)
        self.assertLess(por_mesa, por_percentil - 0.25)

    def test_pasar_descarta_sus_mejores_manos(self):
        mano, mesa = a_enteros(C("Qs Jd")), a_enteros(C("Qh 7c 2d"))
        sin_info = equity(mano, mesa, [motor.Rango(0.40)], presupuesto_s=0.3)
        tras_pasar = equity(mano, mesa, [motor.Rango(0.40, techo=0.25)], presupuesto_s=0.3)
        self.assertGreater(tras_pasar, sin_info)

    def test_resubida_en_el_river_no_se_paga_con_pareja(self):
        hist = [{"id_jugador": "yo", "etapa": "flop", "accion": {"tipo": "bet", "monto": 300}},
                {"id_jugador": "r0", "etapa": "flop", "accion": {"tipo": "call"}},
                {"id_jugador": "yo", "etapa": "turn", "accion": {"tipo": "bet", "monto": 800}},
                {"id_jugador": "r0", "etapa": "turn", "accion": {"tipo": "call"}},
                {"id_jugador": "yo", "etapa": "river", "accion": {"tipo": "bet", "monto": 2000}},
                {"id_jugador": "r0", "etapa": "river", "accion": {"tipo": "raise", "monto": 8000}}]
        t = hacer_turno("Ks 5c", "9c Kh 8d 9h 6c", apuesta_actual=8000, mi_apuesta=2000,
                        pozo=12500, historial=hist, saldo=7000)
        accion, motivo = decidir(t)
        self.assertEqual(accion["tipo"], "fold", motivo)


class AjustesServidor(unittest.TestCase):
    def test_paso_y_despues_apuesta_no_descarta_monstruos(self):
        from rangos import rango_rival
        hist = [{"id_jugador": "r0", "etapa": "flop", "accion": {"tipo": "check"}},
                {"id_jugador": "r0", "etapa": "turn", "accion": {"tipo": "check"}},
                {"id_jugador": "r0", "etapa": "river", "accion": {"tipo": "bet", "monto": 900}}]
        t = hacer_turno("As Ad", "Ks 7d 2c 9h 4s", apuesta_actual=900, pozo=1800, historial=hist)
        self.assertEqual(rango_rival(t, t.rivales[0]).techo, 0.0)

    def test_rival_que_apuesta_mucho_el_river_tiene_mas_faroles(self):
        from rangos import rango_rival
        hist = [{"id_jugador": "r0", "etapa": "river", "accion": {"tipo": "bet", "monto": 900}}]
        t = hacer_turno("As Kd", "Ks 7d 2c 9h 4s", apuesta_actual=900, pozo=1800, historial=hist)
        normal = rango_rival(t, t.rivales[0]).farol
        st = t.stats.de("r0")
        st.rios, st.apuestas_rio = 20, 16          # aposto en 16 de 20 rivers
        self.assertGreater(rango_rival(t, t.rivales[0]).farol, normal + 0.2)


class Seguridad(unittest.TestCase):
    def test_excepcion_en_estrategia_devuelve_accion_segura(self):
        import estrategia
        original = estrategia.decidir_preflop
        estrategia.decidir_preflop = lambda *a, **k: 1 / 0
        try:
            t = hacer_turno("As Ad", validas=("fold", "call", "raise", "allin"))
            accion, _ = decidir(t)
            self.assertEqual(accion["tipo"], "fold")
            t = hacer_turno("As Ad", validas=("check", "bet", "allin"), apuesta_actual=0)
            accion, _ = decidir(t)
            self.assertEqual(accion["tipo"], "check")
        finally:
            estrategia.decidir_preflop = original

    def test_accion_siempre_valida_y_en_rango(self):
        """Fuzz: miles de situaciones al azar; la accion debe ser legal."""
        rnd = random.Random(7)
        mazo = list(range(52))
        for _ in range(1500):
            cartas = rnd.sample(mazo, 7)
            n_com = rnd.choice([0, 3, 4, 5])
            mano = cartas[:2]
            comunes = cartas[2:2 + n_com]
            def a_txt(c):
                return "23456789TJQKA"[c >> 2] + "cdhs"[c & 3]
            apuesta = rnd.choice([0, 100, 300, 1000, 4000, 9000])
            mi_ap = rnd.choice([0, 50, 100]) if apuesta else 0
            mi_ap = min(mi_ap, apuesta)
            saldo = rnd.choice([300, 1500, 5000, 10000])
            hay_apuesta = apuesta > mi_ap
            if hay_apuesta:
                validas = ["fold", "allin"] + (["call"] if saldo > apuesta - mi_ap else [])
                if saldo + mi_ap >= apuesta + 100:
                    validas.append("raise")
            else:
                validas = ["check", "allin", "bet"]
            t = hacer_turno(" ".join(a_txt(c) for c in mano), " ".join(a_txt(c) for c in comunes),
                            validas=validas, apuesta_actual=apuesta, mi_apuesta=mi_ap, saldo=saldo,
                            pozo=rnd.choice([150, 600, 3000]), rivales=rnd.choice([1, 2, 4]))
            accion, _ = decidir(t)
            self.assertIn(accion["tipo"], validas, (validas, accion))
            if accion["tipo"] in ("bet", "raise"):
                self.assertGreaterEqual(accion["monto"], t.subida_minima)
                self.assertLess(accion["monto"], t.techo)


if __name__ == "__main__":
    unittest.main()

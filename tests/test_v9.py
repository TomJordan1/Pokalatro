"""Pruebas automatizadas completas para Pokalatro v9 (unidades + TCP protocolo).

    python -m unittest test_v9 -v
"""

import json
import os
import random
import socket
import sys
import threading
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP = os.path.join(_ROOT, "app")
_TESTS = os.path.dirname(os.path.abspath(__file__))
for _p in (_APP, _TESTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import abstraccion
import blueprint_data
import motor
import rangos
import stats
import subjuego
import torneo
from bot import jugar
from conexion import Conexion
from estrategia import decidir
from test_estrategia import C, ConexionFalsa, hacer_turno
from turno import Turno


class CorreccionesV8(unittest.TestCase):
    def test_posicion_exacta_con_eliminados(self):
        # Sillas 0(BTN), 2(SB), 3(BB), 4(UTG) -> silla 1 eliminada
        jugs = [
            {"id": "j0", "saldo": 10000, "apuesta_ronda": 0, "activo": True, "en_mano": True, "posicion_silla": 0},
            {"id": "j1", "saldo": 0, "apuesta_ronda": 0, "activo": False, "en_mano": False, "posicion_silla": 1},
            {"id": "j2", "saldo": 10000, "apuesta_ronda": 50, "activo": True, "en_mano": True, "posicion_silla": 2},
            {"id": "yo", "saldo": 9900, "apuesta_ronda": 100, "activo": True, "en_mano": True, "posicion_silla": 3},
            {"id": "j4", "saldo": 10000, "apuesta_ronda": 0, "activo": True, "en_mano": True, "posicion_silla": 4},
        ]
        msg = {
            "tipo": "solicitar_accion",
            "acciones_validas": ["check", "raise", "allin", "fold"],
            "timeout_ms": 5000,
            "estado": {
                "id_mano": "m_pos", "etapa": "preflop", "comunitarias": [],
                "pozo": 150, "apuesta_actual": 100, "subida_minima": 100,
                "ciega_chica": 50, "ciega_grande": 100, "posicion_boton": 0,
                "jugadores": jugs, "historial_acciones": [],
            },
        }
        t = Turno(ConexionFalsa("yo", 3), msg, C("As Kd"), stats.Estadisticas())
        self.assertTrue(t.es_bb)
        self.assertEqual(t.detras(), 0)  # BB habla ultimo preflop, no primero!
        self.assertTrue(t.hablo_ultimo)

    def test_pot_odds_descuenta_exceso_no_igualable(self):
        # Yo tengo 1000 saldo, pozo ya tiene 8500 (rival aposto 8000 sobre 500)
        t = hacer_turno("As Kd", "Ah 7c 2d", apuesta_actual=8000, mi_apuesta=0, saldo=1000, pozo=8500)
        # Costo real=1000, exceso no igualable=7000, pozo final=8500-7000+1000=2500 -> 1000/2500 = 0.40
        self.assertAlmostEqual(t.pot_odds, 0.40, places=4)

    def test_proyecto_escalera_comunitario_no_da_bonus(self):
        # Mesa 9-8-7-6 tiene proyecto abierto para cualquiera; 2c 3d no aporta nada
        board = motor.a_enteros(C("9c 8d 7h 6s"))
        made, draw, _ = abstraccion.clasificar_mano_postflop(motor.a_enteros(C("2c 3d")), board)
        self.assertEqual(draw, 0)
        puntajes = motor.puntajes_en_mesa(board)
        c2, c3 = motor.a_enteros(C("2c 3d"))
        # 2c 3d debe estar muy abajo (sin +0.15 fantasma)
        self.assertLess(puntajes[(min(c2, c3), max(c2, c3))], 0.25)

    def test_ranking_jugabilidad_ako_y_aqs_sobre_99(self):
        ako = abstraccion.percentil_jugabilidad(*motor.a_enteros(C("As Kd")))
        aqs = abstraccion.percentil_jugabilidad(*motor.a_enteros(C("As Qs")))
        nines = abstraccion.percentil_jugabilidad(*motor.a_enteros(C("9s 9d")))
        self.assertLess(ako, nines)
        self.assertLess(aqs, nines)

    def test_preflop_no_paga_100bb_allin_con_77(self):
        # Si alguien abre all-in 100bb, 77 no debe pagar automaticamente como si fuera 3bet contra rango ancho
        hist = [{"id_jugador": "r0", "etapa": "preflop", "accion": {"tipo": "allin"}}]
        t = hacer_turno("7s 7d", validas=("fold", "call", "allin"), apuesta_actual=10000,
                        mi_apuesta=0, saldo=10000, pozo=10150, historial=hist)
        st = t.stats.de("r0")
        st.manos, st.manos_eff, st.showdowns, st.suma_pct = 20, 20.0, 5, 2.0  # tight/normal
        acc, _ = decidir(t)
        self.assertEqual(acc["tipo"], "fold")


class ModeloBayesianoYBlueprint(unittest.TestCase):
    def test_rango_bayesiano_distingue_loco_de_nit(self):
        hist = [{"id_jugador": "r0", "etapa": "preflop", "accion": {"tipo": "allin"}}]
        t_loco = hacer_turno("As 9d", "Ks 7d 2c", apuesta_actual=10000, pozo=10150, historial=hist)
        st_l = t_loco.stats.de("r0")
        st_l.manos, st_l.manos_eff, st_l.vpip, st_l.apuestas, st_l.pagos = 25, 25.0, 22.0, 30.0, 5.0
        r_loco = rangos.rango_rival(t_loco, t_loco.rivales[0])

        t_nit = hacer_turno("As 9d", "Ks 7d 2c", apuesta_actual=10000, pozo=10150, historial=hist)
        st_n = t_nit.stats.de("r0")
        st_n.manos, st_n.manos_eff, st_n.vpip, st_n.apuestas, st_n.pagos = 25, 25.0, 4.0, 4.0, 12.0
        r_nit = rangos.rango_rival(t_nit, t_nit.rivales[0])

        eq_vs_loco = motor.equity(t_loco.mano, t_loco.board, [r_loco], max_muestras=1500)
        eq_vs_nit = motor.equity(t_nit.mano, t_nit.board, [r_nit], max_muestras=1500)
        self.assertGreater(eq_vs_loco, eq_vs_nit + 0.15)

    def test_showdown_bb_check_no_contamina_rango(self):
        est = stats.Estadisticas()
        hist = [
            {"id_jugador": "yo", "etapa": "preflop", "accion": {"tipo": "call"}},
            {"id_jugador": "r0", "etapa": "preflop", "accion": {"tipo": "check"}},
        ]
        est.registrar_showdown({"r0": C("7c 2d")}, "yo", lambda c: 0.99, historial=hist)
        self.assertEqual(est.de("r0").showdowns, 0)

    def test_blueprint_tablas_validas(self):
        for k, (push_t, call_t) in blueprint_data.PUSH_FOLD.items():
            self.assertEqual(len(push_t), 169)
            self.assertEqual(len(call_t), 169)
            self.assertGreater(push_t[0], 0.95)  # AA siempre push
        for k, probs in blueprint_data.POSTFLOP_BP.items():
            self.assertEqual(len(probs), 3)
            self.assertAlmostEqual(sum(probs), 1.0, delta=0.02)

    def test_subjuego_river_hu_apuesta_nuts_y_foldea_aire_facing_bet(self):
        t_nuts = hacer_turno("As Ks", "Qs Js Ts 2c 3d", validas=("check", "bet", "allin"),
                             apuesta_actual=0, pozo=1200)
        r_obj = rangos.rango_rival(t_nuts, t_nuts.rivales[0])
        res = subjuego.resolver_river_hu(t_nuts, r_obj, 0.99, 6, False, False, False)
        self.assertIsNotNone(res)
        self.assertEqual(res[0], "bet")

        t_air = hacer_turno("4c 2d", "As Ks Qs Jc 9h", validas=("fold", "call", "raise", "allin"),
                            apuesta_actual=1500, pozo=3000)
        r_obj2 = rangos.rango_rival(t_air, t_air.rivales[0])
        res2 = subjuego.resolver_river_hu(t_air, r_obj2, 0.02, 0, False, False, False)
        self.assertIsNotNone(res2)
        self.assertEqual(res2[0], "fold")

    def test_torneo_push_fold_y_supervivencia(self):
        t = hacer_turno("As Td", saldo=800, pozo=150, rivales=1)
        pp, pc = torneo.probs_push_fold(t, abstraccion.indice_clase(*t.mano))
        self.assertGreater(pp, 0.8)


class ProtocoloTCPReal(unittest.TestCase):
    def test_sesion_tcp_completa_y_json_corrupto(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        recibidos = []

        def servidor():
            cli, _ = srv.accept()
            f = cli.makefile("r", encoding="utf-8")
            saludo = json.loads(f.readline())
            recibidos.append(saludo)
            cli.sendall((json.dumps({"tipo": "bienvenida", "version": "1.0.0", "id_jugador": "j-test", "silla": 0}) + "\n").encode())
            # Enviar linea corrupta + tipo desconocido (el protocolo exige ignorarlos)
            cli.sendall(b"{json_roto\n")
            cli.sendall((json.dumps({"tipo": "mensaje_futuro_ignorar", "x": 1}) + "\n").encode())
            estado = {
                "id_mano": "tcp-1", "etapa": "preflop", "comunitarias": [],
                "pozo": 150, "apuesta_actual": 100, "subida_minima": 100,
                "ciega_chica": 50, "ciega_grande": 100, "posicion_boton": 0,
                "jugadores": [
                    {"id": "j-test", "saldo": 9950, "apuesta_ronda": 50, "activo": True, "en_torneo": True, "en_mano": True, "allin": False, "posicion_silla": 0},
                    {"id": "r1", "saldo": 9900, "apuesta_ronda": 100, "activo": True, "en_torneo": True, "en_mano": True, "allin": False, "posicion_silla": 1},
                ],
                "historial_acciones": [],
            }
            cli.sendall((json.dumps({"tipo": "mano_inicio", "version": "1.0.0", "cartas": C("As Ad"), "estado": estado}) + "\n").encode())
            cli.sendall((json.dumps({"tipo": "solicitar_accion", "version": "1.0.0", "acciones_validas": ["fold", "call", "raise", "allin"], "timeout_ms": 5000, "estado": estado}) + "\n").encode())
            resp = json.loads(f.readline())
            recibidos.append(resp)
            cli.sendall((json.dumps({"tipo": "mano_fin", "version": "1.0.0", "resultado": {"id_mano": "tcp-1", "comunitarias": [], "repartos": [{"id_jugador": "j-test", "monto": 150}]}}) + "\n").encode())
            cli.close()
            srv.close()

        th = threading.Thread(target=servidor, daemon=True)
        th.start()

        con = Conexion(f"127.0.0.1:{port}", "tok-secreto")
        con.conectar(intentos=5, espera_s=0.05)
        cerro = jugar(con, stats.Estadisticas(), verboso=False)
        con.cerrar()
        th.join(timeout=3.0)

        self.assertTrue(cerro)
        self.assertEqual(recibidos[0]["tipo"], "saludo")
        self.assertEqual(recibidos[1]["tipo"], "accion")
        self.assertEqual(recibidos[1]["id_mano"], "tcp-1")
        self.assertIn(recibidos[1]["accion"]["tipo"], ("raise", "allin", "call"))

    def test_preflop_sin_zona_muerta_ante_4bet_y_river_no_farolea_aire_100pct(self):
        # 1. Ante 4-bet con compromiso en (0.32, 0.35] (por_igualar=3300 sobre techo=10000 -> 0.33),
        #    una mano fuerte (QQ) nunca puede caer en zona muerta y foldear.
        hist = [
            {"id_jugador": "r1", "etapa": "preflop", "accion": {"tipo": "raise", "monto": 300}},
            {"id_jugador": "yo", "etapa": "preflop", "accion": {"tipo": "raise", "monto": 900}},
            {"id_jugador": "r1", "etapa": "preflop", "accion": {"tipo": "raise", "monto": 4200}},
        ]
        t = hacer_turno(
            "Qs Qd", "", apuesta_actual=4200, mi_apuesta=900, pozo=5100, saldo=9100,
            validas=("fold", "call", "raise", "allin"), historial=hist,
        )
        acc, _ = decidir(t)
        self.assertIn(acc["tipo"], ("call", "allin", "raise"))

        # 2. En river HU sin apuesta pendiente, aire puro (eq 5%) no debe recibir bet determinista
        t_riv = hacer_turno(
            "7c 2d", "Ah Kh Qc Jd 9s", apuesta_actual=0, mi_apuesta=0, pozo=1000, saldo=8000,
            validas=("check", "bet", "allin"),
        )
        r = rangos.rango_rival(t_riv, t_riv.rivales[0])
        res = subjuego.resolver_river_hu(t_riv, r, 0.05, 0, False, False, False)
        self.assertIsNotNone(res)
        self.assertEqual(res[0], "check")


if __name__ == "__main__":
    unittest.main()

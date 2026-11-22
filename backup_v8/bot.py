#!/usr/bin/env python3
"""Pokalatro: bot de PokerFight (Texas Hold'em no-limit).

Uso:
    python bot.py -addr localhost:9000 -token mi-bot -verboso     # pruebas
    python bot.py -addr HOST:PUERTO -token $TOKEN -persistente    # torneo

El token se pasa SOLO por la linea de comandos, nunca dentro de un archivo.

Lo que hace este archivo es el bucle de mensajes: recibe, decide, responde,
y vuelve a conectarse si se corta la red. La estrategia esta en
estrategia.py, preflop.py y postflop.py.
"""

import argparse
import json
import os
import sys
import time

from conexion import Conexion, MesaRechazo, log
from estrategia import decidir
from motor import a_enteros, fuerza_vs_azar, percentil_mano
from stats import Estadisticas, cargar, guardar
from turno import Turno

NOMBRE = "Pokalatro"
VERSION = "v8"


def describir(accion):
    return f"{accion['tipo']} {accion['monto']}" if "monto" in accion else accion["tipo"]


def _fuerza(cartas, board):
    """Fuerza real de una mano mostrada en la mesa final (0..1)."""
    return fuerza_vs_azar(a_enteros(cartas[:2]), board)


def _percentil(cartas):
    """Percentil de una mano del protocolo, para aprender de los showdowns."""
    enteros = a_enteros(cartas[:2])
    return percentil_mano(enteros[0], enteros[1])


# Registro opcional de decisiones para analizar el bot (no afecta lo que juega).
# Solo se activa si existe la variable de entorno POKALATRO_LOG con una ruta.
_REGISTRO = os.environ.get("POKALATRO_LOG")
_MANOS = os.environ.get("POKALATRO_MANOS")        # historial de cada mano (para analizar)
_STATS = os.environ.get("POKALATRO_STATS")        # archivo donde se guarda lo aprendido


def _registrar(turno, accion, motivo):
    if not _REGISTRO or turno is None:
        return
    try:
        with open(_REGISTRO, "a", encoding="utf-8") as f:
            f.write(json.dumps({"mano": turno.estado.get("id_mano"), "etapa": turno.etapa,
                                "accion": accion.get("tipo"), "motivo": motivo,
                                "rivales": turno.rivales_en_mano}) + "\n")
    except OSError:
        pass


def jugar(conexion, estadisticas, verboso):
    """Atiende mensajes hasta que la mesa cierre.

    Devuelve True si la mesa cerro la conexion (fin normal de la partida) y
    False si se corto la red (hay que reconectar).
    """
    mano = []
    historial = []
    ultimo_estado = {}
    manos_jugadas = 0
    while True:
        try:
            mensaje = conexion.recibir()
        except (OSError, ValueError):
            return False
        if mensaje is None:
            return True

        tipo = mensaje.get("tipo")

        if tipo == "solicitar_accion":
            turno = None
            try:
                turno = Turno(conexion, mensaje, mano, estadisticas)
                accion, motivo = decidir(turno)
            except Exception as error:  # noqa: BLE001
                # Red de seguridad final: nunca dejar de responder un turno.
                validas = mensaje.get("acciones_validas") or []
                accion = {"tipo": "check"} if "check" in validas else {"tipo": "fold"}
                motivo = f"fallo grave: {error!r}"
            try:
                conexion.responder(mensaje.get("estado") or {}, accion)
            except OSError:
                return False
            ultimo_estado = mensaje.get("estado") or ultimo_estado
            historial = ultimo_estado.get("historial_acciones") or historial
            _registrar(turno, accion, motivo)
            if verboso and turno is not None:
                log(f"[{turno.etapa:7}] {describir(accion):12} | {motivo}")

        elif tipo == "mano_inicio":
            mano = mensaje.get("cartas") or []
            historial = []

        elif tipo == "estado":
            ultimo_estado = mensaje.get("estado") or ultimo_estado
            historial = ultimo_estado.get("historial_acciones") or historial

        elif tipo == "mano_fin":
            resultado = mensaje.get("resultado") or {}
            if _MANOS:
                try:
                    with open(_MANOS, "a", encoding="utf-8") as f:
                        f.write(json.dumps({"historial": historial, "resultado": resultado,
                                            "jugadores": ultimo_estado.get("jugadores"),
                                            "mano": mano}) + "\n")
                except OSError:
                    pass
            if verboso:
                ganadores = [(r.get("id_jugador"), r.get("monto")) for r in resultado.get("repartos") or []]
                log(f"[fin mano] ganan {ganadores}")
            try:
                estadisticas.registrar_mano(historial, conexion.id_jugador)
                # Las cartas mostradas en el showdown son la unica informacion
                # legitima sobre lo que realmente juega cada rival.
                board = a_enteros(resultado.get("comunitarias") or [])
                estadisticas.registrar_showdown(resultado.get("mostradas"),
                                                conexion.id_jugador, _percentil,
                                                board=board, historial=historial,
                                                fuerza=_fuerza)
            except Exception:  # noqa: BLE001
                pass
            historial = []
            manos_jugadas += 1
            if _STATS and manos_jugadas % 10 == 0:
                try:
                    guardar(estadisticas, _STATS)
                except OSError:
                    pass

        elif tipo == "error":
            log(f"{NOMBRE}: la mesa aviso un error: {mensaje.get('mensaje', '')}")

        # Un mensaje de tipo desconocido se ignora, como pide el protocolo.


def main():
    parser = argparse.ArgumentParser(description=f"{NOMBRE}: bot de PokerFight")
    parser.add_argument("-addr", default="localhost:9000", help="host:puerto de la mesa")
    parser.add_argument("-token", required=True, help="token del equipo")
    parser.add_argument("-verboso", action="store_true", help="imprime cada decision")
    parser.add_argument("-silencioso", action="store_true",
                        help="compatibilidad con la arena (es el comportamiento por defecto)")
    parser.add_argument("-persistente", action="store_true",
                        help="no se rinde nunca: reconecta siempre (usar el dia del torneo)")
    args = parser.parse_args()

    estadisticas = cargar(_STATS) if _STATS else Estadisticas()
    if _STATS and estadisticas.rivales:
        log(f"{NOMBRE}: recuerdo a {len(estadisticas.rivales)} rivales de partidas anteriores")
    conexion = Conexion(args.addr, args.token)
    try:
        conexion.conectar(intentos=60 if args.persistente else 20, espera_s=0.2)
    except MesaRechazo as error:
        log(f"{NOMBRE}: la mesa rechazo la conexion: {error}")
        sys.exit(1)
    except ConnectionError as error:
        if not args.persistente:
            log(f"{NOMBRE}: {error}")
            sys.exit(1)
        log(f"{NOMBRE}: {error}; sigo intentando")
    else:
        log(f"{NOMBRE} {VERSION} conectado como {conexion.id_jugador} (silla {conexion.silla})")

    fallos = 0
    while True:
        if conexion.socket is not None:
            cerro_la_mesa = jugar(conexion, estadisticas, args.verboso)
            conexion.cerrar()
            if cerro_la_mesa and not args.persistente:
                log(f"{NOMBRE}: la mesa cerro la partida")
                return
            log(f"{NOMBRE}: conexion perdida, vuelvo con el mismo token")
        fallos += 1
        if not args.persistente and fallos > 6:
            sys.exit(1)
        espera = 0.2
        while True:
            try:
                conexion.conectar(intentos=10, espera_s=espera)
                log(f"{NOMBRE}: reconectado como {conexion.id_jugador} "
                    f"(silla {conexion.silla})")
                fallos = 0
                break
            except MesaRechazo as error:
                log(f"{NOMBRE}: la mesa rechazo la reconexion: {error}")
                if not args.persistente:
                    sys.exit(1)
                time.sleep(5)
            except ConnectionError:
                if not args.persistente:
                    break
                espera = min(5.0, espera * 1.5)
                time.sleep(espera)


if __name__ == "__main__":
    main()

"""Conexion con la mesa: TCP + JSON Lines (protocolo 1.0.0).

Derivado de bots/novato/conexion.py del kit, con reintento ante mesa saturada,
TCP_NODELAY y keepalive.
"""

import json
import os
import random
import socket
import sys
import time

VERSION_PROTOCOLO = "1.0.0"
MESA_SATURADA = "demasiadas conexiones esperando el saludo"


class MesaRechazo(Exception):
    """La mesa rechazo el saludo (token invalido, version...). No reintentar."""


class Conexion:
    def __init__(self, direccion, token):
        host, puerto = direccion.rsplit(":", 1)
        self.host = host
        self.puerto = int(puerto)
        self.token = token
        self.id_jugador = token
        self.silla = -1
        self.socket = None
        self.entrada = None

    def conectar(self, intentos=20, espera_s=0.1):
        ultimo_error = None
        espera = espera_s
        for _ in range(intentos):
            try:
                self.socket = socket.create_connection((self.host, self.puerto), timeout=5)
                self.socket.settimeout(None)
                try:
                    self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                except OSError:
                    pass
                self.entrada = self.socket.makefile("r", encoding="utf-8", newline="\n")
                self._saludar()
                return
            except MesaRechazo as error:
                self.cerrar()
                if MESA_SATURADA in str(error):
                    ultimo_error = error
                    time.sleep(espera * (1 + random.random() * 0.5))
                    espera = min(2.0, espera * 2)
                    continue
                raise
            except (OSError, ValueError) as error:
                ultimo_error = error
                self.cerrar()
                time.sleep(espera)
                espera = min(2.0, espera * 1.5)
        raise ConnectionError(f"no se pudo conectar a {self.host}:{self.puerto}: {ultimo_error}")

    def _saludar(self):
        self.enviar({"tipo": "saludo", "version": VERSION_PROTOCOLO, "token": self.token})
        bienvenida = self.recibir()
        if bienvenida is None:
            raise OSError("la mesa cerro la conexion durante el saludo")
        if bienvenida.get("tipo") == "error":
            raise MesaRechazo(bienvenida.get("mensaje", "sin detalle"))
        if bienvenida.get("tipo") != "bienvenida":
            raise MesaRechazo(f"se esperaba bienvenida y llego {bienvenida.get('tipo')!r}")
        self.id_jugador = bienvenida.get("id_jugador") or self.token
        self.silla = bienvenida.get("silla", -1)

    def enviar(self, mensaje):
        self.socket.sendall((json.dumps(mensaje, separators=(",", ":")) + "\n").encode("utf-8"))

    def recibir(self):
        """Siguiente mensaje, o None si la mesa cerro (fin normal)."""
        while True:
            linea = self.entrada.readline()
            if not linea:
                return None
            linea = linea.strip()
            if not linea:
                continue
            try:
                return json.loads(linea)
            except ValueError:
                continue   # linea corrupta: se ignora

    def responder(self, estado, accion):
        self.enviar({
            "tipo": "accion",
            "version": VERSION_PROTOCOLO,
            "id_mano": estado.get("id_mano", ""),
            "accion": accion,
        })

    def cerrar(self):
        try:
            if self.entrada is not None:
                self.entrada.close()
        except OSError:
            pass
        try:
            if self.socket is not None:
                self.socket.close()
        except OSError:
            pass
        self.socket = None
        self.entrada = None


_ARCHIVO_LOG = os.environ.get("POKALATRO_TXT")


def log(texto):
    """Imprime en la terminal y, si POKALATRO_TXT apunta a un archivo, lo copia ahi."""
    print(texto, file=sys.stderr, flush=True)
    if _ARCHIVO_LOG:
        try:
            with open(_ARCHIVO_LOG, "a", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S ") + texto + "\n")
        except OSError:
            pass

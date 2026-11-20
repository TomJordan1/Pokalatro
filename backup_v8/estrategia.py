"""Despachador: elige preflop o postflop y GARANTIZA una accion valida.

Tres garantias, en este orden de importancia:
  1. Siempre se responde algo, y algo que esta en acciones_validas. Una
     accion invalida cuesta lo mismo que no responder.
  2. Si la estrategia falla o tarda, se devuelve la accion segura.
  3. La decision es reproducible: el azar se siembra con la mano y la
     situacion, asi que la misma situacion da siempre la misma jugada (util
     para probar) sin que un rival pueda predecirla.
"""

import random
import sys
import time
import traceback
import zlib

from postflop import decidir_postflop
from preflop import decidir_preflop


def accion_segura(t):
    """Lo que juega la mesa por nosotros si no respondemos: pasar si es
    gratis, retirarse si hay que pagar."""
    if t.puede("check"):
        return {"tipo": "check"}
    if t.puede("fold"):
        return {"tipo": "fold"}
    if t.puede("call"):
        return {"tipo": "call"}
    return {"tipo": "allin"} if t.puede("allin") else {"tipo": "fold"}


def validar(t, accion):
    """Ultimo filtro: deja pasar solo acciones que la mesa va a aceptar."""
    if not isinstance(accion, dict):
        return accion_segura(t)
    tipo = accion.get("tipo")
    if tipo not in t.validas:
        return accion_segura(t)
    if tipo in ("bet", "raise"):
        try:
            monto = int(accion.get("monto"))
        except (TypeError, ValueError):
            return accion_segura(t)
        monto = max(monto, t.subida_minima)
        if monto >= t.techo:
            # Llegar al techo es un all-in: declararlo asi para que la mesa
            # aplique la regla de all-in corto en vez de rechazar la subida.
            return {"tipo": "allin"} if t.puede("allin") else accion_segura(t)
        return {"tipo": tipo, "monto": monto}
    return {"tipo": tipo}


def _sembrar(t):
    """Azar reproducible por situacion, imposible de adivinar desde afuera."""
    clave = "%s|%s|%s|%s|%s" % (t.estado.get("id_mano", ""), t.etapa,
                                t.mano, t.apuesta_actual, len(t.historial))
    random.seed(zlib.crc32(clave.encode("utf-8")))


def decidir(t):
    """-> (accion, motivo). Nunca lanza una excepcion."""
    inicio = time.perf_counter()
    try:
        _sembrar(t)
        if len(t.comunitarias) < 3:
            accion, motivo = decidir_preflop(t, t.cartas)
        else:
            accion, motivo = decidir_postflop(t, t.cartas)
        accion = validar(t, accion)
    except Exception:  # noqa: BLE001 - el bot no se cae nunca por la estrategia
        traceback.print_exc(file=sys.stderr)
        accion, motivo = accion_segura(t), "error en la estrategia: accion segura"
    ms = (time.perf_counter() - inicio) * 1000
    return accion, f"{motivo} [{ms:.0f} ms]"

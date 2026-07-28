"""
Bitacora de cambios (auditoria): helper para registrar acciones destructivas
(edicion, eliminacion, cancelacion) en la tabla bitacora_cambios.

Uso tipico:

    from utils.bitacora import log_cambio, log_cambios_multiples

    # 1) Eliminacion o cancelacion (un solo registro)
    log_cambio(
        db, username,
        modulo="Bodega de materia prima", tabla="recepciones_mp",
        id_registro="MP-20260727-002", accion="eliminacion",
        motivo="Duplicado por error",
    )

    # 2) Edicion con multiples campos (varias entradas de bitacora, una por campo)
    log_cambios_multiples(
        db, username,
        modulo="Recepcion de pedidos", tabla="pedidos",
        id_registro="PED-25",
        cambios={
            "cantidad_kg": (100, 150),
            "fecha_entrega": ("2026-07-28", "2026-07-30"),
        },
        motivo="Cliente ajusto cantidades",
    )
"""
import datetime


def log_cambio(db, usuario, modulo, tabla, id_registro, accion,
               campo="", valor_antes="", valor_despues="", motivo=""):
    """Registra una accion destructiva en bitacora_cambios. Es tolerante a
    fallos: si el sheet no existe o falla el escribir, no rompe la app --
    solo devuelve False para que el flujo principal siga."""
    try:
        ahora = datetime.datetime.now()
        bitacora_id = db.siguiente_id("bitacora_cambios", "BIT", ahora.date())
        db.append_row("bitacora_cambios", {
            "bitacora_id": bitacora_id,
            "fecha_hora": ahora.strftime("%Y-%m-%d %H:%M:%S"),
            "usuario": str(usuario or ""),
            "modulo": str(modulo or ""),
            "tabla": str(tabla or ""),
            "id_registro": str(id_registro or ""),
            "accion": str(accion or ""),
            "campo": str(campo or ""),
            "valor_antes": str(valor_antes) if valor_antes is not None else "",
            "valor_despues": str(valor_despues) if valor_despues is not None else "",
            "motivo": str(motivo or ""),
        })
        return True
    except Exception:
        return False


def log_cambios_multiples(db, usuario, modulo, tabla, id_registro,
                          cambios: dict, motivo=""):
    """Registra multiples cambios de campos de un mismo registro. Cambios es
    un dict {campo: (valor_antes, valor_despues)}. Solo registra los que
    realmente cambiaron (antes != despues)."""
    n = 0
    for campo, tupla in cambios.items():
        antes, despues = tupla
        if str(antes) == str(despues):
            continue
        if log_cambio(
            db, usuario, modulo, tabla, id_registro,
            accion="edicion", campo=campo,
            valor_antes=antes, valor_despues=despues, motivo=motivo,
        ):
            n += 1
    return n

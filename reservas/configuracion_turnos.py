from datetime import date, time, timedelta

# Fecha de activación del nuevo sistema
NUEVO_SISTEMA_DESDE = date(2026, 10, 15)

# Capacidad del nuevo servicio de cena
CAPACIDAD_BARRA_CENA = 10
CAPACIDAD_MESA_CENA_ONLINE = 0

# Horarios de entrada y duración
TURNOS_CENA = (
    time(20, 10),
    time(21, 50),
)

DURACION_CENA = timedelta(minutes=90)


def nuevo_sistema_activo(fecha, servicio):
    return (
        fecha >= NUEVO_SISTEMA_DESDE
        and servicio == "cena"
    )


def capacidad_barra_para_fecha(fecha, servicio):
    if nuevo_sistema_activo(fecha, servicio):
        return CAPACIDAD_BARRA_CENA

    return 8


def capacidad_mesa_online_para_fecha(fecha, servicio):
    if nuevo_sistema_activo(fecha, servicio):
        return CAPACIDAD_MESA_CENA_ONLINE

    return 4
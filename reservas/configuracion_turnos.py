from datetime import date, time, timedelta

# Fecha de activación del nuevo sistema
NUEVO_SISTEMA_DESDE = date(2026, 10, 15)

# Capacidad del nuevo servicio de cena
CAPACIDAD_BARRA_CENA = 10

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

from datetime import time,timedelta,datetime,date

from django.db.models import Sum
from django.utils import timezone

from reservas.models import BloqueoDia, Reserva


CAPACIDAD_BARRA = 8
# Mesas combinables, pero con un máximo de 4 personas en mesa.
CAPACIDAD_MESA = 3
CAPACIDAD_TOTAL = CAPACIDAD_BARRA + CAPACIDAD_MESA
MAX_PERSONAS_RESERVA = 11

TURNOS_COMIDA = [time(13, 0)]
# Turno único de noche: entrada a las 20:00 y servicio hasta las 22:30.
TURNOS_CENA = [time(20, 0)]
ESTADOS_QUE_OCUPAN = ["confirmada", "llegado"]


def etiqueta_turno(hora):
    if hora == time(20, 0):
        return "20:00 - 22:30"
    return hora.strftime("%H:%M")


def obtener_turnos_para_fecha(fecha):
    dia = fecha.weekday()  # lunes=0, domingo=6

    # Cerrado por vacaciones/descanso: lunes y martes.
    if dia in [0, 1]:
        return []

    turnos = []

    if dia in [3, 4, 5, 6]:  # jueves a domingo
        turnos += TURNOS_COMIDA

    if dia in [2, 3, 4, 5]:  # miércoles a sábado
        turnos += TURNOS_CENA

    return turnos


def dia_bloqueado(fecha):
    return BloqueoDia.objects.filter(fecha=fecha).exists()


def reservas_que_ocupan():
    ahora = timezone.now()
    confirmadas = Reserva.objects.filter(estado__in=ESTADOS_QUE_OCUPAN)
    pendientes_vivas = Reserva.objects.filter(estado="pendiente_pago", expira_en__gt=ahora)
    return confirmadas | pendientes_vivas


def zona_permitida(personas, zona):
    # La reserva puede ser de hasta 12 personas en total.
    # La mesa admite como máximo 4 personas; para 5 o más solo se ofrece barra.
    if personas < 1 or personas > MAX_PERSONAS_RESERVA:
        return False
    if zona == "mesa":
        return personas <= CAPACIDAD_MESA
    if zona == "barra":
        return True
    return False


def capacidad_zona(zona):
    if zona == "barra":
        return CAPACIDAD_BARRA
    if zona == "mesa":
        return CAPACIDAD_MESA
    if zona == "completo":
        return CAPACIDAD_TOTAL
    return 0


def _total_queryset(qs):
    return qs.aggregate(total=Sum("personas")).get("total") or 0


def _rango_periodo(hora):
    periodo = periodo_de_hora(hora)

    if periodo == "comida":
        return time(13, 0), time(16, 0)

    if periodo == "cena":
        return time(20, 0), time(22, 30)

    return hora, hora


def reservas_del_mismo_periodo(fecha, hora):
    inicio, fin = _rango_periodo(hora)

    return reservas_que_ocupan().filter(
        fecha=fecha,
        hora__gte=inicio,
        hora__lte=fin,
    )


def total_personas_reservadas(fecha, hora):
    return _total_queryset(reservas_del_mismo_periodo(fecha, hora))


def personas_reservadas(fecha, hora, zona):
    qs = reservas_del_mismo_periodo(fecha, hora)

    if zona == "completo":
        return _total_queryset(qs)

    if qs.filter(zona="completo").exists():
        return capacidad_zona(zona)

    return _total_queryset(qs.filter(zona=zona))


def plazas_disponibles(fecha, hora, zona):
    if zona == "completo":
        return CAPACIDAD_TOTAL if total_personas_reservadas(fecha, hora) == 0 else 0

    return max(capacidad_zona(zona) - personas_reservadas(fecha, hora, zona), 0)


def plazas_disponibles_para_reserva(fecha, hora, personas, zona):
    # Para grupos de 5 o más se muestra solo barra, pero la disponibilidad
    # se calcula contra el aforo total: 8 en barra + 4 en mesa = 12.
    if zona == "barra" and personas > CAPACIDAD_MESA:
        return max(CAPACIDAD_TOTAL - total_personas_reservadas(fecha, hora), 0)
    return plazas_disponibles(fecha, hora, zona)

def hay_disponibilidad(fecha, hora, personas, zona):
    if dia_bloqueado(fecha):
        return False

    periodo = periodo_de_hora(hora)
    turnos_fecha = obtener_turnos_para_fecha(fecha)

    if periodo == "comida" and time(13, 0) not in turnos_fecha:
        return False

    if periodo == "cena" and time(20, 0) not in turnos_fecha:
        return False

    if not periodo:
        return False

    if not zona_permitida(personas, zona):
        return False

    return plazas_disponibles_para_reserva(fecha, hora, personas, zona) >= personas


def zonas_disponibles(fecha, hora, personas):
    zonas = []

    for zona, nombre in [
        ("barra", "Barra"),
        ("mesa", "Mesa"),
    ]:
        if hay_disponibilidad(fecha, hora, personas, zona):
            zonas.append({
                "id": zona,
                "nombre": nombre,
                "plazas_libres": plazas_disponibles_para_reserva(fecha, hora, personas, zona),
            })

    return zonas


def turnos_disponibles(fecha, personas):
    if dia_bloqueado(fecha):
        return []

    turnos = []
    for hora in obtener_turnos_para_fecha(fecha):
        zonas = zonas_disponibles(fecha, hora, personas)
        if zonas:
            turnos.append({"hora": hora, "etiqueta": etiqueta_turno(hora), "zonas": zonas})

    return turnos


def mapa_ocupacion(fecha):
    """Resumen para staff: muestra qué hay libre por turno sin crear una reserva."""
    if dia_bloqueado(fecha):
        return []

    turnos = []
    for hora in obtener_turnos_para_fecha(fecha):
        barra_ocupada = personas_reservadas(fecha, hora, "barra")
        mesa_ocupada = personas_reservadas(fecha, hora, "mesa")
        total_ocupado = total_personas_reservadas(fecha, hora)

        turnos.append({
            "hora": hora,
            "etiqueta": etiqueta_turno(hora),
            "barra_ocupada": barra_ocupada,
            "barra_total": CAPACIDAD_BARRA,
            "barra_libre": max(CAPACIDAD_BARRA - barra_ocupada, 0),
            "mesa_ocupada": mesa_ocupada,
            "mesa_total": CAPACIDAD_MESA,
            "mesa_libre": max(CAPACIDAD_MESA - mesa_ocupada, 0),
            "total_ocupado": total_ocupado,
            "total_capacidad": CAPACIDAD_TOTAL,
            "total_libre": max(CAPACIDAD_TOTAL - total_ocupado, 0),
        })
    return turnos


def periodo_de_hora(hora):
    if time(13, 0) <= hora <= time(16, 0):
        return "comida"
    if time(20, 0) <= hora <= time(22, 30):
        return "cena"
    return None


def horas_llegada_para_turno(turno):
    if turno == "comida":
        inicio = time(13, 0)
        fin = time(15, 30)
    elif turno == "cena":
        inicio = time(20, 0)
        fin = time(22, 30)
    else:
        return []

    horas = []
    actual = datetime.combine(date.today(), inicio)
    limite = datetime.combine(date.today(), fin)

    while actual <= limite:
        horas.append(actual.time())
        actual += timedelta(minutes=15)

    return horas

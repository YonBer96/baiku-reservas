from datetime import time

from django.db.models import Sum
from django.utils import timezone

from reservas.models import BloqueoDia, Reserva


CAPACIDAD_BARRA = 8
# Dos mesas que pueden unirse para grupos de hasta 6 personas.
CAPACIDAD_MESA = 6
CAPACIDAD_TOTAL = CAPACIDAD_BARRA + CAPACIDAD_MESA
MAX_PERSONAS_RESERVA = 12

TURNOS_COMIDA = [time(13, 0)]
TURNOS_CENA = [time(20, 0), time(22, 0)]
ESTADOS_QUE_OCUPAN = ["confirmada", "llegado"]


def obtener_turnos_para_fecha(fecha):
    dia = fecha.weekday()  # lunes=0, domingo=6

    # Cerrado por descanso: lunes y martes.
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
    # 1-6 personas pueden ir a barra o mesa.
    if 1 <= personas <= 6:
        return zona in ["barra", "mesa"]

    # 7-8 personas solo barra.
    if 7 <= personas <= 8:
        return zona == "barra"

    # 9-12 personas requieren reservar toda la capacidad disponible.
    if 9 <= personas <= 12:
        return zona == "completo"

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


def total_personas_reservadas(fecha, hora):
    return _total_queryset(reservas_que_ocupan().filter(fecha=fecha, hora=hora))


def personas_reservadas(fecha, hora, zona):
    qs = reservas_que_ocupan().filter(fecha=fecha, hora=hora)

    if zona == "completo":
        return _total_queryset(qs)

    if qs.filter(zona="completo").exists():
        return capacidad_zona(zona)

    return _total_queryset(qs.filter(zona=zona))


def plazas_disponibles(fecha, hora, zona):
    if zona == "completo":
        return CAPACIDAD_TOTAL if total_personas_reservadas(fecha, hora) == 0 else 0

    return max(capacidad_zona(zona) - personas_reservadas(fecha, hora, zona), 0)


def hay_disponibilidad(fecha, hora, personas, zona):
    if dia_bloqueado(fecha):
        return False
    if hora not in obtener_turnos_para_fecha(fecha):
        return False
    if not zona_permitida(personas, zona):
        return False
    return plazas_disponibles(fecha, hora, zona) >= personas


def zonas_disponibles(fecha, hora, personas):
    zonas = []

    for zona, nombre in [
        ("barra", "Barra"),
        ("mesa", "Mesa"),
        ("completo", "Restaurante completo"),
    ]:
        if hay_disponibilidad(fecha, hora, personas, zona):
            zonas.append({
                "id": zona,
                "nombre": nombre,
                "plazas_libres": plazas_disponibles(fecha, hora, zona),
            })

    return zonas


def turnos_disponibles(fecha, personas):
    if dia_bloqueado(fecha):
        return []

    turnos = []
    for hora in obtener_turnos_para_fecha(fecha):
        zonas = zonas_disponibles(fecha, hora, personas)
        if zonas:
            turnos.append({"hora": hora, "zonas": zonas})

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

from datetime import time, timedelta, datetime, date

from django.db.models import Sum
from django.utils import timezone

from reservas.models import BloqueoDia, Reserva


CAPACIDAD_BARRA = 8
# 2 mesas de 2 personas. Una reserva de 3 o 4 en mesa bloquea las 2 mesas.
CAPACIDAD_MESA = 4
CAPACIDAD_TOTAL = CAPACIDAD_BARRA + CAPACIDAD_MESA
MAX_PERSONAS_RESERVA = 12

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

    # Cerrado por descanso: lunes y martes.
    if dia in [0, 1]:
        return []

    turnos = []

    if dia in [3, 4, 5, 6]:  # jueves a domingo
        turnos += TURNOS_COMIDA

    if dia in [2, 3, 4, 5]:  # miércoles a sábado
        turnos += TURNOS_CENA

    return turnos


def obtener_turnos_reservables_para_fecha(fecha):
    """
    Devuelve los servicios que todavía se pueden reservar.
    Si es hoy, oculta solo los servicios que ya han empezado.
    """
    turnos = obtener_turnos_para_fecha(fecha)

    if fecha != timezone.localdate():
        return turnos

    ahora = timezone.localtime().time()
    return [turno for turno in turnos if turno > ahora]


def dia_bloqueado(fecha):
    return BloqueoDia.objects.filter(fecha=fecha).exists()


def reservas_que_ocupan(excluir_reserva_id=None):
    qs = Reserva.objects.filter(
        estado__in=ESTADOS_QUE_OCUPAN
    )

    if excluir_reserva_id:
        qs = qs.exclude(id=excluir_reserva_id)

    return qs


def zona_base(zona):
    if not zona:
        return zona

    if str(zona).startswith("combinada"):
        return "combinada"

    return zona


def datos_combinada(zona):
    """
    Convierte IDs de opción como 'combinada:8:3' en:
    {'personas_barra': 8, 'personas_mesa': 3}
    """
    if not zona or not str(zona).startswith("combinada:"):
        return None

    partes = str(zona).split(":")

    if len(partes) != 3:
        return None

    try:
        personas_barra = int(partes[1])
        personas_mesa = int(partes[2])
    except (TypeError, ValueError):
        return None

    return {
        "personas_barra": personas_barra,
        "personas_mesa": personas_mesa,
    }


def plazas_mesa_que_bloquea(personas_mesa):
    """
    Reglas reales de mesa:
    - 0 personas: no bloquea mesa.
    - 1 o 2 personas: bloquea 1 mesa = 2 plazas.
    - 3 o 4 personas: bloquea las 2 mesas = 4 plazas.
      El hueco sobrante de una mesa de 3 queda muerto y no se vende.
    """
    if personas_mesa <= 0:
        return 0

    if personas_mesa <= 2:
        return 2

    if personas_mesa <= 4:
        return 4

    return 999


def zona_permitida(personas, zona):
    zona = zona_base(zona)

    if personas < 1 or personas > MAX_PERSONAS_RESERVA:
        return False

    if zona == "mesa":
        return personas <= CAPACIDAD_MESA

    if zona == "barra":
        return personas <= CAPACIDAD_BARRA

    if zona == "combinada":
        return CAPACIDAD_BARRA < personas <= CAPACIDAD_TOTAL

    if zona == "completo":
        return personas <= CAPACIDAD_TOTAL

    return False


def capacidad_zona(zona):
    zona = zona_base(zona)

    if zona == "barra":
        return CAPACIDAD_BARRA

    if zona == "mesa":
        return CAPACIDAD_MESA

    if zona in ["combinada", "completo"]:
        return CAPACIDAD_TOTAL

    return 0


def _total_queryset(qs):
    return qs.aggregate(total=Sum("personas")).get("total") or 0


def periodo_de_hora(hora):
    if time(13, 0) <= hora <= time(16, 0):
        return "comida"

    if time(20, 0) <= hora <= time(22, 30):
        return "cena"

    return None


def _rango_periodo(hora):
    periodo = periodo_de_hora(hora)

    if periodo == "comida":
        return time(13, 0), time(16, 0)

    if periodo == "cena":
        return time(20, 0), time(22, 30)

    return hora, hora


def reservas_del_mismo_periodo(fecha, hora, excluir_reserva_id=None):
    inicio, fin = _rango_periodo(hora)

    return reservas_que_ocupan(excluir_reserva_id).filter(
        fecha=fecha,
        hora__gte=inicio,
        hora__lte=fin,
    )


def total_personas_reservadas(fecha, hora):
    return _total_queryset(reservas_del_mismo_periodo(fecha, hora))


def _ocupacion_reserva(reserva):
    """
    Devuelve ocupación real de una reserva:
    - barra: personas reales en barra.
    - mesa_real: plazas de mesa bloqueadas.
    - mesa_personas: personas reales sentadas en mesa.

    Compatible con reservas antiguas que no tenían personas_barra/personas_mesa.
    """
    zona = zona_base(reserva.zona)

    if zona == "barra":
        return {
            "barra": reserva.personas,
            "mesa_real": 0,
            "mesa_personas": 0,
        }

    if zona == "mesa":
        return {
            "barra": 0,
            "mesa_real": plazas_mesa_que_bloquea(reserva.personas),
            "mesa_personas": reserva.personas,
        }

    if zona == "combinada":
        personas_barra = getattr(reserva, "personas_barra", 0) or 0
        personas_mesa = getattr(reserva, "personas_mesa", 0) or 0

        # Fallback para combinadas antiguas guardadas sin distribución.
        if personas_barra + personas_mesa != reserva.personas:
            personas_barra = min(reserva.personas, CAPACIDAD_BARRA)
            personas_mesa = max(reserva.personas - personas_barra, 0)

        return {
            "barra": personas_barra,
            "mesa_real": plazas_mesa_que_bloquea(personas_mesa),
            "mesa_personas": personas_mesa,
        }

    if zona == "completo":
        return {
            "barra": CAPACIDAD_BARRA,
            "mesa_real": CAPACIDAD_MESA,
            "mesa_personas": CAPACIDAD_MESA,
        }

    return {
        "barra": 0,
        "mesa_real": 0,
        "mesa_personas": 0,
    }


def personas_reservadas(fecha, hora, zona, excluir_reserva_id=None):
    zona = zona_base(zona)
    qs = reservas_del_mismo_periodo(fecha, hora, excluir_reserva_id)

    if qs.filter(zona="completo").exists():
        return capacidad_zona(zona)

    if zona == "barra":
        return sum(_ocupacion_reserva(reserva)["barra"] for reserva in qs)

    if zona == "mesa":
        return sum(_ocupacion_reserva(reserva)["mesa_real"] for reserva in qs)

    if zona == "completo":
        return _total_queryset(qs)

    return _total_queryset(qs.filter(zona=zona))


def plazas_disponibles(fecha, hora, zona, excluir_reserva_id=None):
    zona = zona_base(zona)

    if zona == "completo":
        return CAPACIDAD_TOTAL

    return max(
        capacidad_zona(zona) - personas_reservadas(fecha, hora, zona, excluir_reserva_id),
        0
    )


def plazas_disponibles_para_reserva(fecha, hora, personas, zona, excluir_reserva_id=None):
    zona = zona_base(zona)

    if zona == "combinada":
        return (
            plazas_disponibles(fecha, hora, "barra", excluir_reserva_id)
            + plazas_disponibles(fecha, hora, "mesa", excluir_reserva_id)
        )

    return plazas_disponibles(fecha, hora, zona, excluir_reserva_id)


def hay_disponibilidad(fecha, hora, personas, zona, excluir_reserva_id=None):
    if dia_bloqueado(fecha):
        return False

    if fecha == timezone.localdate():
        ahora = timezone.localtime().time()
        if hora <= ahora:
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

    zona_normalizada = zona_base(zona)

    if zona_normalizada == "mesa":
        return plazas_disponibles(fecha, hora, "mesa", excluir_reserva_id) >= plazas_mesa_que_bloquea(personas)

    if zona_normalizada == "barra":
        return plazas_disponibles(fecha, hora, "barra", excluir_reserva_id) >= personas

    if zona_normalizada == "combinada":
        datos = datos_combinada(zona)

        if not datos:
            return False

        personas_barra = datos["personas_barra"]
        personas_mesa = datos["personas_mesa"]

        if personas_barra + personas_mesa != personas:
            return False

        if personas_barra < 0 or personas_mesa < 0:
            return False

        if personas_barra > CAPACIDAD_BARRA or personas_mesa > CAPACIDAD_MESA:
            return False

        mesa_necesaria = plazas_mesa_que_bloquea(personas_mesa)

        return (
            plazas_disponibles(fecha, hora, "barra", excluir_reserva_id) >= personas_barra
            and plazas_disponibles(fecha, hora, "mesa", excluir_reserva_id) >= mesa_necesaria
        )

    if zona_normalizada == "completo":
        return plazas_disponibles(fecha, hora, "completo", excluir_reserva_id) >= personas

    return False


def combinaciones_posibles(fecha, hora, personas, excluir_reserva_id=None):
    """
    Para grupos de 9 a 12, devuelve combinaciones reales:
    - 8 barra + 1 mesa
    - 7 barra + 2 mesa
    - 6 barra + 3 mesa
    - 5 barra + 4 mesa
    etc., según disponibilidad real.

    Las mesas se bloquean según regla:
    1/2 en mesa bloquea 2 plazas.
    3/4 en mesa bloquea 4 plazas.
    """
    barra_libre = plazas_disponibles(fecha, hora, "barra", excluir_reserva_id)
    mesa_libre = plazas_disponibles(fecha, hora, "mesa", excluir_reserva_id)

    combinaciones = []

    max_barra = min(personas, barra_libre, CAPACIDAD_BARRA)

    for personas_barra in range(0, max_barra + 1):
        personas_mesa = personas - personas_barra

        if personas_mesa <= 0:
            continue

        if personas_mesa > CAPACIDAD_MESA:
            continue

        mesa_bloqueada = plazas_mesa_que_bloquea(personas_mesa)

        if mesa_bloqueada <= mesa_libre:
            combinaciones.append({
                "personas_barra": personas_barra,
                "personas_mesa": personas_mesa,
                "mesa_bloqueada": mesa_bloqueada,
            })

    # Primero las opciones más naturales: máxima barra, mínima mesa.
    combinaciones.sort(key=lambda c: (-c["personas_barra"], c["personas_mesa"]))

    return combinaciones


def zonas_disponibles(fecha, hora, personas, excluir_reserva_id=None):
    opciones = []

    if personas <= 2:
        zonas = [
            ("barra", "Barra", ""),
            ("mesa", "Mesa", ""),
        ]

    elif personas <= 4:
        zonas = [
            ("barra", "Barra", ""),
            ("mesa", "Mesas juntas", ""),
        ]

    elif personas <= 8:
        zonas = [
            ("barra", "Barra", ""),
        ]

    else:
        combinaciones = combinaciones_posibles(fecha, hora, personas, excluir_reserva_id)

        for combinacion in combinaciones:
            personas_barra = combinacion["personas_barra"]
            personas_mesa = combinacion["personas_mesa"]
            mesa_bloqueada = combinacion["mesa_bloqueada"]

            descripcion = f"{personas_barra} en barra + {personas_mesa} en mesa"

            if personas_mesa == 1:
                descripcion += " · se reserva 1 mesa"
            elif personas_mesa == 2:
                descripcion += " · se reserva 1 mesa"
            elif personas_mesa == 3:
                descripcion += " · se ocupan 2 mesas"
            elif personas_mesa == 4:
                descripcion += " · se ocupan 2 mesas"

            zona_id = f"combinada:{personas_barra}:{personas_mesa}"

            if hay_disponibilidad(fecha, hora, personas, zona_id, excluir_reserva_id):
                opciones.append({
                    "id": zona_id,
                    "nombre": "Barra + mesas",
                    "descripcion": descripcion,
                    "plazas_libres": plazas_disponibles_para_reserva(fecha, hora, personas, zona_id, excluir_reserva_id),
                    "personas_barra": personas_barra,
                    "personas_mesa": personas_mesa,
                    "mesa_bloqueada": mesa_bloqueada,
                })

        return opciones

    for zona, nombre, descripcion in zonas:
        if hay_disponibilidad(fecha, hora, personas, zona, excluir_reserva_id):
            opciones.append({
                "id": zona,
                "nombre": nombre,
                "descripcion": descripcion,
                "plazas_libres": plazas_disponibles(fecha, hora, zona, excluir_reserva_id),
            })

    return opciones


def turnos_disponibles(fecha, personas):
    if dia_bloqueado(fecha):
        return []

    turnos = []

    for hora in obtener_turnos_reservables_para_fecha(fecha):
        zonas = zonas_disponibles(fecha, hora, personas)

        if zonas:
            turnos.append({
                "hora": hora,
                "etiqueta": etiqueta_turno(hora),
                "zonas": zonas,
            })

    return turnos


def mapa_ocupacion(fecha):
    """Resumen para staff: muestra qué hay libre por turno sin crear una reserva."""
    if dia_bloqueado(fecha):
        return []

    turnos = []

    for hora in obtener_turnos_para_fecha(fecha):
        barra_ocupada = personas_reservadas(fecha, hora, "barra")
        mesa_ocupada = personas_reservadas(fecha, hora, "mesa")
        total_ocupado = barra_ocupada + mesa_ocupada

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

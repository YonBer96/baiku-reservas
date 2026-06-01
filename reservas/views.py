from datetime import date, datetime, timedelta, time

import logging

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import BloqueoDia, Reserva
from .services.disponibilidad import (
    CAPACIDAD_BARRA,
    CAPACIDAD_MESA,
    CAPACIDAD_TOTAL,
    MAX_PERSONAS_RESERVA,
    hay_disponibilidad,
    mapa_ocupacion,
    turnos_disponibles,
    zonas_disponibles,
    horas_llegada_para_turno,
    periodo_de_hora,
    resumen_calendario_dia,
    zona_base,
    datos_combinada,
)


IMPORTE_ANTICIPO = 1000  # 10€ total por reserva, en céntimos
MINUTOS_EXPIRACION_PAGO = 15
logger = logging.getLogger(__name__)

PASOS_RESERVA = {
    "fecha": {"numero": 1, "total": 7, "titulo": "Fecha"},
    "personas": {"numero": 2, "total": 7, "titulo": "Personas"},
    "turno": {"numero": 3, "total": 7, "titulo": "Servicio"},
    "hora": {"numero": 4, "total": 7, "titulo": "Hora"},
    "zona": {"numero": 5, "total": 7, "titulo": "Zona"},
    "datos": {"numero": 6, "total": 7, "titulo": "Datos"},
    "resumen": {"numero": 7, "total": 7, "titulo": "Confirmar"},
}


def calendario_reservas_api(request):
    """Devuelve próximos días con estado para calendario visual."""
    inicio_txt = request.GET.get("inicio")
    dias_txt = request.GET.get("dias", "90")

    try:
        fecha_inicio = _fecha_desde_texto(inicio_txt) if inicio_txt else date.today()
    except (TypeError, ValueError):
        fecha_inicio = date.today()

    try:
        dias = min(max(int(dias_txt), 1), 90)
    except (TypeError, ValueError):
        dias = 45

    datos = []
    for offset in range(dias):
        fecha = fecha_inicio + timedelta(days=offset)
        resumen = resumen_calendario_dia(fecha)
        datos.append({
            "fecha": fecha.isoformat(),
            "dia": fecha.day,
            "nombre": fecha.strftime("%a"),
            "estado": resumen["estado"],
            "vacantes": resumen["vacantes"],
            "total": resumen["total"],
            "bloqueado": resumen["bloqueado"],
        })

    return JsonResponse({"dias": datos})


def inicio(request):
    return render(request, "reservas/inicio.html")


def obtener_reserva_session(request):
    return request.session.get("reserva", {})


def guardar_reserva_session(request, datos):
    reserva = obtener_reserva_session(request)
    reserva.update(datos)
    request.session["reserva"] = reserva
    request.session.modified = True


def _fecha_desde_texto(fecha_txt):
    return datetime.strptime(fecha_txt, "%Y-%m-%d").date()


def _hora_desde_texto(hora_txt):
    return datetime.strptime(hora_txt, "%H:%M").time()


def _zona_nombre(zona):
    zona_normalizada = zona_base(zona)
    return dict(Reserva.ZONA_CHOICES).get(zona_normalizada, zona_normalizada)


def _normalizar_zona_para_guardar(zona, personas):
    """
    Convierte la opción elegida en el formulario a datos guardables en BD.

    Ejemplo:
    - "combinada:8:3" -> zona="combinada", personas_barra=8, personas_mesa=3
    - "barra" -> zona="barra", personas_barra=personas, personas_mesa=0
    - "mesa" -> zona="mesa", personas_barra=0, personas_mesa=personas
    """
    zona_normalizada = zona_base(zona)

    if zona_normalizada == "combinada":
        datos = datos_combinada(zona)

        if not datos:
            # Fallback conservador para datos antiguos.
            personas_barra = min(personas, CAPACIDAD_BARRA)
            personas_mesa = max(personas - personas_barra, 0)
        else:
            personas_barra = datos["personas_barra"]
            personas_mesa = datos["personas_mesa"]

        descripcion = f"Barra + mesas · {personas_barra} en barra + {personas_mesa} en mesa"

        return {
            "zona": "combinada",
            "personas_barra": personas_barra,
            "personas_mesa": personas_mesa,
            "zona_nombre": descripcion,
        }

    if zona_normalizada == "barra":
        return {
            "zona": "barra",
            "personas_barra": personas,
            "personas_mesa": 0,
            "zona_nombre": "Barra",
        }

    if zona_normalizada == "mesa":
        return {
            "zona": "mesa",
            "personas_barra": 0,
            "personas_mesa": personas,
            "zona_nombre": "Mesa" if personas <= 2 else "Mesas juntas",
        }

    return {
        "zona": zona_normalizada,
        "personas_barra": 0,
        "personas_mesa": 0,
        "zona_nombre": _zona_nombre(zona_normalizada),
    }


def _enviar_email_seguro(funcion_email, reserva):
    """Envía email sin romper la reserva si Gmail/SMTP falla."""
    if not reserva.email or reserva.email == "staff@baiku.local":
        return False

    try:
        funcion_email(reserva)
        return True
    except Exception as exc:
        logger.exception("Error enviando email para la reserva %s: %s", reserva.id, exc)
        return False


def _motivo_fecha_no_reservable(fecha):
    """Devuelve un texto claro si la fecha no se puede reservar."""
    if fecha.weekday() in [0, 1]:
        return "Estamos de vacaciones lunes y martes."

    bloqueo = BloqueoDia.objects.filter(fecha=fecha).first()
    if bloqueo:
        motivo = bloqueo.motivo or "día no disponible"
        return f"Día bloqueado: {motivo}"

    return ""


def reserva_fecha(request):
    reserva = obtener_reserva_session(request)

    if request.method == "POST":
        fecha_txt = request.POST.get("fecha")

        try:
            fecha = _fecha_desde_texto(fecha_txt)
        except (TypeError, ValueError):
            messages.error(request, "Fecha no válida.")
            return redirect("reserva_fecha")

        if fecha < date.today():
            messages.error(request, "No puedes reservar una fecha pasada.")
            return redirect("reserva_fecha")

        motivo_no_reservable = _motivo_fecha_no_reservable(fecha)
        if motivo_no_reservable:
            messages.error(request, motivo_no_reservable)
            return redirect("reserva_fecha")

        # Se permite reservar el mismo día mientras queden horas futuras disponibles.
        # La validación fina se hace en el paso de hora y en hay_disponibilidad().

        request.session["reserva"] = {"fecha": fecha_txt}
        request.session.modified = True

        return redirect("reserva_personas")

    return render(
        request,
        "reservas/reserva_fecha.html",
        {
            "reserva": reserva,
            "paso": PASOS_RESERVA["fecha"],
            "hoy": date.today(),
        },
    )


def reserva_personas(request):
    reserva = obtener_reserva_session(request)

    if not reserva.get("fecha"):
        return redirect("reserva_fecha")

    fecha = _fecha_desde_texto(reserva["fecha"])
    motivo_no_reservable = _motivo_fecha_no_reservable(fecha)
    if fecha < date.today() or motivo_no_reservable:
        messages.error(request, motivo_no_reservable or "No puedes reservar una fecha pasada.")
        return redirect("reserva_fecha")

    if request.method == "POST":
        try:
            personas = int(request.POST.get("personas", 0))
        except (TypeError, ValueError):
            personas = 0

        if personas < 1 or personas > MAX_PERSONAS_RESERVA:
            messages.error(request, f"Elige entre 1 y {MAX_PERSONAS_RESERVA} personas.")
            return redirect("reserva_personas")

        turnos = turnos_disponibles(fecha, personas)
        if not turnos:
            messages.error(request, "No hay turnos disponibles para esa fecha y número de personas.")
            return redirect("reserva_personas")

        guardar_reserva_session(request, {"personas": personas})
        return redirect("reserva_turno")

    return render(
        request,
        "reservas/reserva_personas.html",
        {"reserva": reserva, "paso": PASOS_RESERVA["personas"], "max_personas": MAX_PERSONAS_RESERVA},
    )


def reserva_turno(request):
    reserva = obtener_reserva_session(request)

    if not reserva.get("fecha"):
        return redirect("reserva_fecha")
    if not reserva.get("personas"):
        return redirect("reserva_personas")

    fecha = _fecha_desde_texto(reserva["fecha"])
    personas = int(reserva["personas"])
    turnos = turnos_disponibles(fecha, personas)

    servicios_disponibles = []
    for turno in turnos:
        if turno["hora"] == time(13, 0):
            servicios_disponibles.append({"id": "comida", "nombre": "Comida", "descripcion": "Servicio mediodía"})
        elif turno["hora"] == time(20, 0):
            servicios_disponibles.append({"id": "cena", "nombre": "Cena", "descripcion": "Servicio noche"})

    if request.method == "POST":
        turno_tipo = request.POST.get("turno")
        servicios_validos = [servicio["id"] for servicio in servicios_disponibles]

        if turno_tipo not in servicios_validos:
            messages.error(request, "Ese servicio ya no está disponible.")
            return redirect("reserva_turno")

        reserva.pop("hora", None)
        reserva.pop("zona", None)
        reserva.pop("zona_opcion", None)
        reserva.pop("zona_nombre", None)
        reserva.pop("personas_barra", None)
        reserva.pop("personas_mesa", None)
        reserva["turno"] = turno_tipo
        request.session["reserva"] = reserva
        request.session.modified = True
        return redirect("reserva_hora")

    return render(
        request,
        "reservas/reserva_turno.html",
        {
            "reserva": reserva,
            "servicios": servicios_disponibles,
            "paso": PASOS_RESERVA["turno"],
        },
    )


def reserva_hora(request):
    reserva = obtener_reserva_session(request)

    if not reserva.get("fecha"):
        return redirect("reserva_fecha")
    if not reserva.get("personas"):
        return redirect("reserva_personas")
    if not reserva.get("turno"):
        return redirect("reserva_turno")

    fecha = _fecha_desde_texto(reserva["fecha"])
    personas = int(reserva["personas"])
    turno_tipo = reserva["turno"]

    # Solo mostramos horas que todavía son válidas y con disponibilidad.
    horas = [
        h for h in horas_llegada_para_turno(fecha, turno_tipo)
        if zonas_disponibles(fecha, h, personas)
    ]

    if request.method == "POST":
        hora_txt = request.POST.get("hora")

        try:
            hora = _hora_desde_texto(hora_txt)
        except (TypeError, ValueError):
            messages.error(request, "Hora no válida.")
            return redirect("reserva_hora")

        if hora not in horas:
            messages.error(request, "Esa hora no pertenece al servicio elegido.")
            return redirect("reserva_hora")

        if not zonas_disponibles(fecha, hora, personas):
            messages.error(request, "No hay disponibilidad para esa hora.")
            return redirect("reserva_hora")

        guardar_reserva_session(request, {"hora": hora_txt})
        return redirect("reserva_zona")

    return render(
        request,
        "reservas/reserva_hora.html",
        {
            "reserva": reserva,
            "horas": horas,
            "turno": turno_tipo,
            "paso": PASOS_RESERVA["hora"],
        },
    )

def reserva_zona(request):
    reserva = obtener_reserva_session(request)

    if not reserva.get("turno"):
        return redirect("reserva_turno")
    if not reserva.get("hora"):
        return redirect("reserva_hora")

    fecha = _fecha_desde_texto(reserva["fecha"])
    hora = _hora_desde_texto(reserva["hora"])
    personas = int(reserva["personas"])
    zonas = zonas_disponibles(fecha, hora, personas)

    if request.method == "POST":
        zona = request.POST.get("zona")

        if not hay_disponibilidad(fecha, hora, personas, zona):
            messages.error(request, "Esa zona ya no está disponible.")
            return redirect("reserva_zona")

        datos_zona = _normalizar_zona_para_guardar(zona, personas)
        datos_zona["zona_opcion"] = zona

        guardar_reserva_session(request, datos_zona)
        return redirect("reserva_datos")

    return render(
        request,
        "reservas/reserva_zona.html",
        {"reserva": reserva, "zonas": zonas, "paso": PASOS_RESERVA["zona"]},
    )


def reserva_datos(request):
    reserva = obtener_reserva_session(request)

    if not reserva.get("zona"):
        return redirect("reserva_zona")

    if request.method == "POST":
        nombre = request.POST.get("nombre", "").strip()
        email = request.POST.get("email", "").strip()
        telefono = request.POST.get("telefono", "").strip()
        notas = request.POST.get("notas", "").strip()

        if not nombre or not email or not telefono:
            messages.error(request, "Completa nombre, email y teléfono.")
            return redirect("reserva_datos")

        guardar_reserva_session(
            request,
            {"nombre": nombre, "email": email, "telefono": telefono, "notas": notas},
        )
        return redirect("reserva_resumen")

    return render(
        request,
        "reservas/reserva_datos.html",
        {"reserva": reserva, "paso": PASOS_RESERVA["datos"]},
    )


def reserva_resumen(request):
    reserva = obtener_reserva_session(request)
    campos = ["personas", "fecha", "hora", "zona", "nombre", "email", "telefono"]

    if not all(reserva.get(campo) for campo in campos):
        return redirect("reserva_fecha")

    reserva["zona_nombre"] = reserva.get("zona_nombre") or _zona_nombre(reserva["zona"])

    return render(
        request,
        "reservas/reserva_resumen.html",
        {
            "reserva": reserva,
            "importe": IMPORTE_ANTICIPO,
            "importe_euros": IMPORTE_ANTICIPO // 100,
            "paso": PASOS_RESERVA["resumen"],
        },
    )


@require_POST
def crear_pago_stripe(request):
    reserva_data = obtener_reserva_session(request)
    campos = ["personas", "fecha", "hora", "zona", "nombre", "email", "telefono"]

    if not all(reserva_data.get(campo) for campo in campos):
        return redirect("reserva_fecha")

    if not settings.STRIPE_SECRET_KEY:
        messages.error(request, "Stripe no está configurado todavía.")
        return redirect("reserva_resumen")

    fecha = _fecha_desde_texto(reserva_data["fecha"])
    hora = _hora_desde_texto(reserva_data["hora"])
    personas = int(reserva_data["personas"])
    zona = reserva_data["zona"]
    zona_opcion = reserva_data.get("zona_opcion", zona)
    datos_zona = _normalizar_zona_para_guardar(zona_opcion, personas)

    with transaction.atomic():
        if not hay_disponibilidad(fecha, hora, personas, zona_opcion):
            messages.error(request, "Lo sentimos, esa opción ya no está disponible.")
            return redirect("reserva_fecha")

        reserva = Reserva.objects.create(
            nombre=reserva_data["nombre"],
            email=reserva_data["email"],
            telefono=reserva_data["telefono"],
            personas=personas,
            fecha=fecha,
            hora=hora,
            servicio=periodo_de_hora(hora) or reserva_data.get("turno", "comida"),
            zona=datos_zona["zona"],
            personas_barra=datos_zona["personas_barra"],
            personas_mesa=datos_zona["personas_mesa"],
            notas=reserva_data.get("notas", ""),
            importe_anticipo=IMPORTE_ANTICIPO,
            estado="pendiente_pago",
            expira_en=timezone.now() + timedelta(minutes=MINUTOS_EXPIRACION_PAGO),
        )

    stripe.api_key = settings.STRIPE_SECRET_KEY

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        mode="payment",
        customer_email=reserva.email,
        line_items=[
            {
                "price_data": {
                    "currency": "eur",
                    "product_data": {"name": "Reserva Baiku - anticipo reserva"},
                    "unit_amount": IMPORTE_ANTICIPO,
                },
                "quantity": 1,
            }
        ],
        success_url=f"{settings.SITE_URL}/pago/exito/?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{settings.SITE_URL}/pago/cancelado/",
        metadata={"reserva_id": reserva.id},
    )

    reserva.stripe_session_id = session.id
    reserva.save(update_fields=["stripe_session_id"])

    return redirect(session.url)


def enviar_email_confirmacion(reserva):
    asunto = "Reserva confirmada en Baiku"
    zona_texto = reserva.get_zona_display()
    if reserva.zona == "combinada" and (reserva.personas_barra or reserva.personas_mesa):
        zona_texto = f"Barra + mesas ({reserva.personas_barra} en barra + {reserva.personas_mesa} en mesa)"

    enlace = "https://baiku-reservas.onrender.com/gestionar-reserva/"

    mensaje = (
        f"Hola {reserva.nombre},\n\n"
        "Tu reserva en Baiku ha sido confirmada.\n\n"
        "DETALLES DE LA RESERVA\n"
        f"Fecha: {reserva.fecha.strftime('%d/%m/%Y')}\n"
        f"Hora: {reserva.hora.strftime('%H:%M')}\n"
        f"Personas: {reserva.personas}\n"
        f"Zona: {zona_texto}\n\n"
        "Puedes gestionar o cancelar tu reserva desde:\n"
        f"{enlace}\n\n"
        "Muchas gracias,\n"
        "Baiku"
    )

    send_mail(
        asunto,
        mensaje,
        settings.DEFAULT_FROM_EMAIL,
        [reserva.email],
        fail_silently=False,
    )

def enviar_email_modificacion(reserva):
    asunto = "Tu reserva en Baiku ha sido modificada"
    zona_texto = reserva.get_zona_display()
    if reserva.zona == "combinada" and (reserva.personas_barra or reserva.personas_mesa):
        zona_texto = f"Barra + mesas ({reserva.personas_barra} en barra + {reserva.personas_mesa} en mesa)"

    enlace = "https://baiku-reservas.onrender.com/gestionar-reserva/"

    mensaje = (
        f"Hola {reserva.nombre},\n\n"
        "Tu reserva ha sido modificada correctamente.\n\n"
        "NUEVOS DATOS\n"
        f"Fecha: {reserva.fecha.strftime('%d/%m/%Y')}\n"
        f"Hora: {reserva.hora.strftime('%H:%M')}\n"
        f"Personas: {reserva.personas}\n"
        f"Zona: {zona_texto}\n\n"
        "Puedes volver a gestionarla desde:\n"
        f"{enlace}\n\n"
        "Gracias,\n"
        "Baiku"
    )

    send_mail(
        asunto,
        mensaje,
        settings.DEFAULT_FROM_EMAIL,
        [reserva.email],
        fail_silently=False,
    )

@csrf_exempt
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)
    except Exception:
        return HttpResponse(status=400)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        reserva_id = session["metadata"].get("reserva_id")
        reserva = Reserva.objects.filter(id=reserva_id).first()

        if reserva and reserva.estado != "confirmada":
            reserva.estado = "confirmada"
            reserva.stripe_session_id = session["id"]
            reserva.expira_en = None
            reserva.save(update_fields=["estado", "stripe_session_id", "expira_en", "actualizado"])
            _enviar_email_seguro(enviar_email_confirmacion, reserva)

    return HttpResponse(status=200)


def pago_exito(request):
    session_id = request.GET.get("session_id")

    if session_id and settings.STRIPE_SECRET_KEY:
        try:
            stripe.api_key = settings.STRIPE_SECRET_KEY
            session = stripe.checkout.Session.retrieve(session_id)
            reserva_id = session.metadata.get("reserva_id")
            reserva = Reserva.objects.filter(id=reserva_id).first()

            if reserva and reserva.estado != "confirmada":
                reserva.estado = "confirmada"
                reserva.stripe_session_id = session.id
                reserva.expira_en = None
                reserva.save(update_fields=["estado", "stripe_session_id", "expira_en", "actualizado"])
                _enviar_email_seguro(enviar_email_confirmacion, reserva)
        except Exception:
            messages.warning(request, "Pago recibido. Si no recibes el email, contacta con Baiku.")

    request.session.pop("reserva", None)
    return render(request, "reservas/pago_exito.html")


def pago_cancelado(request):
    return render(request, "reservas/pago_cancelado.html")


@login_required
def staff_hoy(request):
    fecha_txt = request.GET.get("fecha")

    if fecha_txt:
        try:
            fecha = _fecha_desde_texto(fecha_txt)
        except ValueError:
            messages.error(request, "Fecha no válida.")
            fecha = date.today()
    else:
        fecha = date.today()

    reservas = (
        Reserva.objects
        .filter(fecha=fecha)
        .exclude(estado="cancelada")
        .order_by("hora", "zona", "nombre")
    )

    total_personas = sum(r.personas for r in reservas)

    context = {
        "fecha": fecha,
        "fecha_anterior": fecha - timedelta(days=1),
        "fecha_siguiente": fecha + timedelta(days=1),
        "reservas": reservas,
        "total_reservas": reservas.count(),
        "total_personas": total_personas,
        "hoy": date.today(),
    }

    return render(request, "reservas/staff_hoy.html", context)


@login_required
@require_POST
def cambiar_estado(request, reserva_id):
    reserva = get_object_or_404(Reserva, id=reserva_id)
    nuevo_estado = request.POST.get("estado")

    if nuevo_estado in ["confirmada", "llegado", "no_show", "cancelada"]:
        reserva.estado = nuevo_estado
        reserva.save(update_fields=["estado", "actualizado"])

    return redirect("staff_hoy")


@login_required
def staff_ocupacion(request):
    fecha_txt = request.GET.get("fecha")
    fecha = date.today()

    if fecha_txt:
        try:
            fecha = _fecha_desde_texto(fecha_txt)
        except ValueError:
            messages.error(request, "Fecha no válida.")

    motivo_no_reservable = _motivo_fecha_no_reservable(fecha)
    turnos = [] if motivo_no_reservable else mapa_ocupacion(fecha)
    bloqueado = BloqueoDia.objects.filter(fecha=fecha).first()

    return render(
        request,
        "reservas/staff_ocupacion.html",
        {
            "fecha": fecha,
            "turnos": turnos,
            "bloqueado": bloqueado,
            "motivo_no_reservable": motivo_no_reservable,
        },
    )


@login_required
@require_POST
def bloquear_dia(request):
    fecha_txt = request.POST.get("fecha")
    motivo = request.POST.get("motivo", "").strip()

    try:
        fecha = _fecha_desde_texto(fecha_txt)
    except (TypeError, ValueError):
        messages.error(request, "Fecha no válida.")
        return redirect("staff_ocupacion")

    BloqueoDia.objects.update_or_create(fecha=fecha, defaults={"motivo": motivo})
    messages.success(request, "Día bloqueado correctamente.")

    return redirect(f"/staff/ocupacion/?fecha={fecha_txt}")


@login_required
def staff_nueva_reserva(request):
    """Reserva manual staff:
    1) Fecha
    2) Personas
    3) Servicio: comida/cena
    4) Hora exacta de llegada
    5) Zona
    6) Datos cliente
    """
    fecha_txt = request.GET.get("fecha") or request.POST.get("fecha") or ""
    personas_txt = request.GET.get("personas") or request.POST.get("personas") or ""
    turno_tipo = request.GET.get("turno") or request.POST.get("turno") or ""
    hora_txt = request.GET.get("hora") or request.POST.get("hora") or ""
    zona = request.GET.get("zona") or request.POST.get("zona") or ""

    fecha = None
    personas = None
    hora = None
    turnos = []
    horas_llegada = []
    zonas_staff = []
    hueco_elegido = None
    motivo_no_reservable = ""

    if fecha_txt:
        try:
            fecha = _fecha_desde_texto(fecha_txt)
            motivo_no_reservable = _motivo_fecha_no_reservable(fecha)
        except (TypeError, ValueError):
            messages.error(request, "Fecha no válida.")
            fecha = None

    if personas_txt:
        try:
            personas = int(personas_txt)
        except (TypeError, ValueError):
            personas = None

    if fecha and personas:
        if personas < 1 or personas > MAX_PERSONAS_RESERVA:
            messages.error(request, f"Elige entre 1 y {MAX_PERSONAS_RESERVA} personas.")
        elif fecha < date.today():
            messages.error(request, "No puedes crear una reserva en una fecha pasada.")
        elif motivo_no_reservable:
            turnos = []
        else:
            turnos = turnos_disponibles(fecha, personas)

    if turno_tipo:
        horas_llegada = horas_llegada_para_turno(fecha, turno_tipo) if fecha else []

    if hora_txt:
        try:
            hora = _hora_desde_texto(hora_txt)
        except (TypeError, ValueError):
            messages.error(request, "Hora no válida.")
            hora = None

    if fecha and personas and hora:
        zonas_staff = zonas_disponibles(fecha, hora, personas)

    if fecha and personas and hora and zona:
        if hay_disponibilidad(fecha, hora, personas, zona):
            hueco_elegido = {
                "fecha": fecha,
                "personas": personas,
                "turno_tipo": turno_tipo,
                "hora": hora,
                "zona": zona,
                "zona_nombre": _normalizar_zona_para_guardar(zona, personas)["zona_nombre"],
            }
        else:
            messages.error(request, "Ese hueco ya no está disponible. Elige otro.")
            return redirect(
                f"/staff/nueva-reserva/?fecha={fecha_txt}&personas={personas}&turno={turno_tipo}"
            )

    if request.method == "POST":
        nombre = request.POST.get("nombre", "").strip()
        email = request.POST.get("email", "").strip() or "staff@baiku.local"
        telefono = request.POST.get("telefono", "").strip()
        notas = request.POST.get("notas", "").strip()

        if not hueco_elegido:
            messages.error(request, "Primero elige un hueco disponible.")
            return redirect("staff_nueva_reserva")

        if not nombre or not telefono:
            messages.error(request, "Completa nombre y teléfono.")
            return redirect(
                f"/staff/nueva-reserva/?fecha={fecha_txt}&personas={personas}&turno={turno_tipo}&hora={hora_txt}&zona={zona}"
            )

        if not hay_disponibilidad(fecha, hora, personas, zona):
            messages.error(request, "Ese hueco ya no está disponible. Prueba otro turno.")
            return redirect(
                f"/staff/nueva-reserva/?fecha={fecha_txt}&personas={personas}&turno={turno_tipo}"
            )

        datos_zona = _normalizar_zona_para_guardar(zona, personas)

        reserva = Reserva.objects.create(
            nombre=nombre,
            email=email,
            telefono=telefono,
            personas=personas,
            fecha=fecha,
            hora=hora,
            servicio=periodo_de_hora(hora) or turno_tipo or "comida",
            zona=datos_zona["zona"],
            personas_barra=datos_zona["personas_barra"],
            personas_mesa=datos_zona["personas_mesa"],
            notas=notas,
            estado="confirmada",
            importe_anticipo=0,
        )

        _enviar_email_seguro(enviar_email_confirmacion, reserva)

        messages.success(request, "Reserva manual creada correctamente.")
        return redirect("staff_hoy")

    return render(
        request,
        "reservas/staff_nueva_reserva.html",
        {
            "max_personas": MAX_PERSONAS_RESERVA,
            "fecha": fecha,
            "fecha_txt": fecha_txt,
            "personas": personas,
            "turnos": turnos,
            "turno_tipo": turno_tipo,
            "horas_llegada": horas_llegada,
            "hora": hora,
            "zonas_staff": zonas_staff,
            "hoy": date.today(),
            "hueco_elegido": hueco_elegido,
            "motivo_no_reservable": motivo_no_reservable,
        },
    )

@require_POST
def confirmar_reserva(request):
    reserva_data = obtener_reserva_session(request)
    campos = ["personas", "fecha", "hora", "zona", "nombre", "email", "telefono"]

    if not all(reserva_data.get(campo) for campo in campos):
        return redirect("reserva_fecha")

    fecha = _fecha_desde_texto(reserva_data["fecha"])
    hora = _hora_desde_texto(reserva_data["hora"])
    personas = int(reserva_data["personas"])
    zona = reserva_data["zona"]
    zona_opcion = reserva_data.get("zona_opcion", zona)
    datos_zona = _normalizar_zona_para_guardar(zona_opcion, personas)

    with transaction.atomic():
        if not hay_disponibilidad(fecha, hora, personas, zona_opcion):
            messages.error(request, "Lo sentimos, esa opción ya no está disponible.")
            return redirect("reserva_fecha")

        reserva = Reserva.objects.create(
            nombre=reserva_data["nombre"],
            email=reserva_data["email"],
            telefono=reserva_data["telefono"],
            personas=personas,
            fecha=fecha,
            hora=hora,
            servicio=periodo_de_hora(hora) or reserva_data.get("turno", "comida"),
            zona=datos_zona["zona"],
            personas_barra=datos_zona["personas_barra"],
            personas_mesa=datos_zona["personas_mesa"],
            notas=reserva_data.get("notas", ""),
            importe_anticipo=0,
            estado="confirmada",
            expira_en=None,
        )

    _enviar_email_seguro(enviar_email_confirmacion, reserva)

    request.session.pop("reserva", None)
    return render(request, "reservas/pago_exito.html")


def gestionar_reserva(request):
    reservas = []

    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        telefono = request.POST.get("telefono", "").strip()

        reservas = Reserva.objects.filter(
            email=email,
            telefono=telefono,
        ).exclude(
            estado="cancelada"
        ).order_by("fecha", "hora")

        if not reservas:
            messages.error(request, "No hemos encontrado reservas con esos datos.")

    return render(request, "reservas/gestionar_reserva.html", {"reservas": reservas})


@require_POST
def eliminar_reserva_cliente(request, reserva_id):
    reserva = get_object_or_404(Reserva, id=reserva_id)

    reserva.estado = "cancelada"

    reserva.save(
        update_fields=["estado", "actualizado"]
    )

    _enviar_email_seguro(
        enviar_email_cancelacion,
        reserva
    )

    messages.success(
        request,
        "Reserva cancelada correctamente."
    )

    return redirect("gestionar_reserva")


def editar_reserva_cliente(request, reserva_id):
    reserva = get_object_or_404(Reserva, id=reserva_id)

    if request.method == "POST":
        accion = request.POST.get("accion")

        try:
            personas = int(request.POST.get("personas", 0))
            fecha = _fecha_desde_texto(request.POST.get("fecha"))
            hora = _hora_desde_texto(request.POST.get("hora"))
        except (TypeError, ValueError):
            messages.error(request, "Datos de reserva no válidos.")
            return redirect("editar_reserva_cliente", reserva_id=reserva.id)

        if personas < 1 or personas > MAX_PERSONAS_RESERVA:
            messages.error(request, f"Elige entre 1 y {MAX_PERSONAS_RESERVA} personas.")
            return redirect("editar_reserva_cliente", reserva_id=reserva.id)

        motivo_no_reservable = _motivo_fecha_no_reservable(fecha)
        if fecha < date.today() or motivo_no_reservable:
            messages.error(request, motivo_no_reservable or "No puedes reservar una fecha pasada.")
            return redirect("editar_reserva_cliente", reserva_id=reserva.id)

        zonas = zonas_disponibles(
            fecha,
            hora,
            personas,
            excluir_reserva_id=reserva.id,
        )

        if accion == "buscar_zonas":
            return render(
                request,
                "reservas/editar_reserva_cliente.html",
                {
                    "reserva": reserva,
                    "max_personas": MAX_PERSONAS_RESERVA,
                    "hoy": date.today(),
                    "personas_form": personas,
                    "fecha_form": fecha,
                    "hora_form": hora,
                    "zonas": zonas,
                    "mostrar_zonas": True,
                },
            )

        zona = request.POST.get("zona")

        if not zona:
            messages.error(request, "Elige una zona disponible.")
            return redirect("editar_reserva_cliente", reserva_id=reserva.id)

        if not hay_disponibilidad(
            fecha,
            hora,
            personas,
            zona,
            excluir_reserva_id=reserva.id,
        ):
            messages.error(request, "Ese nuevo hueco no está disponible.")
            return redirect("editar_reserva_cliente", reserva_id=reserva.id)

        datos_zona = _normalizar_zona_para_guardar(zona, personas)

        reserva.personas = personas
        reserva.fecha = fecha
        reserva.hora = hora
        reserva.zona = datos_zona["zona"]
        reserva.personas_barra = datos_zona["personas_barra"]
        reserva.personas_mesa = datos_zona["personas_mesa"]
        reserva.save(
            update_fields=[
                "personas",
                "fecha",
                "hora",
                "zona",
                "personas_barra",
                "personas_mesa",
                "actualizado",
            ]
        )
        _enviar_email_seguro(enviar_email_modificacion, reserva)
        messages.success(request, "Reserva modificada correctamente.")
        return redirect("gestionar_reserva")

    return render(
        request,
        "reservas/editar_reserva_cliente.html",
        {
            "reserva": reserva,
            "max_personas": MAX_PERSONAS_RESERVA,
            "hoy": date.today(),
            "personas_form": reserva.personas,
            "fecha_form": reserva.fecha,
            "hora_form": reserva.hora,
            "zonas": [],
            "mostrar_zonas": False,
        },
    )


@login_required
def staff_reservas_count(request):
    fecha = date.today()

    total = (
        Reserva.objects
        .filter(fecha=fecha)
        .exclude(estado="cancelada")
        .count()
    )

    return JsonResponse({"total": total})


def enviar_email_cancelacion(reserva):
    asunto = "Tu reserva en Baiku ha sido cancelada"

    zona_texto = reserva.get_zona_display()

    if reserva.zona == "combinada" and (
        reserva.personas_barra or reserva.personas_mesa
    ):
        zona_texto = (
            f"Barra + mesas "
            f"({reserva.personas_barra} en barra + "
            f"{reserva.personas_mesa} en mesa)"
        )

    mensaje = (
        f"Hola {reserva.nombre},\n\n"
        "Tu reserva en Baiku ha sido cancelada correctamente.\n\n"
        "DATOS CANCELADOS\n"
        f"Fecha: {reserva.fecha.strftime('%d/%m/%Y')}\n"
        f"Hora: {reserva.hora.strftime('%H:%M')}\n"
        f"Personas: {reserva.personas}\n"
        f"Zona: {zona_texto}\n\n"
        "Esperamos verte pronto.\n\n"
        "Baiku"
    )

    send_mail(
        asunto,
        mensaje,
        settings.DEFAULT_FROM_EMAIL,
        [reserva.email],
        fail_silently=False,
    )
from datetime import date, datetime, timedelta

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.http import JsonResponse

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
)


IMPORTE_ANTICIPO = 1000  # 10€ total por reserva, en céntimos
MINUTOS_EXPIRACION_PAGO = 15

PASOS_RESERVA = {
    "fecha": {"numero": 1, "total": 6, "titulo": "Fecha"},
    "personas": {"numero": 2, "total": 6, "titulo": "Personas"},
    "turno": {"numero": 3, "total": 6, "titulo": "Turno"},
    "zona": {"numero": 4, "total": 6, "titulo": "Zona"},
    "datos": {"numero": 5, "total": 6, "titulo": "Datos"},
    "resumen": {"numero": 6, "total": 6, "titulo": "Confirmar"},
}


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
    return dict(Reserva.ZONA_CHOICES).get(zona, zona)


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

        request.session["reserva"] = {"fecha": fecha_txt}
        request.session.modified = True
        return redirect("reserva_personas")

    return render(
        request,
        "reservas/reserva_fecha.html",
        {"reserva": reserva, "paso": PASOS_RESERVA["fecha"], "hoy": date.today()},
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

    if request.method == "POST":
        hora_txt = request.POST.get("hora")

        try:
            hora = _hora_desde_texto(hora_txt)
        except (TypeError, ValueError):
            messages.error(request, "Turno no válido.")
            return redirect("reserva_turno")

        horas_validas = [t["hora"] for t in turnos]
        if hora not in horas_validas:
            messages.error(request, "Ese turno ya no está disponible.")
            return redirect("reserva_turno")

        guardar_reserva_session(request, {"hora": hora_txt})
        return redirect("reserva_zona")

    return render(
        request,
        "reservas/reserva_turno.html",
        {"reserva": reserva, "turnos": turnos, "paso": PASOS_RESERVA["turno"]},
    )


def reserva_zona(request):
    reserva = obtener_reserva_session(request)

    if not reserva.get("hora"):
        return redirect("reserva_turno")

    fecha = _fecha_desde_texto(reserva["fecha"])
    hora = _hora_desde_texto(reserva["hora"])
    personas = int(reserva["personas"])
    zonas = zonas_disponibles(fecha, hora, personas)

    if request.method == "POST":
        zona = request.POST.get("zona")

        if not hay_disponibilidad(fecha, hora, personas, zona):
            messages.error(request, "Esa zona ya no está disponible.")
            return redirect("reserva_zona")

        guardar_reserva_session(request, {"zona": zona, "zona_nombre": _zona_nombre(zona)})
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

    with transaction.atomic():
        if not hay_disponibilidad(fecha, hora, personas, zona):
            messages.error(request, "Lo sentimos, esa opción ya no está disponible.")
            return redirect("reserva_fecha")

        reserva = Reserva.objects.create(
            nombre=reserva_data["nombre"],
            email=reserva_data["email"],
            telefono=reserva_data["telefono"],
            personas=personas,
            fecha=fecha,
            hora=hora,
            zona=zona,
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

    enlace = f"{settings.SITE_URL}/gestionar-reserva/"

    mensaje = (
        f"Hola {reserva.nombre},\n\n"
        "Tu reserva en Baiku ha sido confirmada.\n\n"
        "DETALLES DE LA RESERVA\n"
        f"Fecha: {reserva.fecha.strftime('%d/%m/%Y')}\n"
        f"Hora: {reserva.hora.strftime('%H:%M')}\n"
        f"Personas: {reserva.personas}\n"
        f"Zona: {reserva.get_zona_display()}\n\n"
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

    enlace = f"{settings.SITE_URL}/gestionar-reserva/"

    mensaje = (
        f"Hola {reserva.nombre},\n\n"
        "Tu reserva ha sido modificada correctamente.\n\n"
        "NUEVOS DATOS\n"
        f"Fecha: {reserva.fecha.strftime('%d/%m/%Y')}\n"
        f"Hora: {reserva.hora.strftime('%H:%M')}\n"
        f"Personas: {reserva.personas}\n"
        f"Zona: {reserva.get_zona_display()}\n\n"
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
            enviar_email_confirmacion(reserva)

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
                enviar_email_confirmacion(reserva)
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
        horas_llegada = horas_llegada_para_turno(turno_tipo)

    if hora_txt:
        try:
            hora = _hora_desde_texto(hora_txt)
        except (TypeError, ValueError):
            messages.error(request, "Hora no válida.")
            hora = None

    if fecha and personas and hora and zona:
        if hay_disponibilidad(fecha, hora, personas, zona):
            hueco_elegido = {
                "fecha": fecha,
                "personas": personas,
                "turno_tipo": turno_tipo,
                "hora": hora,
                "zona": zona,
                "zona_nombre": _zona_nombre(zona),
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

        reserva = Reserva.objects.create(
            nombre=nombre,
            email=email,
            telefono=telefono,
            personas=personas,
            fecha=fecha,
            hora=hora,
            zona=zona,
            notas=notas,
            estado="confirmada",
            importe_anticipo=0,
        )

        if reserva.email and reserva.email != "staff@baiku.local":
            enviar_email_confirmacion(reserva)

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

    with transaction.atomic():
        if not hay_disponibilidad(fecha, hora, personas, zona):
            messages.error(request, "Lo sentimos, esa opción ya no está disponible.")
            return redirect("reserva_fecha")

        reserva = Reserva.objects.create(
            nombre=reserva_data["nombre"],
            email=reserva_data["email"],
            telefono=reserva_data["telefono"],
            personas=personas,
            fecha=fecha,
            hora=hora,
            zona=zona,
            notas=reserva_data.get("notas", ""),
            importe_anticipo=0,
            estado="confirmada",
            expira_en=None,
        )

    enviar_email_confirmacion(reserva)
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
    reserva.save(update_fields=["estado", "actualizado"])
    messages.success(request, "Reserva cancelada correctamente.")
    return redirect("gestionar_reserva")


def editar_reserva_cliente(request, reserva_id):
    reserva = get_object_or_404(Reserva, id=reserva_id)

    if request.method == "POST":
        try:
            personas = int(request.POST.get("personas", 0))
            fecha = _fecha_desde_texto(request.POST.get("fecha"))
            hora = _hora_desde_texto(request.POST.get("hora"))
        except (TypeError, ValueError):
            messages.error(request, "Datos de reserva no válidos.")
            return redirect("editar_reserva_cliente", reserva_id=reserva.id)

        zona = request.POST.get("zona")

        if personas < 1 or personas > MAX_PERSONAS_RESERVA:
            messages.error(request, f"Elige entre 1 y {MAX_PERSONAS_RESERVA} personas.")
            return redirect("editar_reserva_cliente", reserva_id=reserva.id)

        motivo_no_reservable = _motivo_fecha_no_reservable(fecha)
        if fecha < date.today() or motivo_no_reservable:
            messages.error(request, motivo_no_reservable or "No puedes reservar una fecha pasada.")
            return redirect("editar_reserva_cliente", reserva_id=reserva.id)

        if not hay_disponibilidad(fecha, hora, personas, zona):
            messages.error(request, "Ese nuevo hueco no está disponible.")
            return redirect("editar_reserva_cliente", reserva_id=reserva.id)

        reserva.personas = personas
        reserva.fecha = fecha
        reserva.hora = hora
        reserva.zona = zona
        reserva.save(update_fields=["personas", "fecha", "hora", "zona", "actualizado"])
        enviar_email_modificacion(reserva)
        messages.success(request, "Reserva modificada correctamente.")
        return redirect("gestionar_reserva")

    return render(
        request,
        "reservas/editar_reserva_cliente.html",
        {"reserva": reserva, "max_personas": MAX_PERSONAS_RESERVA, "hoy": date.today()},
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
"""
Emails que se envían a los clientes.

Todos comparten el bloque de detalles de la reserva y el enlace de gestión,
que se construye a partir de settings.SITE_URL.
"""
from datetime import datetime

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

from .configuracion_turnos import DURACION_CENA, TURNOS_CENA


def enlace_gestion():
    return f"{settings.SITE_URL}{reverse('gestionar_reserva')}"


def texto_zona(reserva):
    if reserva.zona == "combinada" and (
        reserva.personas_barra or reserva.personas_mesa
    ):
        return (
            f"Barra + mesas "
            f"({reserva.personas_barra} en barra + "
            f"{reserva.personas_mesa} en mesa)"
        )

    return reserva.get_zona_display()


def _detalles(reserva):
    return (
        f"Fecha: {reserva.fecha.strftime('%d/%m/%Y')}\n"
        f"Hora: {reserva.hora.strftime('%H:%M')}\n"
        f"Personas: {reserva.personas}\n"
        f"Zona: {texto_zona(reserva)}\n\n"
    )


def _aviso_cena_nueva(reserva):
    """Aviso de duración para los turnos de cena del sistema nuevo."""
    if (
        reserva.servicio != "cena"
        or reserva.cena_sistema_antiguo
        or reserva.hora not in TURNOS_CENA
    ):
        return ""

    inicio = datetime.combine(reserva.fecha, reserva.hora)
    fin = inicio + DURACION_CENA
    minutos = int(DURACION_CENA.total_seconds() // 60)

    return (
        "INFORMACIÓN IMPORTANTE SOBRE TU RESERVA\n"
        f"El servicio de cena tiene una duración máxima de {minutos} minutos.\n"
        f"Tu turno comienza a las {inicio.strftime('%H:%M')} "
        f"y finaliza a las {fin.strftime('%H:%M')}.\n"
        "Te agradecemos tu puntualidad para que puedas "
        "disfrutar de tu experiencia en Baiku.\n\n"
    )


def _enviar(asunto, mensaje, reserva):
    send_mail(
        asunto,
        mensaje,
        settings.DEFAULT_FROM_EMAIL,
        [reserva.email],
        fail_silently=False,
    )


def enviar_email_confirmacion(reserva):
    mensaje = (
        f"Hola {reserva.nombre},\n\n"
        "Tu reserva en Baiku ha sido confirmada.\n\n"
        "DETALLES DE LA RESERVA\n"
        f"{_detalles(reserva)}"
        f"{_aviso_cena_nueva(reserva)}"
        "Puedes gestionar o cancelar tu reserva desde:\n"
        f"{enlace_gestion()}\n\n"
        "Muchas gracias,\n"
        "Baiku"
    )
    _enviar("Reserva confirmada en Baiku", mensaje, reserva)


def enviar_email_modificacion(reserva):
    mensaje = (
        f"Hola {reserva.nombre},\n\n"
        "Tu reserva ha sido modificada correctamente.\n\n"
        "NUEVOS DATOS\n"
        f"{_detalles(reserva)}"
        f"{_aviso_cena_nueva(reserva)}"
        "Puedes volver a gestionarla desde:\n"
        f"{enlace_gestion()}\n\n"
        "Gracias,\n"
        "Baiku"
    )
    _enviar("Tu reserva en Baiku ha sido modificada", mensaje, reserva)


def enviar_email_cancelacion(reserva):
    mensaje = (
        f"Hola {reserva.nombre},\n\n"
        "Tu reserva en Baiku ha sido cancelada correctamente.\n\n"
        "DATOS CANCELADOS\n"
        f"{_detalles(reserva)}"
        "Esperamos verte pronto.\n\n"
        "Baiku"
    )
    _enviar("Tu reserva en Baiku ha sido cancelada", mensaje, reserva)


def enviar_email_recordatorio(reserva):
    mensaje = (
        f"Hola {reserva.nombre},\n\n"
        "Te recordamos que tienes una reserva en Baiku para mañana.\n\n"
        "DETALLES DE LA RESERVA\n"
        f"{_detalles(reserva)}"
        f"{_aviso_cena_nueva(reserva)}"
        "Si necesitas modificar o cancelar tu reserva, puedes hacerlo desde:\n"
        f"{enlace_gestion()}\n\n"
        "Muchas gracias,\n"
        "Baiku"
    )
    _enviar("Recordatorio de tu reserva en Baiku", mensaje, reserva)

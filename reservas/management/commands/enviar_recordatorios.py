from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.utils import timezone

from reservas.models import Reserva


class Command(BaseCommand):
    help = "Envía recordatorios de reservas para el día siguiente."

    def handle(self, *args, **options):
        hoy = timezone.localdate()

       

        fecha_objetivo = hoy + timedelta(days=1)

        reservas = Reserva.objects.filter(
            fecha=fecha_objetivo,
            estado="confirmada",
            recordatorio_enviado=False,
        )

        total_enviados = 0

        for reserva in reservas:
            zona_texto = reserva.get_zona_display()

            if reserva.zona == "combinada" and (
                reserva.personas_barra or reserva.personas_mesa
            ):
                zona_texto = (
                    f"Barra + mesas "
                    f"({reserva.personas_barra} en barra + "
                    f"{reserva.personas_mesa} en mesa)"
                )

            asunto = "Recordatorio de tu reserva en Baiku"

            mensaje = (
                f"Hola {reserva.nombre},\n\n"
                "Te recordamos que tienes una reserva en Baiku para mañana.\n\n"
                "DETALLES DE LA RESERVA\n"
                f"Fecha: {reserva.fecha.strftime('%d/%m/%Y')}\n"
                f"Hora: {reserva.hora.strftime('%H:%M')}\n"
                f"Personas: {reserva.personas}\n"
                f"Zona: {zona_texto}\n\n"
                "Si necesitas modificar o cancelar tu reserva, puedes hacerlo desde:\n"
                "https://baiku-reservas.onrender.com/gestionar-reserva/\n\n"
                "Muchas gracias,\n"
                "Baiku"
            )

            try:
                send_mail(
                    asunto,
                    mensaje,
                    settings.DEFAULT_FROM_EMAIL,
                    [reserva.email],
                    fail_silently=False,
                )

                reserva.recordatorio_enviado = True
                reserva.save(update_fields=["recordatorio_enviado", "actualizado"])

                total_enviados += 1
                self.stdout.write(f"Recordatorio enviado a {reserva.email}")

            except Exception as e:
                self.stderr.write(
                    f"Error enviando recordatorio reserva {reserva.id}: {e}"
                )

        self.stdout.write(
            self.style.SUCCESS(f"Recordatorios enviados: {total_enviados}")
        )
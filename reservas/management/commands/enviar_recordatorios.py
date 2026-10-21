from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from reservas.emails import enviar_email_recordatorio
from reservas.models import Reserva


class Command(BaseCommand):
    help = "Envía recordatorios de reservas para el día siguiente."

    def handle(self, *args, **options):
        fecha_objetivo = timezone.localdate() + timedelta(days=1)

        reservas = Reserva.objects.filter(
            fecha=fecha_objetivo,
            estado="confirmada",
            recordatorio_enviado=False,
        )

        total_enviados = 0

        for reserva in reservas:
            try:
                enviar_email_recordatorio(reserva)

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

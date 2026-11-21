"""
Borrado de datos personales según la política de privacidad.

- Notas (pueden contener alergias = datos de salud): se vacían a los
  30 días de la fecha de la reserva.
- Datos personales de la reserva (nombre, email, teléfono): se anonimizan
  a los 12 meses de la fecha de la reserva. La reserva se conserva para
  las estadísticas (fecha, personas, zona...), pero ya no identifica a nadie.

Uso:
    python manage.py limpiar_datos            # aplica los cambios
    python manage.py limpiar_datos --dry-run  # solo muestra cuántas reservas tocaría

Programarlo una vez al día, igual que enviar_recordatorios.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from reservas.models import Reserva

DIAS_NOTAS = 30
DIAS_DATOS_PERSONALES = 365

NOMBRE_ANONIMO = "Anonimizado"
EMAIL_ANONIMO = "anonimizado@baiku.invalid"


class Command(BaseCommand):
    help = "Vacía notas antiguas y anonimiza reservas de hace más de 12 meses."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="No modifica nada; solo indica cuántas reservas se tocarían.",
        )

    def handle(self, *args, **options):
        hoy = timezone.localdate()
        ahora = timezone.now()
        dry_run = options["dry_run"]

        notas_qs = Reserva.objects.filter(
            fecha__lt=hoy - timedelta(days=DIAS_NOTAS),
        ).exclude(notas="")

        personales_qs = Reserva.objects.filter(
            fecha__lt=hoy - timedelta(days=DIAS_DATOS_PERSONALES),
        ).exclude(email=EMAIL_ANONIMO)

        if dry_run:
            self.stdout.write(f"Notas que se vaciarían: {notas_qs.count()}")
            self.stdout.write(f"Reservas que se anonimizarían: {personales_qs.count()}")
            return

        # update() no pasa por Reserva.save(), así no se tocan otras reglas del modelo.
        notas_vaciadas = notas_qs.update(notas="", actualizado=ahora)

        anonimizadas = personales_qs.update(
            nombre=NOMBRE_ANONIMO,
            email=EMAIL_ANONIMO,
            telefono="",
            notas="",
            actualizado=ahora,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Notas vaciadas: {notas_vaciadas} · "
                f"Reservas anonimizadas: {anonimizadas}"
            )
        )

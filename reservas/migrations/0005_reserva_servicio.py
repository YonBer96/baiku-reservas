# Generated manually for staggered reservation services
from django.db import migrations, models
from datetime import time


def asignar_servicio_por_hora(apps, schema_editor):
    Reserva = apps.get_model("reservas", "Reserva")
    Reserva.objects.filter(hora__gte=time(20, 0)).update(servicio="cena")
    Reserva.objects.filter(hora__lt=time(20, 0)).update(servicio="comida")


class Migration(migrations.Migration):

    dependencies = [
        ("reservas", "0004_reserva_recordatorio_enviado"),
    ]

    operations = [
        migrations.AddField(
            model_name="reserva",
            name="servicio",
            field=models.CharField(choices=[("comida", "Comida"), ("cena", "Cena")], default="comida", max_length=20),
        ),
        migrations.RunPython(asignar_servicio_por_hora, migrations.RunPython.noop),
    ]

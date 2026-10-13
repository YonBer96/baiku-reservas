from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("reservas", "0007_reserva_email_valoracion_enviado_reserva_valoracion_and_more")]

    operations = [
        migrations.AddField(
            model_name="reserva",
            name="cena_sistema_antiguo",
            field=models.BooleanField(default=True),
        ),
    ]

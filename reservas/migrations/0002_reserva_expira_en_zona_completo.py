from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reservas", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="reserva",
            name="expira_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="reserva",
            name="zona",
            field=models.CharField(
                choices=[
                    ("barra", "Barra"),
                    ("mesa", "Mesa"),
                    ("completo", "Restaurante completo"),
                ],
                max_length=20,
            ),
        ),
    ]

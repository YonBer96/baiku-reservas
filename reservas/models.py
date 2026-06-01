from django.db import models


class Reserva(models.Model):
    ESTADO_CHOICES = [
        ("pendiente_pago", "Pendiente de pago"),
        ("confirmada", "Confirmada"),
        ("llegado", "Ha llegado"),
        ("no_show", "No-show"),
        ("cancelada", "Cancelada"),
    ]

    ZONA_CHOICES = [
        ("barra", "Barra"),
        ("mesa", "Mesa"),
        ("combinada", "Barra + mesas"),
        ("completo", "Restaurante completo"),
    ]

    SERVICIO_CHOICES = [
        ("comida", "Comida"),
        ("cena", "Cena"),
    ]

    nombre = models.CharField(max_length=120)
    email = models.EmailField()
    telefono = models.CharField(max_length=30)

    personas = models.PositiveIntegerField()

    # NUEVO
    personas_barra = models.PositiveIntegerField(default=0)
    personas_mesa = models.PositiveIntegerField(default=0)

    fecha = models.DateField()
    hora = models.TimeField()
    servicio = models.CharField(
        max_length=20,
        choices=SERVICIO_CHOICES,
        default="comida",
    )

    zona = models.CharField(
        max_length=20,
        choices=ZONA_CHOICES
    )

    estado = models.CharField(
        max_length=20,
        choices=ESTADO_CHOICES,
        default="pendiente_pago"
    )

    importe_anticipo = models.PositiveIntegerField(default=0)

    stripe_session_id = models.CharField(
        max_length=255,
        blank=True
    )

    expira_en = models.DateTimeField(
        null=True,
        blank=True
    )

    notas = models.TextField(blank=True)

    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)
    recordatorio_enviado = models.BooleanField(default=False)

    class Meta:
        ordering = ["fecha", "servicio", "hora", "zona"]

    @property
    def zona_detalle(self):
        if self.zona == "combinada" and (self.personas_barra or self.personas_mesa):
            return f"Barra + mesas ({self.personas_barra} barra + {self.personas_mesa} mesa)"
        return self.get_zona_display()

    def __str__(self):
        return (
            f"{self.nombre} · "
            f"{self.fecha} {self.hora} · "
            f"{self.personas} pax"
        )


class BloqueoDia(models.Model):
    fecha = models.DateField(unique=True)
    motivo = models.CharField(max_length=255, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["fecha"]

    def __str__(self):
        return f"Bloqueado: {self.fecha}"
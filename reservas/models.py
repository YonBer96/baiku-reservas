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
        ("completo", "Restaurante completo"),
    ]

    nombre = models.CharField(max_length=120)
    email = models.EmailField()
    telefono = models.CharField(max_length=30)
    personas = models.PositiveIntegerField()
    fecha = models.DateField()
    hora = models.TimeField()
    zona = models.CharField(max_length=20, choices=ZONA_CHOICES)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="pendiente_pago")
    importe_anticipo = models.PositiveIntegerField(default=0)  # en céntimos
    stripe_session_id = models.CharField(max_length=255, blank=True)
    expira_en = models.DateTimeField(null=True, blank=True)
    notas = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["fecha", "hora", "zona"]

    def __str__(self):
        return f"{self.nombre} · {self.fecha} {self.hora} · {self.personas} pax"


class BloqueoDia(models.Model):
    fecha = models.DateField(unique=True)
    motivo = models.CharField(max_length=255, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["fecha"]

    def __str__(self):
        return f"Bloqueado: {self.fecha}"

from django.contrib import admin
from .models import Reserva, BloqueoDia


@admin.register(Reserva)
class ReservaAdmin(admin.ModelAdmin):
    list_display = (
        "nombre",
        "personas",
        "fecha",
        "hora",
        "zona",
        "estado",
        "importe_anticipo",
    )
    list_filter = ("fecha", "hora", "zona", "estado")
    search_fields = ("nombre", "email", "telefono")
    ordering = ("fecha", "hora")


@admin.register(BloqueoDia)
class BloqueoDiaAdmin(admin.ModelAdmin):
    list_display = ("fecha", "motivo")
    search_fields = ("motivo",)
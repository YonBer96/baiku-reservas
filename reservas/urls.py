from django.urls import path
from django.views.generic import TemplateView
from . import views

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path(
        "legal/",
        TemplateView.as_view(template_name="reservas/legal.html"),
        name="legal",
    ),
    path("reservar/personas/", views.reserva_personas, name="reserva_personas"),
    path("reservar/fecha/", views.reserva_fecha, name="reserva_fecha"),
    path("api/calendario-reservas/", views.calendario_reservas_api, name="calendario_reservas_api"),
    path(
        "api/reserva/<int:reserva_id>/horas/",
        views.horas_edicion_reserva_api,
        name="horas_edicion_reserva_api",
    ),
    path("reservar/turno/", views.reserva_turno, name="reserva_turno"),
    path("reservar/hora/", views.reserva_hora, name="reserva_hora"),
    path("reservar/zona/", views.reserva_zona, name="reserva_zona"),
    path("reservar/datos/", views.reserva_datos, name="reserva_datos"),
    path("reservar/resumen/", views.reserva_resumen, name="reserva_resumen"),
    path("staff/hoy/", views.staff_hoy, name="staff_hoy"),
    path("staff/reserva/<int:reserva_id>/estado/", views.cambiar_estado, name="cambiar_estado"),
    path("staff/bloquear-dia/", views.bloquear_dia, name="bloquear_dia"),
    path("gestionar-reserva/", views.gestionar_reserva, name="gestionar_reserva"),
    path("gestionar-reserva/<int:reserva_id>/editar/", views.editar_reserva_cliente, name="editar_reserva_cliente"),
    path("gestionar-reserva/<int:reserva_id>/eliminar/", views.eliminar_reserva_cliente, name="eliminar_reserva_cliente"),
    path("reservar/confirmar/", views.confirmar_reserva, name="confirmar_reserva"),
    path("staff/reservas-count/", views.staff_reservas_count, name="staff_reservas_count"),
    path("staff/buscar-reserva/", views.staff_buscar_reserva, name="staff_buscar_reserva"),
]
from django.urls import path
from . import views

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("reservar/personas/", views.reserva_personas, name="reserva_personas"),
    path("reservar/fecha/", views.reserva_fecha, name="reserva_fecha"),
    path("reservar/turno/", views.reserva_turno, name="reserva_turno"),
    path("reservar/zona/", views.reserva_zona, name="reserva_zona"),
    path("reservar/datos/", views.reserva_datos, name="reserva_datos"),
    path("reservar/resumen/", views.reserva_resumen, name="reserva_resumen"),
    path("pago/exito/", views.pago_exito, name="pago_exito"),
    path("pago/cancelado/", views.pago_cancelado, name="pago_cancelado"),
    path("staff/hoy/", views.staff_hoy, name="staff_hoy"),
    path("staff/reserva/<int:reserva_id>/estado/", views.cambiar_estado, name="cambiar_estado"),
    path("staff/ocupacion/", views.staff_ocupacion, name="staff_ocupacion"),
    path("staff/bloquear-dia/", views.bloquear_dia, name="bloquear_dia"),
    path("staff/nueva-reserva/", views.staff_nueva_reserva, name="staff_nueva_reserva"),
    path("gestionar-reserva/", views.gestionar_reserva, name="gestionar_reserva"),
    path("gestionar-reserva/<int:reserva_id>/editar/", views.editar_reserva_cliente, name="editar_reserva_cliente"),
    path("gestionar-reserva/<int:reserva_id>/eliminar/", views.eliminar_reserva_cliente, name="eliminar_reserva_cliente"),
    path("reservar/confirmar/", views.confirmar_reserva, name="confirmar_reserva"),
    path("staff/reservas-count/",views.staff_reservas_count,name="staff_reservas_count"),
]
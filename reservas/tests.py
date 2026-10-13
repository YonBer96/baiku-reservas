from datetime import date, time

from django.test import TestCase
from django.urls import reverse

from reservas.models import Reserva
from reservas.services.disponibilidad import (
    hay_disponibilidad,
    horas_llegada_para_turno,
    plazas_libres_nueva_cena,
    zonas_disponibles,
)


class TestModeloBCenas(TestCase):
    def setUp(self):
        self.fecha = date(2026, 10, 16)

    def crear_reserva(self, hora, personas, zona="barra", antigua=True, estado="confirmada"):
        return Reserva.objects.create(
            nombre="Cliente de prueba",
            email="prueba@example.com",
            telefono="600123456",
            fecha=self.fecha,
            hora=hora,
            servicio="cena",
            personas=personas,
            zona=zona,
            estado=estado,
            cena_sistema_antiguo=antigua,
        )

    def test_dia_sin_cenas_anteriores(self):
        self.assertEqual(horas_llegada_para_turno(self.fecha, "cena"), [time(20, 10), time(21, 50)])
        for hora in (time(20, 10), time(21, 50)):
            self.assertEqual(plazas_libres_nueva_cena(self.fecha, hora), 10)
            self.assertTrue(hay_disponibilidad(self.fecha, hora, 2, "barra"))

    def test_cena_antigua_barra_conserva_horarios_y_aforo_antiguos(self):
        self.crear_reserva(time(20, 30), 2)
        horas = horas_llegada_para_turno(self.fecha, "cena")
        self.assertIn(time(20, 45), horas)
        self.assertNotIn(time(20, 10), horas)
        self.assertNotIn(time(21, 50), horas)
        self.assertTrue(hay_disponibilidad(self.fecha, time(20, 45), 6, "barra"))
        self.assertFalse(hay_disponibilidad(self.fecha, time(20, 45), 7, "barra"))
        self.assertEqual(plazas_libres_nueva_cena(self.fecha, time(20, 10)), 0)
        self.assertEqual(plazas_libres_nueva_cena(self.fecha, time(21, 50)), 0)

    def test_cena_antigua_mesa_permite_reservar_en_otra_mesa(self):
        self.crear_reserva(time(21, 0), 2, "mesa")
        self.assertTrue(hay_disponibilidad(self.fecha, time(20, 45), 2, "mesa"))
        self.assertFalse(hay_disponibilidad(self.fecha, time(20, 45), 3, "mesa"))
        zonas = zonas_disponibles(self.fecha, time(20, 45), 2)
        self.assertIn("mesa", [z["id"] for z in zonas])

    def test_cancelar_ultima_antigua_activa_nuevos_turnos(self):
        reserva = self.crear_reserva(time(20, 30), 2)
        self.assertIn(time(20, 45), horas_llegada_para_turno(self.fecha, "cena"))
        reserva.estado = "cancelada"
        reserva.save(update_fields=["estado"])
        self.assertEqual(horas_llegada_para_turno(self.fecha, "cena"), [time(20, 10), time(21, 50)])
        for hora in (time(20, 10), time(21, 50)):
            self.assertEqual(plazas_libres_nueva_cena(self.fecha, hora), 10)

    def test_cancelar_una_de_dos_antiguas_no_cambia_modo(self):
        primera = self.crear_reserva(time(20, 30), 2)
        self.crear_reserva(time(21, 0), 2, "mesa")
        primera.estado = "cancelada"
        primera.save(update_fields=["estado"])
        self.assertIn(time(20, 45), horas_llegada_para_turno(self.fecha, "cena"))
        self.assertNotIn(time(20, 10), horas_llegada_para_turno(self.fecha, "cena"))

    def test_cancelada_no_bloquea(self):
        self.crear_reserva(time(20, 30), 2, estado="cancelada")
        self.assertEqual(plazas_libres_nueva_cena(self.fecha, time(20, 10)), 10)

    def test_reservas_nuevas_mantienen_turnos(self):
        self.crear_reserva(time(20, 10), 8, antigua=False)
        self.assertEqual(horas_llegada_para_turno(self.fecha, "cena"), [time(20, 10), time(21, 50)])
        self.assertEqual(plazas_libres_nueva_cena(self.fecha, time(20, 10)), 2)
        self.assertEqual(plazas_libres_nueva_cena(self.fecha, time(21, 50)), 10)
        self.assertFalse(hay_disponibilidad(self.fecha, time(20, 30), 2, "barra"))

    def test_rechaza_horarios_y_zonas_antiguas_en_modo_nuevo(self):
        self.assertFalse(hay_disponibilidad(self.fecha, time(20, 30), 2, "barra"))
        self.assertFalse(hay_disponibilidad(self.fecha, time(20, 10), 2, "mesa"))
        self.assertFalse(hay_disponibilidad(self.fecha, time(20, 10), 11, "barra"))

    def test_aforo_maximo_diez_personas_por_turno(self):
        self.crear_reserva(time(20, 10), 6, antigua=False)
        self.assertEqual(plazas_libres_nueva_cena(self.fecha, time(20, 10)), 4)
        self.crear_reserva(time(20, 10), 4, antigua=False)
        self.assertEqual(plazas_libres_nueva_cena(self.fecha, time(20, 10)), 0)
        self.assertFalse(hay_disponibilidad(self.fecha, time(20, 10), 1, "barra"))
        self.assertEqual(plazas_libres_nueva_cena(self.fecha, time(21, 50)), 10)
        self.assertTrue(hay_disponibilidad(self.fecha, time(21, 50), 10, "barra"))

    def test_cliente_no_puede_modificar_cena_antigua(self):
        reserva = self.crear_reserva(time(21, 50), 3, zona="mesa", antigua=True)
        respuesta = self.client.post(
            reverse("editar_reserva_cliente", kwargs={"reserva_id": reserva.id}),
            {"accion": "confirmar", "fecha": self.fecha.isoformat(), "hora": "20:10",
             "servicio": "cena", "personas": "5", "zona": "barra"},
        )
        reserva.refresh_from_db()
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(reserva.hora, time(21, 50))
        self.assertEqual(reserva.personas, 3)
        self.assertEqual(reserva.zona, "mesa")
        self.assertTrue(reserva.cena_sistema_antiguo)


class TestHorarioComida(TestCase):
    def test_horas_comida_ambos_extremos_incluidos(self):
        self.assertEqual(horas_llegada_para_turno(date(2026, 10, 16), "comida"), [
            time(13, 30), time(13, 45), time(14, 0), time(14, 15),
            time(14, 30), time(14, 45), time(15, 0),
        ])

    def test_cena_nueva_con_diez_minutos_de_separacion(self):
        self.assertEqual(horas_llegada_para_turno(date(2026, 10, 16), "cena"),
                         [time(20, 10), time(21, 50)])

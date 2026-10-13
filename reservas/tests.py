from datetime import date, time

from django.test import TestCase

from reservas.models import Reserva
from reservas.services.disponibilidad import plazas_libres_nueva_cena
from reservas.services.disponibilidad import hay_disponibilidad

class TestNuevosTurnosCena(TestCase):
    def setUp(self):
        self.fecha = date(2026, 10, 16)

    def crear_reserva(self, hora, personas, zona="barra", antigua=True, estado="confirmada"):
        return Reserva.objects.create(
            nombre="Cliente de prueba", email="prueba@example.com",
            telefono="600123456", fecha=self.fecha, hora=hora,
            servicio="cena", personas=personas, zona=zona,
            estado=estado, cena_sistema_antiguo=antigua,
        )

    def test_dia_sin_cenas_anteriores(self):
        for hora in (time(20, 10), time(21, 50)):
            self.assertEqual(plazas_libres_nueva_cena(self.fecha, hora), 10)
            self.assertTrue(hay_disponibilidad(self.fecha, hora, 2, "barra"))

    def test_cena_antigua_barra_bloquea_ambos_turnos(self):
        self.crear_reserva(time(20, 30), 2)
        for hora in (time(20, 10), time(21, 50)):
            self.assertEqual(plazas_libres_nueva_cena(self.fecha, hora), 0)
            self.assertFalse(hay_disponibilidad(self.fecha, hora, 1, "barra"))

    def test_cena_antigua_mesa_tambien_bloquea(self):
        self.crear_reserva(time(21, 0), 2, "mesa")
        self.assertFalse(hay_disponibilidad(self.fecha, time(20, 10), 1, "barra"))

    def test_cancelada_no_bloquea(self):
        self.crear_reserva(time(20, 30), 2, estado="cancelada")
        self.assertEqual(plazas_libres_nueva_cena(self.fecha, time(20, 10)), 10)

    def test_nuevas_reservas_comparten_turno_y_no_bloquean_el_segundo(self):
        self.crear_reserva(time(20, 10), 8, antigua=False)
        self.assertEqual(plazas_libres_nueva_cena(self.fecha, time(20, 10)), 2)
        self.assertEqual(plazas_libres_nueva_cena(self.fecha, time(21, 50)), 10)

    def test_rechaza_horarios_y_zonas_antiguas(self):
        self.assertFalse(hay_disponibilidad(self.fecha, time(20, 30), 2, "barra"))
        self.assertFalse(hay_disponibilidad(self.fecha, time(20, 10), 2, "mesa"))
        self.assertFalse(hay_disponibilidad(self.fecha, time(20, 10), 11, "barra"))

    def test_aforo_maximo_diez_personas_por_turno(self):
        primer_turno = time(20, 10)
        segundo_turno = time(21, 50)

        # Primera reserva: 6 personas.
        self.crear_reserva(primer_turno, 6, antigua=False)

        self.assertEqual(
            plazas_libres_nueva_cena(self.fecha, primer_turno),
            4,
        )

        # Segunda reserva: 4 personas.
        self.crear_reserva(primer_turno, 4, antigua=False)

        # Primer turno completo.
        self.assertEqual(
            plazas_libres_nueva_cena(self.fecha, primer_turno),
            0,
        )

        # No se puede reservar ni una persona más.
        self.assertFalse(
            hay_disponibilidad(self.fecha, primer_turno, 1, "barra")
        )

        # El segundo turno sigue completamente disponible.
        self.assertEqual(
            plazas_libres_nueva_cena(self.fecha, segundo_turno),
            10,
        )

        self.assertTrue(
            hay_disponibilidad(self.fecha, segundo_turno, 10, "barra")
        )

    def test_cliente_no_puede_modificar_cena_antigua(self):
        from django.urls import reverse

        reserva = self.crear_reserva(
            time(21, 50),
            3,
            zona="mesa",
            antigua=True,
        )

        url = reverse(
            "editar_reserva_cliente",
            kwargs={"reserva_id": reserva.id},
        )

        respuesta = self.client.post(
            url,
            {
                "accion": "confirmar",
                "fecha": self.fecha.isoformat(),
                "hora": "20:10",
                "servicio": "cena",
                "personas": "5",
                "zona": "barra",
            },
        )

        reserva.refresh_from_db()

        self.assertEqual(respuesta.status_code, 302)
        mensajes = [str(mensaje) for mensaje in list(respuesta.wsgi_request._messages)]

        self.assertTrue(
            any(
                "Esta reserva pertenece al sistema anterior" in mensaje
                for mensaje in mensajes
            ),
            f"Se esperaba el bloqueo de reserva antigua. Mensajes: {mensajes}",
        )
        self.assertEqual(reserva.hora, time(21, 50))
        self.assertEqual(reserva.personas, 3)
        self.assertEqual(reserva.zona, "mesa")
        self.assertTrue(reserva.cena_sistema_antiguo)

class TestHorarioComida(TestCase):
    def test_horas_comida_ambos_extremos_incluidos(self):
        from reservas.services.disponibilidad import horas_llegada_para_turno

        horas = horas_llegada_para_turno(date(2026, 10, 16), "comida")
        self.assertEqual(horas, [
            time(13, 30), time(13, 45), time(14, 0),
            time(14, 15), time(14, 30), time(14, 45), time(15, 0),
        ])

    def test_cena_nueva_con_diez_minutos_de_separacion(self):
        from reservas.services.disponibilidad import horas_llegada_para_turno

        self.assertEqual(
            horas_llegada_para_turno(date(2026, 10, 16), "cena"),
            [time(20, 10), time(21, 50)],
        )


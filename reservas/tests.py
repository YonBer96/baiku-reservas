from datetime import date, time

from django.test import TestCase

from reservas.models import Reserva
from reservas.services.disponibilidad import plazas_libres_nueva_cena
from reservas.services.disponibilidad import hay_disponibilidad

class TestNuevosTurnosCena(TestCase):

    def setUp(self):
        self.fecha = date(2026, 10, 16)

    def crear_reserva(self, hora, personas, zona="barra"):
        return Reserva.objects.create(
            nombre="Cliente de prueba",
            email="prueba@example.com",
            telefono="600123456",
            fecha=self.fecha,
            hora=hora,
            servicio="cena",
            personas=personas,
            zona=zona,
            estado="confirmada",
        )

    def test_sin_reservas(self):
        self.assertEqual(
            plazas_libres_nueva_cena(self.fecha, time(20, 10)),
            10,
        )
        self.assertEqual(
            plazas_libres_nueva_cena(self.fecha, time(21, 50)),
            10,
        )

    def test_reserva_antigua_solapada(self):
        # Una reserva antigua de 4 personas a las 20:30.
        # Su ocupación provisional es 20:30–22:00.
        self.crear_reserva(time(20, 30), 4)

        self.assertEqual(
            plazas_libres_nueva_cena(self.fecha, time(20, 10)),
            6,
        )
        self.assertEqual(
            plazas_libres_nueva_cena(self.fecha, time(21, 50)),
            6,
        )

    def test_dos_reservas_antiguas(self):
        self.crear_reserva(time(20, 30), 4)
        self.crear_reserva(time(21, 0), 2)

        self.assertEqual(
            plazas_libres_nueva_cena(self.fecha, time(20, 10)),
            4,
        )
        self.assertEqual(
            plazas_libres_nueva_cena(self.fecha, time(21, 50)),
            4,
        )

    def test_turnos_independientes(self):
        # Una reserva nueva a las 20:10 termina a las 21:40.
        self.crear_reserva(time(20, 10), 8)

        self.assertEqual(
            plazas_libres_nueva_cena(self.fecha, time(20, 10)),
            2,
        )
        self.assertEqual(
            plazas_libres_nueva_cena(self.fecha, time(21, 50)),
            10,
        )

    def test_reserva_antigua_de_mesa(self):
        # Las mesas antiguas se respetan, pero no consumen
        # plazas físicas de barra.
        self.crear_reserva(time(20, 30), 4, "mesa")

        self.assertEqual(
            plazas_libres_nueva_cena(self.fecha, time(20, 10)),
            10,
        )

    def test_acepta_nuevos_turnos(self):
        self.assertTrue(
            hay_disponibilidad(self.fecha, time(20, 10), 2, "barra")
        )
        self.assertTrue(
            hay_disponibilidad(self.fecha, time(21, 50), 2, "barra")
        )

    def test_rechaza_horarios_antiguos(self):
        self.assertFalse(
            hay_disponibilidad(self.fecha, time(20, 30), 2, "barra")
        )
        self.assertFalse(
            hay_disponibilidad(self.fecha, time(21, 0), 2, "barra")
        )

    def test_rechaza_mesas_y_combinadas(self):
        self.assertFalse(
            hay_disponibilidad(self.fecha, time(20, 10), 2, "mesa")
        )
        self.assertFalse(
            hay_disponibilidad(self.fecha, time(20, 10), 4, "combinada:2:2")
        )

    def test_rechaza_mas_de_diez_personas(self):
        self.assertFalse(
            hay_disponibilidad(self.fecha, time(20, 10), 11, "barra")
        )

    def test_respeta_reservas_antiguas(self):
        self.crear_reserva(time(20, 30), 8)

        self.assertTrue(
            hay_disponibilidad(self.fecha, time(20, 10), 2, "barra")
        )
        self.assertFalse(
            hay_disponibilidad(self.fecha, time(20, 10), 3, "barra")
        )

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

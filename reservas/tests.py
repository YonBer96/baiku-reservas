from datetime import datetime, time, timedelta
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from reservas.models import Reserva
from reservas.services.disponibilidad import (
    hay_disponibilidad,
    horas_llegada_para_turno,
    obtener_turnos_reservables_para_fecha,
    plazas_disponibles,
    plazas_libres_nueva_cena,
    zonas_disponibles,
)
from reservas.views import SESION_RESERVAS_AUTORIZADAS


def proximo_viernes():
    """Un viernes futuro (hay comida y cena) para que los tests no caduquen."""
    fecha = timezone.localdate() + timedelta(days=7)
    while fecha.weekday() != 4:
        fecha += timedelta(days=1)
    return fecha


def autorizar_en_sesion(client, *reservas):
    session = client.session
    session[SESION_RESERVAS_AUTORIZADAS] = [r.id for r in reservas]
    session.save()


class TestModeloBCenas(TestCase):
    def setUp(self):
        self.fecha = proximo_viernes()

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
        autorizar_en_sesion(self.client, reserva)
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
        self.assertEqual(horas_llegada_para_turno(proximo_viernes(), "comida"), [
            time(13, 30), time(13, 45), time(14, 0), time(14, 15),
            time(14, 30), time(14, 45), time(15, 0),
        ])

    def test_cena_nueva_con_diez_minutos_de_separacion(self):
        self.assertEqual(horas_llegada_para_turno(proximo_viernes(), "cena"),
                         [time(20, 10), time(21, 50)])


def crear_reserva_cliente(fecha, **extra):
    datos = dict(
        nombre="Ana García",
        email="ana@example.com",
        telefono="612 345 678",
        fecha=fecha,
        hora=time(13, 30),
        servicio="comida",
        personas=2,
        zona="barra",
        personas_barra=2,
        estado="confirmada",
    )
    datos.update(extra)
    return Reserva.objects.create(**datos)


class TestPermisosCliente(TestCase):
    def setUp(self):
        self.fecha = proximo_viernes()
        self.reserva = crear_reserva_cliente(self.fecha)

    def test_no_se_puede_cancelar_reserva_ajena(self):
        respuesta = self.client.post(
            reverse("eliminar_reserva_cliente", args=[self.reserva.id])
        )
        self.reserva.refresh_from_db()
        self.assertRedirects(respuesta, reverse("gestionar_reserva"))
        self.assertEqual(self.reserva.estado, "confirmada")

    def test_no_se_puede_ver_edicion_de_reserva_ajena(self):
        respuesta = self.client.get(
            reverse("editar_reserva_cliente", args=[self.reserva.id])
        )
        self.assertRedirects(respuesta, reverse("gestionar_reserva"))

    def test_api_horas_requiere_autorizacion(self):
        respuesta = self.client.get(
            reverse("horas_edicion_reserva_api", args=[self.reserva.id]),
            {"fecha": self.fecha.isoformat(), "servicio": "comida"},
        )
        self.assertEqual(respuesta.status_code, 403)

    def test_buscar_y_cancelar_con_datos_escritos_de_otra_forma(self):
        # Mayúsculas en el email y teléfono con prefijo y espacios distintos.
        respuesta = self.client.post(
            reverse("gestionar_reserva"),
            {"email": "Ana@Example.com", "telefono": "612-345-678"},
        )
        self.assertContains(respuesta, "Modificar")

        respuesta = self.client.post(
            reverse("eliminar_reserva_cliente", args=[self.reserva.id])
        )
        self.reserva.refresh_from_db()
        self.assertEqual(self.reserva.estado, "cancelada")

    def test_buscar_con_datos_incorrectos_no_autoriza(self):
        self.client.post(
            reverse("gestionar_reserva"),
            {"email": "ana@example.com", "telefono": "699999999"},
        )
        self.client.post(reverse("eliminar_reserva_cliente", args=[self.reserva.id]))
        self.reserva.refresh_from_db()
        self.assertEqual(self.reserva.estado, "confirmada")


class TestZonaManipulada(TestCase):
    def setUp(self):
        self.fecha = proximo_viernes()
        session = self.client.session
        session["reserva"] = {
            "fecha": self.fecha.isoformat(),
            "personas": 2,
            "turno": "comida",
            "hora": "13:30",
        }
        session.save()

    def test_no_se_acepta_zona_completo_desde_el_formulario(self):
        respuesta = self.client.post(reverse("reserva_zona"), {"zona": "completo"})
        self.assertRedirects(respuesta, reverse("reserva_zona"))
        self.assertNotIn("zona", self.client.session["reserva"])

    def test_zona_ofrecida_si_se_acepta(self):
        respuesta = self.client.post(reverse("reserva_zona"), {"zona": "barra"})
        self.assertRedirects(respuesta, reverse("reserva_datos"))
        self.assertEqual(self.client.session["reserva"]["zona"], "barra")

    def test_comida_permite_elegir_mesa_con_sistema_nuevo_de_cenas(self):
        respuesta = self.client.post(reverse("reserva_zona"), {"zona": "mesa"})
        self.assertRedirects(respuesta, reverse("reserva_datos"))
        self.assertEqual(self.client.session["reserva"]["zona"], "mesa")

    def test_paso_hora_de_comida_lleva_a_elegir_zona(self):
        respuesta = self.client.post(reverse("reserva_hora"), {"hora": "13:30"})
        self.assertRedirects(respuesta, reverse("reserva_zona"))

    def test_resumen_muestra_fecha_legible(self):
        session = self.client.session
        session["reserva"].update({
            "zona": "barra", "nombre": "Ana", "email": "ana@example.com",
            "telefono": "612345678",
        })
        session.save()
        respuesta = self.client.get(reverse("reserva_resumen"))
        self.assertContains(respuesta, self.fecha.strftime("%d/%m/%Y"))
        self.assertContains(respuesta, "Viernes")

    def test_completo_no_disponible_si_hay_reservas(self):
        crear_reserva_cliente(self.fecha, hora=time(14, 0))
        self.assertEqual(plazas_disponibles(self.fecha, time(13, 30), "completo"), 0)
        self.assertFalse(hay_disponibilidad(self.fecha, time(13, 30), 2, "completo"))


class TestReservasMismoDia(TestCase):
    """A las 20:30 de hoy todavía debe poder reservarse el turno de 21:50."""

    def test_cena_nueva_sigue_reservable_tras_las_20(self):
        hoy = proximo_viernes()
        ahora = timezone.make_aware(datetime.combine(hoy, time(20, 30)))

        with mock.patch("reservas.services.disponibilidad.timezone.localdate", return_value=hoy), \
             mock.patch("reservas.services.disponibilidad.timezone.localtime", return_value=ahora):
            self.assertIn(time(20, 0), obtener_turnos_reservables_para_fecha(hoy))
            self.assertEqual(horas_llegada_para_turno(hoy, "cena"), [time(21, 50)])
            self.assertTrue(hay_disponibilidad(hoy, time(21, 50), 2, "barra"))
            self.assertFalse(hay_disponibilidad(hoy, time(20, 10), 2, "barra"))

    def test_comida_sigue_reservable_tras_las_13(self):
        hoy = proximo_viernes()
        ahora = timezone.make_aware(datetime.combine(hoy, time(13, 40)))

        with mock.patch("reservas.services.disponibilidad.timezone.localdate", return_value=hoy), \
             mock.patch("reservas.services.disponibilidad.timezone.localtime", return_value=ahora):
            self.assertIn(time(13, 0), obtener_turnos_reservables_para_fecha(hoy))
            self.assertNotIn(time(13, 30), horas_llegada_para_turno(hoy, "comida"))
            self.assertIn(time(13, 45), horas_llegada_para_turno(hoy, "comida"))


class TestStaffNavegacion(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        self.user = User.objects.create_user("staff", password="x", is_staff=True)
        self.client.force_login(self.user)

    def test_sumar_meses_ajusta_fin_de_mes(self):
        from datetime import date
        from reservas.views import _sumar_meses
        self.assertEqual(_sumar_meses(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(_sumar_meses(date(2026, 12, 15), 1), date(2027, 1, 15))
        self.assertEqual(_sumar_meses(date(2026, 1, 15), -1), date(2025, 12, 15))

    def test_staff_hoy_muestra_enlaces_de_mes(self):
        respuesta = self.client.get(reverse("staff_hoy"), {"fecha": "2026-03-31"})
        self.assertContains(respuesta, "?fecha=2026-02-28")
        self.assertContains(respuesta, "?fecha=2026-04-30")

    def test_cambiar_estado_vuelve_al_dia_de_la_reserva(self):
        reserva = crear_reserva_cliente(proximo_viernes())
        respuesta = self.client.post(
            reverse("cambiar_estado", args=[reserva.id]), {"estado": "llegado"}
        )
        self.assertRedirects(
            respuesta,
            f"{reverse('staff_hoy')}?fecha={reserva.fecha.isoformat()}",
        )


# =========================
# TEXTOS LEGALES Y RGPD
# =========================

class TestPaginaLegal(TestCase):
    def test_pagina_legal_disponible(self):
        respuesta = self.client.get(reverse("legal"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Aviso legal")
        self.assertContains(respuesta, "Política de privacidad")
        self.assertContains(respuesta, "Política de cookies")
        self.assertContains(respuesta, "77184428Q")

    def test_inicio_enlaza_a_legal(self):
        respuesta = self.client.get(reverse("inicio"))
        self.assertContains(respuesta, reverse("legal"))


class TestConsentimientosFormulario(TestCase):
    def setUp(self):
        session = self.client.session
        session["reserva"] = {
            "fecha": proximo_viernes().isoformat(),
            "zona": "barra",
            "personas": 2,
        }
        session.save()

    def _post(self, **extra):
        datos = {
            "nombre": "Ana García",
            "email": "ana@example.com",
            "email_confirmacion": "ana@example.com",
            "telefono": "612345678",
            "notas": "",
        }
        datos.update(extra)
        return self.client.post(reverse("reserva_datos"), datos)

    def test_formulario_muestra_casillas(self):
        respuesta = self.client.get(reverse("reserva_datos"))
        self.assertContains(respuesta, 'name="acepta_privacidad"')
        self.assertContains(respuesta, 'name="consiente_alergias"')

    def test_sin_aceptar_privacidad_no_avanza(self):
        respuesta = self._post()
        self.assertRedirects(respuesta, reverse("reserva_datos"), fetch_redirect_response=False)

    def test_notas_sin_consentimiento_no_avanza(self):
        respuesta = self._post(acepta_privacidad="1", notas="Alergia al marisco")
        self.assertRedirects(respuesta, reverse("reserva_datos"), fetch_redirect_response=False)
        self.assertNotIn("notas", self.client.session.get("reserva", {}))

    def test_notas_con_consentimiento_avanza(self):
        respuesta = self._post(
            acepta_privacidad="1",
            consiente_alergias="1",
            notas="Alergia al marisco",
        )
        self.assertRedirects(respuesta, reverse("reserva_resumen"), fetch_redirect_response=False)
        self.assertEqual(self.client.session["reserva"]["notas"], "Alergia al marisco")

    def test_sin_notas_no_pide_consentimiento_alergias(self):
        respuesta = self._post(acepta_privacidad="1")
        self.assertRedirects(respuesta, reverse("reserva_resumen"), fetch_redirect_response=False)


class TestLimpiarDatos(TestCase):
    def test_vacia_notas_y_anonimiza_por_antiguedad(self):
        from django.core.management import call_command

        hoy = timezone.localdate()
        reciente = crear_reserva_cliente(hoy - timedelta(days=5), notas="Celíaco")
        mes = crear_reserva_cliente(hoy - timedelta(days=40), notas="Celíaco")
        anio = crear_reserva_cliente(hoy - timedelta(days=400), notas="Celíaco")

        call_command("limpiar_datos", stdout=open(__import__("os").devnull, "w"))

        for r in (reciente, mes, anio):
            r.refresh_from_db()

        self.assertEqual(reciente.notas, "Celíaco")
        self.assertEqual(reciente.nombre, "Ana García")

        self.assertEqual(mes.notas, "")
        self.assertEqual(mes.nombre, "Ana García")

        self.assertEqual(anio.notas, "")
        self.assertEqual(anio.nombre, "Anonimizado")
        self.assertEqual(anio.telefono, "")
        self.assertNotEqual(anio.email, "ana@example.com")

    def test_dry_run_no_modifica(self):
        from django.core.management import call_command

        hoy = timezone.localdate()
        vieja = crear_reserva_cliente(hoy - timedelta(days=400), notas="Celíaco")
        call_command("limpiar_datos", "--dry-run", stdout=open(__import__("os").devnull, "w"))
        vieja.refresh_from_db()
        self.assertEqual(vieja.nombre, "Ana García")
        self.assertEqual(vieja.notas, "Celíaco")

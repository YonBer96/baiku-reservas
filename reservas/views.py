import calendar
import logging
from datetime import datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .configuracion_turnos import TURNOS_CENA as TURNOS_CENA_NUEVOS
from .emails import (
    enviar_email_cancelacion,
    enviar_email_confirmacion,
    enviar_email_modificacion,
)
from .models import BloqueoDia, Reserva
from .services.disponibilidad import (
    CAPACIDAD_BARRA,
    MAX_PERSONAS_RESERVA,
    bloquear_fecha_para_reservar,
    cena_con_sistema_nuevo,
    datos_combinada,
    hay_disponibilidad,
    horas_llegada_para_turno,
    periodo_de_hora,
    resumen_calendario_dia,
    turnos_disponibles,
    zona_base,
    zonas_disponibles,
)
from .utils import normalizar_telefono, normalizar_texto

logger = logging.getLogger(__name__)

# Clave de sesión con los IDs de reservas que el cliente ha demostrado que son
# suyas (buscándolas con email + teléfono o creándolas en esta sesión).
SESION_RESERVAS_AUTORIZADAS = "reservas_autorizadas"

SEGUNDOS_CACHE_CALENDARIO = 30

PASOS_RESERVA = {
    "fecha": {"numero": 1, "total": 7, "titulo": "Fecha"},
    "personas": {"numero": 2, "total": 7, "titulo": "Personas"},
    "turno": {"numero": 3, "total": 7, "titulo": "Servicio"},
    "hora": {"numero": 4, "total": 7, "titulo": "Hora"},
    "zona": {"numero": 5, "total": 7, "titulo": "Zona"},
    "datos": {"numero": 6, "total": 7, "titulo": "Datos"},
    "resumen": {"numero": 7, "total": 7, "titulo": "Confirmar"},
}


# ==================================================
# PERMISOS DEL CLIENTE SOBRE SUS RESERVAS
# ==================================================

def _autorizar_reservas(request, ids):
    autorizadas = set(request.session.get(SESION_RESERVAS_AUTORIZADAS, []))
    autorizadas.update(int(i) for i in ids)
    request.session[SESION_RESERVAS_AUTORIZADAS] = sorted(autorizadas)
    request.session.modified = True


def _reserva_autorizada(request, reserva_id):
    if request.user.is_authenticated and request.user.is_staff:
        return True
    return int(reserva_id) in request.session.get(SESION_RESERVAS_AUTORIZADAS, [])


def _reserva_ya_pasada(reserva):
    ahora = timezone.localtime()
    return (
        reserva.fecha < ahora.date()
        or (reserva.fecha == ahora.date() and reserva.hora <= ahora.time())
    )


def calendario_reservas_api(request):
    """Devuelve próximos días con estado para calendario visual."""
    inicio_txt = request.GET.get("inicio")
    dias_txt = request.GET.get("dias", "90")
    excluir_txt = request.GET.get("excluir_reserva_id")

    try:
        excluir_reserva_id = int(excluir_txt) if excluir_txt else None
    except (TypeError, ValueError):
        excluir_reserva_id = None

    # Solo se puede "liberar" en el cálculo una reserva propia.
    if excluir_reserva_id and not _reserva_autorizada(request, excluir_reserva_id):
        excluir_reserva_id = None

    hoy = timezone.localdate()

    try:
        fecha_inicio = _fecha_desde_texto(inicio_txt) if inicio_txt else hoy
    except (TypeError, ValueError):
        fecha_inicio = hoy

    fecha_inicio = max(fecha_inicio, hoy)

    try:
        dias = min(max(int(dias_txt), 1), 90)
    except (TypeError, ValueError):
        dias = 45

    # El cálculo hace muchas consultas por día: se cachea unos segundos.
    # La disponibilidad real se vuelve a comprobar siempre al confirmar.
    clave_cache = f"calendario:{fecha_inicio.isoformat()}:{dias}:{excluir_reserva_id or 0}"
    respuesta = cache.get(clave_cache)
    if respuesta is not None:
        return JsonResponse(respuesta)

    datos = []
    for offset in range(dias):
        fecha = fecha_inicio + timedelta(days=offset)
        resumen = resumen_calendario_dia(fecha, excluir_reserva_id)
        datos.append({
            "fecha": fecha.isoformat(),
            "dia": fecha.day,
            "nombre": fecha.strftime("%a"),
            "estado": resumen["estado"],
            "vacantes": resumen["vacantes"],
            "total": resumen["total"],
            "bloqueado": resumen["bloqueado"],
            "detalles": resumen["detalles"],
            "mensaje": resumen["mensaje"],
        })

    respuesta = {"dias": datos}
    cache.set(clave_cache, respuesta, SEGUNDOS_CACHE_CALENDARIO)
    return JsonResponse(respuesta)



def horas_edicion_reserva_api(request, reserva_id):
    """Devuelve las horas válidas al cambiar fecha o servicio al editar."""
    if not _reserva_autorizada(request, reserva_id):
        return JsonResponse({"horas": [], "error": "No autorizado."}, status=403)

    reserva = get_object_or_404(Reserva, id=reserva_id)
    fecha_txt = request.GET.get("fecha")
    servicio = request.GET.get("servicio")

    try:
        fecha = _fecha_desde_texto(fecha_txt)
    except (TypeError, ValueError):
        return JsonResponse({"horas": [], "error": "Fecha no válida."}, status=400)

    if fecha < timezone.localdate():
        return JsonResponse({"horas": [], "error": "La fecha ya ha pasado."}, status=400)

    motivo = _motivo_fecha_no_reservable(fecha)
    if motivo:
        return JsonResponse({"horas": [], "error": motivo}, status=400)

    if servicio not in {"comida", "cena"}:
        return JsonResponse({"horas": [], "error": "Servicio no válido."}, status=400)

    horas = horas_llegada_para_turno(
        fecha,
        servicio,
        excluir_reserva_id=reserva.id,
    )

    # Conserva la hora actual únicamente cuando sigue perteneciendo a la
    # misma fecha y servicio. La propia reserva ya está excluida del cálculo.
    servicio_actual = reserva.servicio or periodo_de_hora(reserva.hora)
    if (
        fecha == reserva.fecha
        and servicio == servicio_actual
        and reserva.hora not in horas
    ):
        horas.insert(0, reserva.hora)

    return JsonResponse({
        "horas": [hora.strftime("%H:%M") for hora in horas],
    })

def inicio(request):
    return render(request, "reservas/inicio.html")


def obtener_reserva_session(request):
    return request.session.get("reserva", {})


def guardar_reserva_session(request, datos):
    reserva = obtener_reserva_session(request)
    reserva.update(datos)
    request.session["reserva"] = reserva
    request.session.modified = True


def _fecha_desde_texto(fecha_txt):
    return datetime.strptime(fecha_txt, "%Y-%m-%d").date()


def _hora_desde_texto(hora_txt):
    return datetime.strptime(hora_txt, "%H:%M").time()


def _zona_nombre(zona):
    zona_normalizada = zona_base(zona)
    return dict(Reserva.ZONA_CHOICES).get(zona_normalizada, zona_normalizada)


def _normalizar_zona_para_guardar(zona, personas):
    """
    Convierte la opción elegida en el formulario a datos guardables en BD.

    Ejemplo:
    - "combinada:8:3" -> zona="combinada", personas_barra=8, personas_mesa=3
    - "barra" -> zona="barra", personas_barra=personas, personas_mesa=0
    - "mesa" -> zona="mesa", personas_barra=0, personas_mesa=personas
    """
    zona_normalizada = zona_base(zona)

    if zona_normalizada == "combinada":
        datos = datos_combinada(zona)

        if not datos:
            
            personas_barra = min(personas, CAPACIDAD_BARRA)
            personas_mesa = max(personas - personas_barra, 0)
        else:
            personas_barra = datos["personas_barra"]
            personas_mesa = datos["personas_mesa"]

        descripcion = f"Barra + mesas · {personas_barra} en barra + {personas_mesa} en mesa"

        return {
            "zona": "combinada",
            "personas_barra": personas_barra,
            "personas_mesa": personas_mesa,
            "zona_nombre": descripcion,
        }

    if zona_normalizada == "barra":
        return {
            "zona": "barra",
            "personas_barra": personas,
            "personas_mesa": 0,
            "zona_nombre": "Barra",
        }

    if zona_normalizada == "mesa":
        return {
            "zona": "mesa",
            "personas_barra": 0,
            "personas_mesa": personas,
            "zona_nombre": "Mesa" if personas <= 2 else "Mesas juntas",
        }

    return {
        "zona": zona_normalizada,
        "personas_barra": 0,
        "personas_mesa": 0,
        "zona_nombre": _zona_nombre(zona_normalizada),
    }


def _enviar_email_seguro(funcion_email, reserva):
    """Envía email sin romper la reserva si Gmail/SMTP falla."""
    if not reserva.email or reserva.email == "staff@baiku.local":
        return False

    try:
        funcion_email(reserva)
        return True
    except Exception as exc:
        logger.exception("Error enviando email para la reserva %s: %s", reserva.id, exc)
        return False


def _motivo_fecha_no_reservable(fecha):
    """Devuelve un texto claro si la fecha no se puede reservar."""
    if fecha.weekday() in [0, 1]:
        return "Estamos de vacaciones lunes y martes."

    bloqueo = BloqueoDia.objects.filter(fecha=fecha).first()
    if bloqueo:
        motivo = bloqueo.motivo or "día no disponible"
        return f"Día bloqueado: {motivo}"

    return ""


def reserva_fecha(request):
    reserva = obtener_reserva_session(request)

    if request.method == "POST":
        fecha_txt = request.POST.get("fecha")

        try:
            fecha = _fecha_desde_texto(fecha_txt)
        except (TypeError, ValueError):
            messages.error(request, "Fecha no válida.")
            return redirect("reserva_fecha")

        if fecha < timezone.localdate():
            messages.error(request, "No puedes reservar una fecha pasada.")
            return redirect("reserva_fecha")

        motivo_no_reservable = _motivo_fecha_no_reservable(fecha)
        if motivo_no_reservable:
            messages.error(request, motivo_no_reservable)
            return redirect("reserva_fecha")

        # Se permite reservar el mismo día mientras queden horas futuras disponibles.
        # La validación fina se hace en el paso de hora y en hay_disponibilidad().

        request.session["reserva"] = {"fecha": fecha_txt}
        request.session.modified = True

        return redirect("reserva_personas")

    return render(
        request,
        "reservas/reserva_fecha.html",
        {
            "reserva": reserva,
            "paso": PASOS_RESERVA["fecha"],
            "hoy": timezone.localdate(),
        },
    )


def reserva_personas(request):
    reserva = obtener_reserva_session(request)

    if not reserva.get("fecha"):
        return redirect("reserva_fecha")

    fecha = _fecha_desde_texto(reserva["fecha"])
    motivo_no_reservable = _motivo_fecha_no_reservable(fecha)
    if fecha < timezone.localdate() or motivo_no_reservable:
        messages.error(request, motivo_no_reservable or "No puedes reservar una fecha pasada.")
        return redirect("reserva_fecha")

    if request.method == "POST":
        try:
            personas = int(request.POST.get("personas", 0))
        except (TypeError, ValueError):
            personas = 0

        if personas < 1 or personas > MAX_PERSONAS_RESERVA:
            messages.error(request, f"Elige entre 1 y {MAX_PERSONAS_RESERVA} personas.")
            return redirect("reserva_personas")

        turnos = turnos_disponibles(fecha, personas)
        if not turnos:
            messages.error(request, "No hay turnos disponibles para esa fecha y número de personas.")
            return redirect("reserva_personas")

        guardar_reserva_session(request, {"personas": personas})
        return redirect("reserva_turno")

    return render(
        request,
        "reservas/reserva_personas.html",
        {"reserva": reserva, "paso": PASOS_RESERVA["personas"], "max_personas": MAX_PERSONAS_RESERVA},
    )


def reserva_turno(request):
    reserva = obtener_reserva_session(request)

    if not reserva.get("fecha"):
        return redirect("reserva_fecha")
    if not reserva.get("personas"):
        return redirect("reserva_personas")

    fecha = _fecha_desde_texto(reserva["fecha"])
    personas = int(reserva["personas"])
    turnos = turnos_disponibles(fecha, personas)

    servicios_disponibles = []
    for turno in turnos:
        if turno["hora"] == time(13, 0):
            servicios_disponibles.append({"id": "comida", "nombre": "Comida", "descripcion": "Servicio mediodía"})
        elif turno["hora"] == time(20, 0):
            servicios_disponibles.append({"id": "cena", "nombre": "Cena", "descripcion": "Servicio noche"})

    if request.method == "POST":
        turno_tipo = request.POST.get("turno")
        servicios_validos = [servicio["id"] for servicio in servicios_disponibles]

        if turno_tipo not in servicios_validos:
            messages.error(request, "Ese servicio ya no está disponible.")
            return redirect("reserva_turno")

        reserva.pop("hora", None)
        reserva.pop("zona", None)
        reserva.pop("zona_opcion", None)
        reserva.pop("zona_nombre", None)
        reserva.pop("personas_barra", None)
        reserva.pop("personas_mesa", None)
        reserva["turno"] = turno_tipo
        request.session["reserva"] = reserva
        request.session.modified = True
        return redirect("reserva_hora")

    return render(
        request,
        "reservas/reserva_turno.html",
        {
            "reserva": reserva,
            "servicios": servicios_disponibles,
            "paso": PASOS_RESERVA["turno"],
        },
    )


def reserva_hora(request):
    reserva = obtener_reserva_session(request)

    if not reserva.get("fecha"):
        return redirect("reserva_fecha")
    if not reserva.get("personas"):
        return redirect("reserva_personas")
    if not reserva.get("turno"):
        return redirect("reserva_turno")

    fecha = _fecha_desde_texto(reserva["fecha"])
    personas = int(reserva["personas"])
    turno_tipo = reserva["turno"]

    # Solo mostramos horas que todavía son válidas y con disponibilidad.
    horas = [
        h for h in horas_llegada_para_turno(fecha, turno_tipo)
        if zonas_disponibles(fecha, h, personas)
    ]

    if request.method == "POST":
        hora_txt = request.POST.get("hora")

        try:
            hora = _hora_desde_texto(hora_txt)
        except (TypeError, ValueError):
            messages.error(request, "Hora no válida.")
            return redirect("reserva_hora")

        if hora not in horas:
            messages.error(request, "Esa hora no pertenece al servicio elegido.")
            return redirect("reserva_hora")

        if not zonas_disponibles(fecha, hora, personas):
            messages.error(request, "No hay disponibilidad para esa hora.")
            return redirect("reserva_hora")

        guardar_reserva_session(request, {"hora": hora_txt})

        # En las cenas nuevas solo existe una zona: barra.
        # La asignamos sin mostrar un paso innecesario al cliente.
        # (Solo en cena: la comida sigue eligiendo barra / mesa.)
        if turno_tipo == "cena" and cena_con_sistema_nuevo(fecha):
            guardar_reserva_session(request, {
                "zona": "barra",
                "zona_opcion": "barra",
                "zona_nombre": "Barra",
                "personas_barra": personas,
                "personas_mesa": 0,
            })
            return redirect("reserva_datos")

        return redirect("reserva_zona")

    return render(
        request,
        "reservas/reserva_hora.html",
        {
            "reserva": reserva,
            "horas": horas,
            "turno": turno_tipo,
            "paso": PASOS_RESERVA["hora"],
        },
    )

def reserva_zona(request):
    reserva = obtener_reserva_session(request)

    if not reserva.get("turno"):
        return redirect("reserva_turno")
    if not reserva.get("hora"):
        return redirect("reserva_hora")

    fecha = _fecha_desde_texto(reserva["fecha"])
    hora = _hora_desde_texto(reserva["hora"])
    personas = int(reserva["personas"])
    zonas = zonas_disponibles(fecha, hora, personas)

    if periodo_de_hora(hora) == "cena" and cena_con_sistema_nuevo(fecha):
        if not hay_disponibilidad(fecha, hora, personas, "barra"):
            messages.error(request, "Ese horario ya no tiene plazas disponibles.")
            return redirect("reserva_hora")
        guardar_reserva_session(request, {
            "zona": "barra",
            "zona_opcion": "barra",
            "zona_nombre": "Barra",
            "personas_barra": personas,
            "personas_mesa": 0,
        })
        return redirect("reserva_datos")

    if request.method == "POST":
        zona = request.POST.get("zona")

        # Solo se aceptan las zonas que se le han ofrecido al cliente.
        if zona not in {opcion["id"] for opcion in zonas}:
            messages.error(request, "Esa zona ya no está disponible.")
            return redirect("reserva_zona")

        if not hay_disponibilidad(fecha, hora, personas, zona):
            messages.error(request, "Esa zona ya no está disponible.")
            return redirect("reserva_zona")

        datos_zona = _normalizar_zona_para_guardar(zona, personas)
        datos_zona["zona_opcion"] = zona

        guardar_reserva_session(request, datos_zona)
        return redirect("reserva_datos")

    return render(
        request,
        "reservas/reserva_zona.html",
        {"reserva": reserva, "zonas": zonas, "paso": PASOS_RESERVA["zona"]},
    )


def reserva_datos(request):
    reserva = obtener_reserva_session(request)

    if not reserva.get("zona"):
        return redirect("reserva_zona")

    if request.method == "POST":
        nombre = request.POST.get("nombre", "").strip()
        email = request.POST.get("email", "").strip().lower()
        email_confirmacion = request.POST.get("email_confirmacion", "").strip().lower()
        telefono = request.POST.get("telefono", "").strip()
        notas = request.POST.get("notas", "").strip()

        if not nombre or not email or not email_confirmacion or not telefono:
            messages.error(request, "Completa nombre, email, confirmación de email y teléfono.")
            return redirect("reserva_datos")

        if email != email_confirmacion:
            messages.error(request, "Los emails no coinciden. Revisa los datos.")
            return redirect("reserva_datos")

        try:
            validate_email(email)
        except ValidationError:
            messages.error(request, "El email no es válido.")
            return redirect("reserva_datos")

        if len(normalizar_telefono(telefono)) < 9:
            messages.error(request, "El teléfono no es válido.")
            return redirect("reserva_datos")

        # RGPD: aceptación de la política de privacidad.
        if not request.POST.get("acepta_privacidad"):
            messages.error(
                request,
                "Para continuar debes aceptar la política de privacidad y el aviso legal.",
            )
            return redirect("reserva_datos")

        # RGPD art. 9: las alergias son datos de salud y necesitan
        # consentimiento explícito. Sin él no se guardan las notas.
        if notas and not request.POST.get("consiente_alergias"):
            messages.error(
                request,
                "Para guardar las notas (alergias, intolerancias…) "
                "marca la casilla de consentimiento.",
            )
            return redirect("reserva_datos")

        guardar_reserva_session(
            request,
            {"nombre": nombre, "email": email, "telefono": telefono, "notas": notas},
        )
        return redirect("reserva_resumen")

    return render(
        request,
        "reservas/reserva_datos.html",
        {"reserva": reserva, "paso": PASOS_RESERVA["datos"]},
    )


def reserva_resumen(request):
    reserva = obtener_reserva_session(request)
    campos = ["personas", "fecha", "hora", "zona", "nombre", "email", "telefono"]

    if not all(reserva.get(campo) for campo in campos):
        return redirect("reserva_fecha")

    reserva["zona_nombre"] = reserva.get("zona_nombre") or _zona_nombre(reserva["zona"])

    return render(
        request,
        "reservas/reserva_resumen.html",
        {
            "reserva": reserva,
            "fecha": _fecha_desde_texto(reserva["fecha"]),
            "paso": PASOS_RESERVA["resumen"],
        },
    )


def _sumar_meses(fecha, meses):
    """Mismo día en otro mes; si no existe (31 → 30), usa el último día del mes."""
    mes_total = fecha.month - 1 + meses
    anio = fecha.year + mes_total // 12
    mes = mes_total % 12 + 1
    ultimo_dia = calendar.monthrange(anio, mes)[1]
    return fecha.replace(year=anio, month=mes, day=min(fecha.day, ultimo_dia))


@login_required
def staff_hoy(request):
    fecha_txt = request.GET.get("fecha")

    if fecha_txt:
        try:
            fecha = _fecha_desde_texto(fecha_txt)
        except ValueError:
            messages.error(request, "Fecha no válida.")
            fecha = timezone.localdate()
    else:
        fecha = timezone.localdate()

    reservas = (
    Reserva.objects
    .filter(
        fecha=fecha,
        estado__in=[
            "pendiente_pago",
            "confirmada",
            "llegado",
        ],
     )
        .order_by("hora", "zona", "nombre")
    )

    total_personas = sum(r.personas for r in reservas)

    context = {
        "fecha": fecha,
        "fecha_anterior": fecha - timedelta(days=1),
        "fecha_siguiente": fecha + timedelta(days=1),
        "mes_anterior": _sumar_meses(fecha, -1),
        "mes_siguiente": _sumar_meses(fecha, 1),
        "reservas": reservas,
        "total_reservas": reservas.count(),
        "total_personas": total_personas,
        "hoy": timezone.localdate(),
    }

    return render(request, "reservas/staff_hoy.html", context)

@login_required
def staff_buscar_reserva(request):
    query = request.GET.get("q", "").strip()

    reservas = Reserva.objects.none()

    if query:
        hoy = timezone.localdate()

        query_texto = normalizar_texto(query)
        query_telefono = normalizar_telefono(query)

        # Solo reservas de hoy en adelante
        reservas_base = Reserva.objects.filter(
            fecha__gte=hoy
        )

        ids_encontrados = []

        for reserva in reservas_base:

            # -------------------------
            # BUSCAR POR TEXTO
            # -------------------------
            campos_texto = [
                reserva.nombre,
                reserva.email,
                reserva.estado,
                reserva.zona,
                reserva.servicio,
            ]

            coincide_texto = any(
                query_texto in normalizar_texto(campo)
                for campo in campos_texto
                if campo
            )

            # -------------------------
            # BUSCAR POR TELÉFONO
            # -------------------------
            coincide_telefono = False

            # Solo buscamos por teléfono si el usuario
            # realmente ha escrito algún número
            if query_telefono:
                coincide_telefono = (
                    query_telefono
                    in normalizar_telefono(reserva.telefono)
                )

            # -------------------------
            # BUSCAR POR ID
            # -------------------------
            coincide_id = False

            if query.isdigit():
                coincide_id = reserva.id == int(query)

            # -------------------------
            # GUARDAR COINCIDENCIA
            # -------------------------
            if (
                coincide_texto
                or coincide_telefono
                or coincide_id
            ):
                ids_encontrados.append(reserva.id)

        reservas = (
            Reserva.objects
            .filter(
                id__in=ids_encontrados,
                fecha__gte=hoy,
            )
            .order_by("fecha", "hora")
        )

    return render(
        request,
        "reservas/staff_buscar_reserva.html",
        {
            "reservas": reservas,
            "query": query,
        },
    )



@login_required
@require_POST
def cambiar_estado(request, reserva_id):
    reserva = get_object_or_404(Reserva, id=reserva_id)
    nuevo_estado = request.POST.get("estado")

    if nuevo_estado in ["confirmada", "llegado", "no_show", "cancelada"]:
        reserva.estado = nuevo_estado
        reserva.save(update_fields=["estado", "actualizado"])

    # Volver al día de la reserva, no siempre a hoy.
    return redirect(f"{reverse('staff_hoy')}?fecha={reserva.fecha.isoformat()}")





@login_required
@require_POST
def bloquear_dia(request):
    fecha_txt = request.POST.get("fecha")
    motivo = request.POST.get("motivo", "").strip()

    try:
        fecha = _fecha_desde_texto(fecha_txt)
    except (TypeError, ValueError):
        messages.error(request, "Fecha no válida.")
        return redirect("staff_hoy")

    BloqueoDia.objects.update_or_create(fecha=fecha, defaults={"motivo": motivo})
    messages.success(request, "Día bloqueado correctamente.")

    return redirect(f"{reverse('staff_hoy')}?fecha={fecha.isoformat()}")




@require_POST
def confirmar_reserva(request):
    reserva_data = obtener_reserva_session(request)
    campos = ["personas", "fecha", "hora", "zona", "nombre", "email", "telefono"]

    if not all(reserva_data.get(campo) for campo in campos):
        return redirect("reserva_fecha")

    fecha = _fecha_desde_texto(reserva_data["fecha"])
    hora = _hora_desde_texto(reserva_data["hora"])
    personas = int(reserva_data["personas"])
    zona = reserva_data["zona"]
    zona_opcion = reserva_data.get("zona_opcion", zona)
    datos_zona = _normalizar_zona_para_guardar(zona_opcion, personas)

    with transaction.atomic():
        bloquear_fecha_para_reservar(fecha)

        if not hay_disponibilidad(fecha, hora, personas, zona_opcion):
            messages.error(request, "Lo sentimos, esa opción ya no está disponible.")
            return redirect("reserva_fecha")

        reserva = Reserva.objects.create(
            nombre=reserva_data["nombre"],
            email=reserva_data["email"],
            telefono=reserva_data["telefono"],
            personas=personas,
            fecha=fecha,
            hora=hora,
            servicio=periodo_de_hora(hora) or reserva_data.get("turno", "comida"),
            cena_sistema_antiguo=not cena_con_sistema_nuevo(fecha),
            zona=datos_zona["zona"],
            personas_barra=datos_zona["personas_barra"],
            personas_mesa=datos_zona["personas_mesa"],
            notas=reserva_data.get("notas", ""),
            importe_anticipo=0,
            estado="confirmada",
            expira_en=None,
        )

    _enviar_email_seguro(enviar_email_confirmacion, reserva)

    request.session.pop("reserva", None)
    _autorizar_reservas(request, [reserva.id])
    return render(request, "reservas/pago_exito.html")


def gestionar_reserva(request):
    reservas = []

    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        # El teléfono se guarda solo con dígitos (ver Reserva.save).
        telefono = normalizar_telefono(request.POST.get("telefono", ""))

        if email and telefono:
            reservas = list(
                Reserva.objects.filter(
                    email__iexact=email,
                    telefono=telefono,
                    fecha__gte=timezone.localdate(),
                ).exclude(
                    estado__in=["cancelada", "no_show"]
                ).order_by("fecha", "hora")
            )

        if reservas:
            _autorizar_reservas(request, [r.id for r in reservas])
        else:
            messages.error(request, "No hemos encontrado reservas con esos datos.")

    return render(request, "reservas/gestionar_reserva.html", {"reservas": reservas})




@require_POST
def eliminar_reserva_cliente(request, reserva_id):
    if not _reserva_autorizada(request, reserva_id):
        messages.error(request, "Busca tu reserva con tu email y teléfono para gestionarla.")
        return redirect("gestionar_reserva")

    reserva = get_object_or_404(Reserva, id=reserva_id)

    if _reserva_ya_pasada(reserva):
        messages.error(request, "Esta reserva ya ha pasado y no se puede cancelar.")
        return redirect("gestionar_reserva")

    if reserva.estado == "cancelada":
        messages.error(
            request,
            "Esta reserva ya se encuentra cancelada."
        )
        return redirect("gestionar_reserva")

    reserva.estado = "cancelada"
    reserva.recordatorio_enviado = False

    reserva.save(
        update_fields=[
            "estado",
            "recordatorio_enviado",
            "actualizado",
        ]
    )

    _enviar_email_seguro(
        enviar_email_cancelacion,
        reserva
    )

    messages.success(
        request,
        "Reserva cancelada correctamente."
    )

    return redirect("gestionar_reserva")


def editar_reserva_cliente(request, reserva_id):
    if not _reserva_autorizada(request, reserva_id):
        messages.error(request, "Busca tu reserva con tu email y teléfono para gestionarla.")
        return redirect("gestionar_reserva")

    reserva = get_object_or_404(Reserva, id=reserva_id)

    if reserva.estado == "cancelada":
        messages.error(
            request,
            "No se puede modificar una reserva cancelada."
        )
        return redirect("gestionar_reserva")

    if _reserva_ya_pasada(reserva):
        messages.error(request, "Esta reserva ya ha pasado y no se puede modificar.")
        return redirect("gestionar_reserva")

    servicio_actual = (
        reserva.servicio
        or periodo_de_hora(reserva.hora)
        or "comida"
    )

    if request.method == "POST":
        accion = request.POST.get("accion")

        if accion not in {"volver_edicion", "buscar_zonas", "confirmar"}:
            messages.error(request, "Acción no válida.")
            return redirect(
                "editar_reserva_cliente",
                reserva_id=reserva.id,
            )

        try:
            personas = int(request.POST.get("personas", 0))
            fecha = _fecha_desde_texto(request.POST.get("fecha"))
        except (TypeError, ValueError):
            messages.error(request, "Datos de reserva no válidos.")
            return redirect(
                "editar_reserva_cliente",
                reserva_id=reserva.id,
            )

        servicio_form = (
            request.POST.get("servicio")
            or servicio_actual
        )

        hora_txt = request.POST.get("hora") or ""

        # Si no se envía hora y se mantiene el servicio,
        # utilizamos la hora original.
        if not hora_txt and servicio_form == servicio_actual:
            hora = reserva.hora
        else:
            try:
                hora = _hora_desde_texto(hora_txt)
            except (TypeError, ValueError):
                messages.error(
                    request,
                    "Selecciona una hora válida."
                )
                return redirect(
                    "editar_reserva_cliente",
                    reserva_id=reserva.id,
                )

        # --------------------------------------------------
        # 1. VALIDACIONES GENERALES
        # --------------------------------------------------

        if personas < 1 or personas > MAX_PERSONAS_RESERVA:
            messages.error(
                request,
                f"Elige entre 1 y {MAX_PERSONAS_RESERVA} personas."
            )
            return redirect(
                "editar_reserva_cliente",
                reserva_id=reserva.id,
            )

        motivo_no_reservable = _motivo_fecha_no_reservable(fecha)

        if fecha < timezone.localdate() or motivo_no_reservable:
            messages.error(
                request,
                motivo_no_reservable
                or "No puedes reservar una fecha pasada."
            )
            return redirect(
                "editar_reserva_cliente",
                reserva_id=reserva.id,
            )

        if servicio_form not in {"comida", "cena"}:
            messages.error(
                request,
                "El servicio seleccionado no es válido."
            )
            return redirect(
                "editar_reserva_cliente",
                reserva_id=reserva.id,
            )

        servicio_hora = periodo_de_hora(hora)

        if servicio_hora != servicio_form:
            messages.error(
                request,
                "La hora seleccionada no pertenece al servicio elegido."
            )
            return redirect(
                "editar_reserva_cliente",
                reserva_id=reserva.id,
            )

        # --------------------------------------------------
        # 2. COMPROBAR HORARIOS
        # --------------------------------------------------

        horas_disponibles = [
            hora_opcion
            for hora_opcion in horas_llegada_para_turno(
                fecha, servicio_form, excluir_reserva_id=reserva.id,
            )
            if zonas_disponibles(
                fecha, hora_opcion, personas,
                excluir_reserva_id=reserva.id,
            )
        ]

        # Las cenas nuevas solo admiten los dos horarios
        # establecidos. Las reservas antiguas pueden
        # conservar su hora si no se modifican.
        cambia_fecha_hora_o_personas = (
            fecha != reserva.fecha
            or hora != reserva.hora
            or personas != reserva.personas
            or servicio_form != servicio_actual
        )

        # Proteger las reservas antiguas de cena.
        if (
            servicio_actual == "cena"
            and reserva.cena_sistema_antiguo
            and cambia_fecha_hora_o_personas
        ):
            messages.error(
                request,
                "Esta reserva pertenece al sistema anterior. "
                "Para modificarla, contacta con el restaurante."
            )
            return redirect(
                "editar_reserva_cliente",
                reserva_id=reserva.id,
            )

        # --------------------------------------------------
        # 3. VOLVER AL FORMULARIO
        # --------------------------------------------------

        if accion == "volver_edicion":
            return render(
                request,
                "reservas/editar_reserva_cliente.html",
                {
                    "reserva": reserva,
                    "max_personas": MAX_PERSONAS_RESERVA,
                    "hoy": timezone.localdate(),
                    "personas_form": personas,
                    "fecha_form": fecha,
                    "servicio_form": servicio_form,
                    "hora_form": hora,
                    "horas_disponibles": horas_disponibles,
                },
            )

        # --------------------------------------------------
        # 4. CONSULTAR ZONAS DISPONIBLES
        # --------------------------------------------------

        zonas = zonas_disponibles(
            fecha,
            hora,
            personas,
            excluir_reserva_id=reserva.id,
        )

        # Para las nuevas cenas solo existe barra: confirmar directamente
        # sin mostrar la pantalla intermedia de selección de zona.
        cena_nueva = (
            cena_con_sistema_nuevo(fecha, reserva.id)
            and hora in TURNOS_CENA_NUEVOS
            and not (
                servicio_actual == "cena"
                and reserva.cena_sistema_antiguo
            )
        )

        if accion == "buscar_zonas" and cena_nueva:
            accion = "confirmar"
        elif accion == "buscar_zonas":
            return render(
                request,
                "reservas/editar_reserva_zona_cliente.html",
                {
                    "reserva": reserva,
                    "personas_form": personas,
                    "fecha_form": fecha,
                    "servicio_form": servicio_form,
                    "hora_form": hora,
                    "zonas": zonas,
                },
            )

        # --------------------------------------------------
        # 5. VALIDAR LA ZONA ELEGIDA
        # --------------------------------------------------

        zona = "barra" if cena_nueva else request.POST.get("zona")

        if not zona:
            messages.error(
                request,
                "Elige una zona disponible."
            )
            return redirect(
                "editar_reserva_cliente",
                reserva_id=reserva.id,
            )

        zonas_validas = {
            opcion["id"]
            for opcion in zonas
        }

        # Permitir conservar exactamente una reserva antigua
        # sin obligarla a cambiar de horario o de zona.
        conserva_reserva_original = (
            fecha == reserva.fecha
            and hora == reserva.hora
            and personas == reserva.personas
            and servicio_form == servicio_actual
            and zona == reserva.zona
        )
        if (
            servicio_actual == "cena"
            and reserva.cena_sistema_antiguo
            and not conserva_reserva_original
        ):
            messages.error(
                request,
                "Para modificar esta reserva antigua, "
                "contacta con el restaurante."
            )
            return redirect(
                "editar_reserva_cliente",
                reserva_id=reserva.id,
            )
        if (
            zona not in zonas_validas
            and not conserva_reserva_original
        ):
            messages.error(
                request,
                "La zona seleccionada no está disponible "
                "para esta reserva."
            )
            return redirect(
                "editar_reserva_cliente",
                reserva_id=reserva.id,
            )

        # Si no cambia nada, no reescribimos la reserva.
        # Así conservamos también los datos originales
        # de las reservas antiguas y combinadas.
        if conserva_reserva_original:
            messages.success(
                request,
                "La reserva se ha mantenido sin cambios."
            )
            return redirect("gestionar_reserva")

        # --------------------------------------------------
        # 6. VALIDACIÓN FINAL Y GUARDADO
        # --------------------------------------------------

        datos_zona = _normalizar_zona_para_guardar(
            zona,
            personas,
        )

        with transaction.atomic():
            bloquear_fecha_para_reservar(fecha)

            if not hay_disponibilidad(
                fecha,
                hora,
                personas,
                zona,
                excluir_reserva_id=reserva.id,
            ):
                messages.error(
                    request,
                    "Ese nuevo hueco no está disponible."
                )
                return redirect(
                    "editar_reserva_cliente",
                    reserva_id=reserva.id,
                )

            reserva.personas = personas
            reserva.fecha = fecha
            reserva.hora = hora
            reserva.servicio = servicio_form
            reserva.zona = datos_zona["zona"]
            reserva.personas_barra = datos_zona["personas_barra"]
            reserva.personas_mesa = datos_zona["personas_mesa"]
            reserva.recordatorio_enviado = False
            reserva.cena_sistema_antiguo = not cena_con_sistema_nuevo(fecha, reserva.id)

            reserva.save(
                update_fields=[
                    "personas",
                    "fecha",
                    "hora",
                    "servicio",
                    "zona",
                    "personas_barra",
                    "personas_mesa",
                    "recordatorio_enviado",
                    "cena_sistema_antiguo",
                    "actualizado",
                ]
            )

        _enviar_email_seguro(
            enviar_email_modificacion,
            reserva,
        )

        messages.success(
            request,
            "Reserva modificada correctamente."
        )

        return redirect("gestionar_reserva")

    # --------------------------------------------------
    # 7. MOSTRAR FORMULARIO INICIAL
    # --------------------------------------------------

    horas_disponibles = horas_llegada_para_turno(
        reserva.fecha,
        servicio_actual,
        excluir_reserva_id=reserva.id,
    )

    # Mostrar la hora original aunque ya no se ofrezca
    # para nuevas reservas.
    if reserva.hora not in horas_disponibles:
        horas_disponibles.insert(0, reserva.hora)

    return render(
        request,
        "reservas/editar_reserva_cliente.html",
        {
            "reserva": reserva,
            "max_personas": MAX_PERSONAS_RESERVA,
            "hoy": timezone.localdate(),
            "personas_form": reserva.personas,
            "fecha_form": reserva.fecha,
            "servicio_form": servicio_actual,
            "hora_form": reserva.hora,
            "horas_disponibles": horas_disponibles,
            "zonas": [],
            "mostrar_zonas": False,
        },
    )


@login_required
def staff_reservas_count(request):
    fecha = timezone.localdate()

    total = (
        Reserva.objects
        .filter(
            fecha=fecha,
            estado__in=[
                "pendiente_pago",
                "confirmada",
                "llegado",
            ],
        )
        .count()
    )

    return JsonResponse({"total": total})

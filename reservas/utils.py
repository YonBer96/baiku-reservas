import re
import unicodedata


def normalizar_telefono(valor):
    """
    Devuelve únicamente los dígitos del teléfono.

    Ejemplos:
    '612 345 678'   -> '612345678'
    '612-345-678'   -> '612345678'
    '+34 612345678' -> '34612345678'
    """
    if not valor:
        return ""

    return re.sub(r"\D", "", str(valor))


def normalizar_texto(valor):
    """
    Normaliza texto para búsquedas:
    - minúsculas
    - sin tildes
    - sin espacios al principio/final

    García -> garcia
    GARCÍA -> garcia
    """
    if not valor:
        return ""

    valor = str(valor).strip().lower()
    valor = unicodedata.normalize("NFD", valor)

    return "".join(
        caracter
        for caracter in valor
        if unicodedata.category(caracter) != "Mn"
    )
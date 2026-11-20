"""
Normalisation des dates et libellés pour le contrôle
des jours fériés. Ne contient aucune donnée métier.
"""

import difflib
import unicodedata
from datetime import date, datetime


def normalize_label(value):
    """
    Normalise un libellé pour la comparaison :
    trim, minuscules, suppression des accents.
    """

    if value is None:
        return ""

    text = str(value).strip().lower()
    text = "".join(
        char for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) != "Mn"
    )
    return " ".join(text.split())


def parse_date(value):
    """Convertit une date ISO ou datetime en datetime.date."""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()


def labels_match(label_a, label_b, threshold=0.85):
    """
    Compare deux libellés normalisés.
    Retourne True si leur similarité atteint le seuil.
    """

    a = normalize_label(label_a)
    b = normalize_label(label_b)

    if not a or not b:
        return False

    if a == b:
        return True

    return difflib.SequenceMatcher(None, a, b).ratio() >= threshold


def slugify_label(value):
    """
    Produit un slug stable pour un libellé,
    utilisé dans l'identifiant des propositions.
    """

    normalized = normalize_label(value)
    slug = "".join(
        char if char.isalnum() else "-" for char in normalized
    )
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:50]

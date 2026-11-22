from datetime import date

import pytest

from apps.recruitment.models import Candidat
from apps.recruitment.normalization import (
    identity_key,
    normalize_email,
    normalize_name,
    normalize_phone,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("06 00 12 34 56", "212600123456"),
        ("+212 6 00 12 34 56", "212600123456"),
        ("00212600123456", "212600123456"),
        ("0600-12-34-56", "212600123456"),
        ("", ""),
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_normalize_email():
    assert normalize_email("  Sara.Alaoui@Example.COM ") == "sara.alaoui@example.com"
    assert normalize_email("s.ara+concours@gmail.com") == "sara@gmail.com"


def test_normalize_name_ordre_casse_accents():
    assert normalize_name("EL AMRANI", "Salmà") == normalize_name("el amrani", "Salma")
    assert normalize_name("Alaoui", "Sara") == normalize_name("Sara", "ALAOUI")


def test_identity_key_exige_la_date_de_naissance():
    assert identity_key("Alaoui", "Sara", None) == ""
    assert identity_key("Alaoui", "Sara", date(1990, 1, 2)).endswith("|1990-01-02")


@pytest.mark.django_db
def test_cles_calculees_a_l_enregistrement():
    a = Candidat.objects.create(
        nom="Tazi",
        prenom="Omar",
        email="Omar.Tazi@Example.com",
        telephone="06 00 11 22 33",
        date_naissance=date(1988, 5, 1),
    )
    b = Candidat.objects.create(
        nom="TAZI",
        prenom="omar",
        email="omar.tazi@example.com",
        telephone="+212600112233",
        date_naissance=date(1988, 5, 1),
    )
    assert a.email_normalise == b.email_normalise
    assert a.telephone_normalise == b.telephone_normalise
    assert a.cle_identite == b.cle_identite

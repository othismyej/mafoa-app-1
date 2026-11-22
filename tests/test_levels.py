import pytest

from apps.evaluation.eligibility.levels import NiveauEtudes, parse_niveau


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Bac+5", NiveauEtudes.BAC_5),
        ("bac + 2", NiveauEtudes.BAC_2),
        ("BAC+3 en gestion", NiveauEtudes.BAC_3),
        ("Bac+8", NiveauEtudes.DOCTORAT),
        ("Bac+6", NiveauEtudes.BAC_5),  # arrondi au niveau reconnu inférieur
        ("Master spécialisé en finance", NiveauEtudes.BAC_5),
        ("Diplôme d'Ingénieur d'État", NiveauEtudes.BAC_5),
        ("Mastère", NiveauEtudes.BAC_5),
        ("Licence professionnelle", NiveauEtudes.BAC_3),
        ("Licence en droit", NiveauEtudes.BAC_3),
        ("Maîtrise", NiveauEtudes.BAC_4),
        ("DUT Génie électrique", NiveauEtudes.BAC_2),
        ("BTS", NiveauEtudes.BAC_2),
        ("Technicien spécialisé", NiveauEtudes.BAC_2),
        ("Doctorat en hydraulique", NiveauEtudes.DOCTORAT),
        ("Baccalauréat sciences", NiveauEtudes.BAC),
    ],
)
def test_parse_niveau(label, expected):
    assert parse_niveau(label) == expected


@pytest.mark.parametrize("label", ["", None, "Certificat de natation", "Formation continue"])
def test_parse_niveau_inconnu_renvoie_none(label):
    assert parse_niveau(label) is None


def test_niveaux_sont_ordonnes():
    assert NiveauEtudes.BAC < NiveauEtudes.BAC_2 < NiveauEtudes.BAC_3 < NiveauEtudes.BAC_5
    assert NiveauEtudes.DOCTORAT > NiveauEtudes.BAC_5

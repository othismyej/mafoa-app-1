from datetime import date
from decimal import Decimal

from apps.evaluation.eligibility.experience import Periode, compute_experience

REF = date(2026, 10, 15)


def test_periode_simple_annee_complete():
    r = compute_experience([Periode(date(2020, 1, 1), date(2020, 12, 31))], REF)
    assert r.jours == 366  # 2020 bissextile, bornes incluses
    assert r.annees == Decimal("1.00")


def test_chevauchements_non_comptes_deux_fois():
    r = compute_experience(
        [
            Periode(date(2018, 1, 1), date(2019, 12, 31)),
            Periode(date(2019, 1, 1), date(2020, 12, 31)),  # chevauche toute l'année 2019
        ],
        REF,
    )
    assert r.periodes_retenues == [(date(2018, 1, 1), date(2020, 12, 31))]
    assert r.annees == Decimal("3.00")


def test_periode_incluse_dans_une_autre():
    r = compute_experience(
        [
            Periode(date(2015, 1, 1), date(2020, 12, 31)),
            Periode(date(2016, 3, 1), date(2017, 2, 1)),
        ],
        REF,
    )
    assert len(r.periodes_retenues) == 1
    assert r.annees == Decimal("6.00")


def test_periodes_contigues_fusionnees():
    r = compute_experience(
        [
            Periode(date(2019, 1, 1), date(2019, 6, 30)),
            Periode(date(2019, 7, 1), date(2019, 12, 31)),
        ],
        REF,
    )
    assert r.periodes_retenues == [(date(2019, 1, 1), date(2019, 12, 31))]


def test_poste_en_cours_arrete_a_la_date_de_reference():
    r = compute_experience([Periode(date(2021, 10, 16), None)], REF)
    assert r.periodes_retenues == [(date(2021, 10, 16), REF)]
    assert r.annees == Decimal("5.00")


def test_periode_tronquee_et_periode_future_ignoree():
    r = compute_experience(
        [Periode(date(2025, 10, 16), date(2027, 1, 1)), Periode(date(2026, 11, 1), None)], REF
    )
    assert r.periodes_retenues == [(date(2025, 10, 16), REF)]
    assert any("postérieure" in a for a in r.avertissements)


def test_date_debut_manquante_rend_incertain():
    r = compute_experience([Periode(None, date(2020, 1, 1)), Periode(date(2022, 1, 1), None)], REF)
    assert r.incertain is True
    assert len(r.periodes_retenues) == 1


def test_fin_avant_debut_incoherent():
    r = compute_experience([Periode(date(2020, 1, 1), date(2019, 1, 1), intitule="Ingénieur")], REF)
    assert r.incoherent is True
    assert r.jours == 0
    assert "Ingénieur" in r.avertissements[0]


def test_aucune_periode():
    r = compute_experience([], REF)
    assert r.annees == Decimal("0.00")
    assert not r.incertain and not r.incoherent

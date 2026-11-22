from datetime import date
from decimal import Decimal

import pytest

from apps.evaluation.eligibility.engine import (
    CriteresPoste,
    Diplome,
    ProfilCandidat,
    Verdict,
    evaluate,
)
from apps.evaluation.eligibility.experience import Periode
from apps.evaluation.eligibility.levels import NiveauEtudes
from apps.evaluation.eligibility.specialites import match_specialite

REF = date(2026, 10, 15)
CRITERES = CriteresPoste(
    date_reference=REF,
    niveau_min=NiveauEtudes.BAC_5,
    specialites=["Génie civil", "Hydraulique"],
    experience_min=Decimal("5"),
)


def profil(niveau="Bac+5", specialite="Génie civil", debut=date(2018, 1, 1), fin=None, **kw):
    return ProfilCandidat(
        diplomes=[Diplome(intitule=f"Master {specialite}", niveau=niveau, specialite=specialite)],
        experiences=kw.get("experiences", [Periode(debut, fin)]),
    )


def by_critere(result):
    return {c.critere: c for c in result.criteres}


def test_candidat_eligible_sur_tous_les_criteres():
    r = evaluate(profil(), CRITERES)
    assert r.verdict == Verdict.ELIGIBLE
    assert all(c.verdict == Verdict.ELIGIBLE for c in r.criteres)
    assert "Bac+5" in by_critere(r)["niveau_etudes"].raison


def test_niveau_insuffisant_non_eligible():
    r = evaluate(profil(niveau="Bac+3"), CRITERES)
    assert r.verdict == Verdict.NON_ELIGIBLE
    assert by_critere(r)["niveau_etudes"].verdict == Verdict.NON_ELIGIBLE


def test_niveau_illisible_a_verifier_jamais_non_eligible():
    p = ProfilCandidat(
        diplomes=[Diplome(intitule="Diplôme étranger XYZ", niveau=None, specialite="Génie civil")],
        experiences=[Periode(date(2015, 1, 1), None)],
    )
    r = evaluate(p, CRITERES)
    assert r.verdict == Verdict.A_VERIFIER
    assert by_critere(r)["niveau_etudes"].verdict == Verdict.A_VERIFIER


def test_un_diplome_suffisant_parmi_plusieurs():
    p = ProfilCandidat(
        diplomes=[
            Diplome(intitule="DUT", niveau="Bac+2", specialite="Génie civil"),
            Diplome(intitule="Diplôme d'ingénieur d'État", specialite="Génie civil"),
        ],
        experiences=[Periode(date(2015, 1, 1), None)],
    )
    assert evaluate(p, CRITERES).verdict == Verdict.ELIGIBLE


def test_aucun_diplome_a_verifier():
    p = ProfilCandidat(diplomes=[], experiences=[Periode(date(2015, 1, 1), None)])
    assert by_critere(evaluate(p, CRITERES))["niveau_etudes"].verdict == Verdict.A_VERIFIER


@pytest.mark.parametrize(
    "specialite", ["Génie civil", "GENIE CIVIL", "génie-civil", "BTP", "Génie civil et BTP"]
)
def test_specialite_reconnue(specialite):
    r = evaluate(profil(specialite=specialite), CRITERES)
    assert by_critere(r)["specialite"].verdict == Verdict.ELIGIBLE


def test_specialite_hors_liste_a_verifier_jamais_non_eligible():
    r = evaluate(profil(specialite="Biologie"), CRITERES)
    assert by_critere(r)["specialite"].verdict == Verdict.A_VERIFIER
    assert r.verdict == Verdict.A_VERIFIER


def test_specialite_ignoree_sur_diplome_de_niveau_insuffisant():
    p = ProfilCandidat(
        diplomes=[
            Diplome(intitule="DUT", niveau="Bac+2", specialite="Génie civil"),
            Diplome(intitule="Master", niveau="Bac+5", specialite="Biologie"),
        ],
        experiences=[Periode(date(2015, 1, 1), None)],
    )
    assert by_critere(evaluate(p, CRITERES))["specialite"].verdict == Verdict.A_VERIFIER


def test_toute_specialite_acceptee_si_liste_vide():
    criteres = CriteresPoste(date_reference=REF, niveau_min=NiveauEtudes.BAC_2)
    r = evaluate(profil(niveau="Bac+2", specialite="Lettres"), criteres)
    assert r.verdict == Verdict.ELIGIBLE


def test_experience_exactement_au_minimum():
    # 5 ans pile : du 16/10/2021 au 15/10/2026 inclus = 1826 jours = 5,00 ans.
    r = evaluate(profil(debut=date(2021, 10, 16)), CRITERES)
    assert by_critere(r)["experience"].verdict == Verdict.ELIGIBLE


def test_experience_juste_sous_le_minimum_non_eligible():
    r = evaluate(profil(debut=date(2021, 12, 1)), CRITERES)
    exp = by_critere(r)["experience"]
    assert exp.verdict == Verdict.NON_ELIGIBLE
    assert "inférieure au minimum" in exp.raison


def test_experience_au_dela_du_maximum_non_eligible():
    criteres = CriteresPoste(
        date_reference=REF, experience_min=Decimal("3"), experience_max=Decimal("10")
    )
    r = evaluate(profil(debut=date(2010, 1, 1)), criteres)
    assert by_critere(r)["experience"].verdict == Verdict.NON_ELIGIBLE


def test_experience_insuffisante_avec_date_manquante_a_verifier():
    p = profil(experiences=[Periode(None, date(2019, 1, 1)), Periode(date(2024, 1, 1), None)])
    r = evaluate(p, CRITERES)
    assert by_critere(r)["experience"].verdict == Verdict.A_VERIFIER
    assert r.verdict == Verdict.A_VERIFIER


def test_experience_insuffisante_avec_dates_incoherentes_a_verifier():
    p = profil(
        experiences=[Periode(date(2019, 1, 1), date(2015, 1, 1)), Periode(date(2024, 1, 1), None)]
    )
    assert by_critere(evaluate(p, CRITERES))["experience"].verdict == Verdict.A_VERIFIER


def test_experience_suffisante_malgre_periode_incoherente_reste_eligible_et_signalee():
    p = profil(
        experiences=[Periode(date(2015, 1, 1), None), Periode(date(2010, 1, 1), date(2009, 1, 1))]
    )
    exp = by_critere(evaluate(p, CRITERES))["experience"]
    assert exp.verdict == Verdict.ELIGIBLE
    assert "antérieure au début" in exp.raison


def test_experience_non_renseignee_a_verifier():
    p = ProfilCandidat(diplomes=profil().diplomes, experiences=None)
    assert by_critere(evaluate(p, CRITERES))["experience"].verdict == Verdict.A_VERIFIER


def test_aucune_experience_declaree_non_eligible_si_minimum():
    p = ProfilCandidat(diplomes=profil().diplomes, experiences=[])
    assert by_critere(evaluate(p, CRITERES))["experience"].verdict == Verdict.NON_ELIGIBLE


def test_autres_criteres_toujours_a_verifier():
    criteres = CriteresPoste(date_reference=REF, autres_criteres="Permis de conduire B")
    r = evaluate(profil(), criteres)
    assert r.verdict == Verdict.A_VERIFIER
    assert by_critere(r)["autres"].attendu == "Permis de conduire B"


def test_non_eligible_prime_sur_a_verifier():
    r = evaluate(profil(niveau="Bac+2", specialite="Biologie"), CRITERES)
    assert r.verdict == Verdict.NON_ELIGIBLE


def test_details_serialisables_et_justification():
    r = evaluate(profil(), CRITERES)
    details = r.details()
    assert {d["critere"] for d in details} == {"niveau_etudes", "specialite", "experience"}
    assert all(isinstance(d["verdict"], str) for d in details)
    assert "[Niveau d'études]" in r.justification


def test_match_specialite_explications():
    assert match_specialite("Electrotechnique", ["Génie électrique"])[1] == "équivalence reconnue"
    assert match_specialite("", ["Droit"]) == (None, "spécialité non renseignée")

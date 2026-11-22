import pytest

from ai.client import AIClient, AIError
from ai.models import AppelIA
from ai.prompts import load_prompt
from ai.pseudonymize import Pseudonymizer
from ai.schemas import ExtractionCV

pytestmark = pytest.mark.django_db
TEXTE = (
    "CV de Sara Alaoui, CIN ZZ123456, tél. 06 00 12 34 56, sara.alaoui@example.com. "
    "Ingénieure chez Société Fictive des Eaux depuis 2019."
)


def test_pseudonymisation_et_restauration():
    p = Pseudonymizer()
    out = p.pseudonymize(TEXTE, known_names=["Sara", "Alaoui"])
    for secret in ("Sara", "Alaoui", "ZZ123456", "06 00 12 34 56", "sara.alaoui@example.com"):
        assert secret not in out
    assert "[CIN_1]" in out and "[EMAIL_1]" in out and "[TEL_1]" in out
    assert p.restore(out) == TEXTE


def test_prompt_versionne():
    prompt = load_prompt("extraction_cv", "1")
    assert prompt.version == "1" and "N'invente rien" in prompt.text


class FakeTransport:
    """Simule l'API Claude : aucun appel réseau dans les tests."""

    def __init__(self):
        self.calls = []

    def __call__(self, model, system, user_text, schema):
        self.calls.append(user_text)
        return schema(nom_complet="[NOM_1] [NOM_2]", confiance=0.9), {
            "input_tokens": 100,
            "output_tokens": 20,
        }


def test_client_pseudonymise_trace_et_restaure():
    transport = FakeTransport()
    client = AIClient(transport=transport, model="modele-test", pseudonymize=True)
    result, appel = client.structured(
        task="extraction_cv",
        prompt=load_prompt("extraction_cv", "1"),
        user_text=TEXTE,
        schema=ExtractionCV,
        known_names=["Sara", "Alaoui"],
    )
    assert "Alaoui" not in transport.calls[0]
    assert set(result.nom_complet.split()) == {"Sara", "Alaoui"}  # ré-identifié côté serveur
    appel.refresh_from_db()
    assert appel.statut == AppelIA.Statut.SUCCES
    assert appel.modele == "modele-test" and appel.prompt_version == "1"
    assert "Alaoui" not in str(appel.entree)  # entrée journalisée pseudonymisée
    assert appel.tokens_entree == 100


def test_client_journalise_les_erreurs():
    def failing(*args):
        raise RuntimeError("panne simulée")

    client = AIClient(transport=failing, model="modele-test")
    with pytest.raises(AIError):
        client.structured(
            task="t", prompt=load_prompt("extraction_cv", "1"), user_text="x", schema=ExtractionCV
        )
    appel = AppelIA.objects.get()
    assert appel.statut == AppelIA.Statut.ERREUR and "panne simulée" in appel.erreur


def test_sortie_invalide_rejetee():
    from pydantic import BaseModel

    class Mauvais(BaseModel):
        confiance: float = 5.0  # hors bornes [0, 1] pour ExtractionCV

    client = AIClient(transport=lambda *a: (Mauvais(), {}), model="m")
    with pytest.raises(AIError):
        client.structured(
            task="t", prompt=load_prompt("extraction_cv", "1"), user_text="x", schema=ExtractionCV
        )

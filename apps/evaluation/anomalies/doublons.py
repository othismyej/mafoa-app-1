"""Doublons de candidats et de pièces (spec KAN-42, section C).

Rapprochement par regroupement en base : une requête par clé (email, téléphone, identité),
une pour les candidatures des comptes rapprochés, une pour les pièces de même empreinte. Aucune
comparaison paire à paire sur l'ensemble des candidats ; le nombre de requêtes ne dépend pas du
nombre de candidatures traitées (règle 33).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from django.conf import settings

from apps.recruitment.models import Candidat, Candidature, Piece

from .regles import CRITERE_LIBELLES, HAUTE, INFO, MOYENNE, Constat, libelle_piece

CRITERES = (
    ("email", "email_normalise"),
    ("telephone", "telephone_normalise"),
    ("identite", "cle_identite"),
    # Q3 bis révisée : la CIN figure sur le profil du site (valeur jamais recopiée).
    ("cin", "cin_normalise"),
)
#: Pièces dont la copie chez un autre compte est grave (règle 6).
PIECES_SENSIBLES = ("diplome", "attestation_travail")
TELEPHONE_MIN_CHIFFRES = 9


def _valeur(critere: str, candidat: Candidat) -> str:
    valeur = getattr(candidat, dict(CRITERES)[critere]) or ""
    if critere == "telephone" and len(valeur) < TELEPHONE_MIN_CHIFFRES:
        return ""
    return valeur


@dataclass
class ResultatDoublons:
    constats: dict[int, list[Constat]] = field(default_factory=lambda: defaultdict(list))
    #: Candidatures d'autres comptes visées par un constat (symétrie, règle 8).
    liees: set[int] = field(default_factory=set)


def _criteres_texte(criteres) -> str:
    return ", ".join(CRITERE_LIBELLES[c] for c in criteres)


def detecter_doublons(candidatures: list[Candidature], pieces: list[Piece]) -> ResultatDoublons:
    """Constats de doublons pour ``candidatures`` (``candidat`` chargé) et leurs ``pieces``."""
    seuil = settings.DOUBLONS_SEUIL_GROUPE
    resultat = ResultatDoublons()
    if not candidatures:
        return resultat
    candidats = {c.candidat_id: c.candidat for c in candidatures}

    # 1. Comptes partageant chaque valeur de clé : une requête par clé.
    comptes: dict[tuple[str, str], set[int]] = {}
    for critere, champ in CRITERES:
        valeurs = {v for cand in candidats.values() if (v := _valeur(critere, cand))}
        if not valeurs:
            continue
        lignes = Candidat.objects.filter(**{f"{champ}__in": valeurs}).values_list("id", champ)
        for cid, valeur in lignes:
            comptes.setdefault((critere, valeur), set()).add(cid)

    liens: dict[int, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    partages: dict[int, list[dict]] = defaultdict(list)
    for a_id, cand in candidats.items():
        for critere, _champ in CRITERES:
            valeur = _valeur(critere, cand)
            if not valeur:
                continue
            groupe = comptes.get((critere, valeur), {a_id})
            if len(groupe) > seuil:
                partages[a_id].append({"critere": critere, "nb_comptes": len(groupe)})
                continue
            for b_id in groupe - {a_id}:
                liens[a_id][b_id].add(critere)

    # 2. Candidatures des comptes rapprochés : une requête.
    rapproches = {b for d in liens.values() for b in d}
    autres: dict[int, list[Candidature]] = defaultdict(list)
    if rapproches:
        for y in (
            Candidature.objects.filter(candidat_id__in=rapproches)
            .only("id", "candidat_id", "type", "concours_id", "offre_id", "poste_concours_id")
            .order_by("id")
        ):
            autres[y.candidat_id].append(y)

    for x in candidatures:
        for b_id, criteres in sorted(liens.get(x.candidat_id, {}).items()):
            ordre = [c for c, _ in CRITERES if c in criteres]
            for y in autres.get(b_id, []):
                resultat.constats[x.pk].append(_constat_compte(x, y, ordre))
                resultat.liees.add(y.pk)
        if partages.get(x.candidat_id):
            groupes = partages[x.candidat_id]
            resultat.constats[x.pk].append(
                Constat(
                    cle="doublon_candidat:valeur_partagee",
                    type="doublon_candidat",
                    code="valeur_partagee",
                    gravite=INFO,
                    description=(
                        "Une valeur de rapprochement de ce compte est partagée par de nombreux "
                        "comptes ("
                        + ", ".join(
                            f"{CRITERE_LIBELLES[g['critere']]} : {g['nb_comptes']} comptes"
                            for g in groupes
                        )
                        + f", seuil : {seuil}) ; elle n'est pas utilisée pour rechercher des "
                        "doublons."
                    ),
                    details={"source": "compte_rapproche", "valeurs_partagees": groupes},
                )
            )

    _doublons_pieces(candidatures, pieces, resultat, seuil)
    return resultat


def _constat_compte(x: Candidature, y: Candidature, criteres: list[str]) -> Constat:
    texte = _criteres_texte(criteres)
    details = {
        "source": "compte_rapproche",
        "criteres": criteres,
        "candidature_liee_id": y.pk,
    }
    if x.concours_id and x.concours_id == y.concours_id:
        meme_poste = x.poste_concours_id == y.poste_concours_id
        return Constat(
            cle=f"plusieurs_postes_concours:cand={y.pk}",
            type="plusieurs_postes_concours",
            code="autre_compte",
            gravite=HAUTE,
            description=(
                f"Un autre compte candidat (rapproché par : {texte}) a aussi postulé à ce "
                f"concours (candidature #{y.pk}, {'même poste' if meme_poste else 'autre poste'})."
                " Règle du site : un seul poste par concours."
            ),
            details={**details, "cible_commune": "concours", "meme_poste": meme_poste},
            candidature_liee_id=y.pk,
        )
    if x.offre_id and x.offre_id == y.offre_id:
        return Constat(
            cle=f"doublon_candidat:cand={y.pk}",
            type="doublon_candidat",
            code="meme_offre",
            gravite=HAUTE,
            description=(
                f"Un autre compte candidat (rapproché par : {texte}) a aussi postulé à cette "
                f"offre (candidature #{y.pk})."
            ),
            details={**details, "cible_commune": "offre"},
            candidature_liee_id=y.pk,
        )
    if "cin" in criteres:
        return Constat(
            cle=f"doublon_candidat:cand={y.pk}",
            type="doublon_candidat",
            code="meme_cin",
            gravite=HAUTE,
            description=(
                f"Un autre compte candidat (rapproché par : {texte}) a une candidature (#{y.pk})."
                " Même CIN : même personne probable."
            ),
            details={**details, "cible_commune": None},
            candidature_liee_id=y.pk,
        )
    fort = "email" in criteres or "identite" in criteres
    return Constat(
        cle=f"doublon_candidat:cand={y.pk}",
        type="doublon_candidat",
        code="autre_compte" if fort else "telephone_seul",
        gravite=MOYENNE if fort else INFO,
        description=(
            f"Un autre compte candidat (rapproché par : {texte}) a une candidature (#{y.pk})."
        ),
        details={**details, "cible_commune": None},
        candidature_liee_id=y.pk,
    )


def _doublons_pieces(candidatures, pieces, resultat: ResultatDoublons, seuil: int) -> None:
    """Pièces de même empreinte (règle 10) : une requête."""
    candidat_de = {c.pk: c.candidat_id for c in candidatures}
    a_controler = [
        p for p in pieces if p.sha256 and p.taille != 0 and p.candidature_id in candidat_de
    ]
    empreintes = {p.sha256 for p in a_controler}
    if not empreintes:
        return
    groupes: dict[str, list[dict]] = defaultdict(list)
    for ligne in (
        Piece.objects.filter(sha256__in=empreintes)
        .exclude(taille=0)
        .values("id", "sha256", "type", "candidature_id", "candidature__candidat_id")
        .order_by("id")
    ):
        groupes[ligne["sha256"]].append(ligne)

    for p in a_controler:
        groupe = groupes.get(p.sha256, [])
        nb_comptes = len({q["candidature__candidat_id"] for q in groupe})
        if nb_comptes > seuil:
            resultat.constats[p.candidature_id].append(
                Constat(
                    cle=f"doublon_piece:valeur_partagee:piece={p.pk}",
                    type="doublon_piece",
                    code="valeur_partagee",
                    gravite=INFO,
                    description=(
                        f"Fichier identique (même empreinte) à des pièces de {nb_comptes} comptes "
                        f"candidats (seuil : {seuil}) : modèle commun probable."
                    ),
                    details={"source": "piece", "nb_comptes": nb_comptes},
                    piece_id=p.pk,
                )
            )
            continue
        candidat_id = candidat_de[p.candidature_id]
        meme_dossier = [
            q for q in groupe if q["candidature_id"] == p.candidature_id and q["id"] < p.pk
        ]
        if meme_dossier:
            q = meme_dossier[0]
            resultat.constats[p.candidature_id].append(
                Constat(
                    cle=f"doublon_piece:meme_dossier:piece={p.pk}",
                    type="doublon_piece",
                    code="meme_dossier",
                    gravite=INFO,
                    description=(
                        "Fichier identique (même empreinte) à une autre pièce de ce dossier "
                        f"(pièce #{q['id']}, {libelle_piece(q['type'])})."
                    ),
                    details={"source": "piece", "piece_identique_id": q["id"]},
                    piece_id=p.pk,
                )
            )
        vues: set[int] = set()
        for q in groupe:
            y_id = q["candidature_id"]
            if q["candidature__candidat_id"] == candidat_id or y_id in vues:
                continue  # même compte (CV réutilisé) : aucune anomalie
            vues.add(y_id)
            resultat.liees.add(y_id)
            resultat.constats[p.candidature_id].append(
                Constat(
                    cle=f"doublon_piece:autre_candidat:piece={p.pk}:cand={y_id}",
                    type="doublon_piece",
                    code="autre_candidat",
                    gravite=HAUTE if p.type in PIECES_SENSIBLES else MOYENNE,
                    description=(
                        "Fichier identique (même empreinte) à une pièce d'un autre compte "
                        f"candidat (candidature #{y_id}, {libelle_piece(q['type'])})."
                    ),
                    details={
                        "source": "piece",
                        "candidature_liee_id": y_id,
                        "piece_liee_id": q["id"],
                        "type_piece_liee": q["type"],
                    },
                    piece_id=p.pk,
                    candidature_liee_id=y_id,
                )
            )

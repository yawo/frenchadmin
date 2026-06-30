from __future__ import annotations

import re

from config import ENABLE_QUERY_EXPANSION

# Legal acronyms → full form (case-insensitive matching)
ACRONYM_MAP: dict[str, str] = {
    "eurl": "entreprise unipersonnelle à responsabilité limitée",
    "sarl": "société à responsabilité limitée",
    "sas": "société par actions simplifiée",
    "sasu": "société par actions simplifiée unipersonnelle",
    "sa": "société anonyme",
    "sci": "société civile immobilière",
    "scm": "société civile de moyens",
    "scp": "société civile professionnelle",
    "snc": "société en nom collectif",
    "sel": "société d'exercice libéral",
    "selarl": "société d'exercice libéral à responsabilité limitée",
    "cgi": "code général des impôts",
    "lf": "loi de finances",
    "lfr": "loi de finances rectificative",
    "boi": "bulletin officiel des impôts",
    "bofip": "bulletin officiel des finances publiques",
    "tva": "taxe sur la valeur ajoutée",
    "ir": "impôt sur le revenu",
    "is": "impôt sur les sociétés",
    "bic": "bénéfices industriels et commerciaux",
    "bnc": "bénéfices non commerciaux",
    "ba": "bénéfices agricoles",
    "cfe": "cotisation foncière des entreprises",
    "cvae": "cotisation sur la valeur ajoutée des entreprises",
    "cet": "contribution économique territoriale",
    "csg": "contribution sociale généralisée",
    "crds": "contribution au remboursement de la dette sociale",
    "pfu": "prélèvement forfaitaire unique",
    "tmi": "tranche marginale d'imposition",
    "rcs": "registre du commerce et des sociétés",
    "kbis": "extrait du registre du commerce",
    "ag": "assemblée générale",
    "age": "assemblée générale extraordinaire",
    "ago": "assemblée générale ordinaire",
    "ca": "chiffre d'affaires",
    "ebe": "excédent brut d'exploitation",
    "rn": "résultat net",
    "pv": "plus-value",
    "mv": "moins-value",
    "dmtg": "droits de mutation à titre gratuit",
    "dmto": "droits de mutation à titre onéreux",
    "ifi": "impôt sur la fortune immobilière",
    "lmp": "loueur meublé professionnel",
    "lmnp": "loueur meublé non professionnel",
    "zfu": "zone franche urbaine",
    "zrr": "zone de revitalisation rurale",
    "jei": "jeune entreprise innovante",
    "cir": "crédit d'impôt recherche",
    "cice": "crédit d'impôt compétitivité emploi",
}

# Common legal synonyms (term → list of alternatives to append)
SYNONYM_MAP: dict[str, list[str]] = {
    "dirigeant": ["gérant", "mandataire social"],
    "gérant": ["dirigeant", "mandataire social"],
    "salaire": ["rémunération", "traitement"],
    "rémunération": ["salaire", "traitement", "émoluments"],
    "impôt": ["imposition", "taxe", "contribution"],
    "imposition": ["impôt", "taxation"],
    "entreprise": ["société", "exploitation"],
    "société": ["entreprise", "personne morale"],
    "bénéfice": ["résultat", "profit"],
    "déficit": ["perte", "résultat négatif"],
    "dividende": ["distribution", "revenu distribué"],
    "distribution": ["dividende", "mise en distribution"],
    "associé": ["actionnaire", "porteur de parts"],
    "actionnaire": ["associé", "porteur d'actions"],
    "cession": ["vente", "transmission", "aliénation"],
    "transmission": ["cession", "transfert", "mutation"],
    "exonération": ["dispense", "franchise", "exemption"],
    "déduction": ["abattement", "réduction"],
    "abattement": ["déduction", "réduction"],
    "amortissement": ["dépréciation"],
    "provision": ["dépréciation", "charge à payer"],
    "créance": ["dette", "obligation"],
    "patrimoine": ["actif", "fortune"],
    "foncier": ["immobilier", "bien immeuble"],
    "mobilier": ["meuble", "valeur mobilière"],
    "plus-value": ["gain en capital", "profit de cession"],
    "apport": ["contribution", "mise en société"],
    "fusion": ["absorption", "restructuration"],
    "scission": ["division", "séparation"],
}

_WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)


def expand_query(text: str) -> str:
    """Expand a query with legal acronym definitions and synonym alternatives.

    Returns the original query with expansions appended (preserving the original
    for exact-match scenarios while adding expanded terms for broader matching).
    """
    if not ENABLE_QUERY_EXPANSION:
        return text

    words = _WORD_RE.findall(text.lower())
    expansions: list[str] = []
    seen: set[str] = set()

    for word in words:
        # Acronym expansion
        if word in ACRONYM_MAP:
            expanded = ACRONYM_MAP[word]
            if expanded not in seen:
                expansions.append(expanded)
                seen.add(expanded)

        # Synonym expansion
        if word in SYNONYM_MAP:
            for syn in SYNONYM_MAP[word]:
                if syn not in seen and syn not in text.lower():
                    expansions.append(syn)
                    seen.add(syn)

    if not expansions:
        return text

    return text + " " + " ".join(expansions)

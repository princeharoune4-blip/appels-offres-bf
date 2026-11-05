"""Collecte les avis d'appels d'offres des régions dans le Quotidien des marchés publics (DGCMEF)."""
import io, json, re, sys, datetime, requests, pdfplumber

BASES = ["https://www.finances.gov.bf/fileadmin/user_upload/storage/fichiers/",
         "https://finances.gov.bf/fileadmin/user_upload/storage/fichiers/"]
NOMS = ["Quotidien_N__{n}.pdf", "Quotidien_N__{n}_.pdf", "Quotidien_N__{n}-pdf.pdf", "Quotidien_n__{n}.pdf"]
MODELES = [b + nom for nom in NOMS for b in BASES]  # adresses possibles d'un numéro du Quotidien
ENTETES = {"User-Agent": "Mozilla/5.0 (compatible; collecteur-appels-offres)"}
MAX_PAR_PASSAGE = 40   # nombre maximum de numéros traités à chaque passage
MAX_TROUS = 4          # on s'arrête après 4 numéros introuvables d'affilée
ETAT, SORTIE = "data/etat.json", "data/appels.json"

# mot-clé trouvé dans l'avis -> région (nouveaux noms depuis juillet 2025)
REGIONS = [
 ("sourou|tougan","Sourou"),("soum|djibo","Soum"),("tapoa|diapaga|kantchari","Tapoa"),
 ("sirba|bogand|gnagna|komondjari","Sirba"),("goulmou|fada|gourma","Goulmou"),
 ("liptako|oudalan|dori|séno|seno|yagha","Liptako"),("boucle du mouhoun|bankui|dédougou|dedougou","Bankui"),
 ("cascades|comoé|comoe|banfora","Comoé"),("sud-ouest|djôrô|djoro|gaoua","Djôrô"),
 ("hauts-bassins|guiriko|bobo","Guiriko"),("centre-ouest|nando|koudougou","Nando"),
 ("centre-sud|nazinon|manga","Nazinon"),("centre-est|nakambé|nakambe|tenkodogo","Nakambé"),
 ("centre-nord|kuilsé|kuilse|kaya","Kuilsé"),("plateau-central|oubri|ziniaré|ziniare","Oubri"),
 ("région du nord|yaadga|ouahigouya","Yaadga"),("kadiogo|ouagadougou|région du centre","Kadiogo"),
]
SECTEURS = [("nettoy|entretien|hygiène","Nettoyage et entretien"),("gardiennage|surveillance","Gardiennage"),
 ("informatique|ordinateur|logiciel","Informatique"),("médicament|santé|sanitaire|csps|hôpital","Santé"),
 ("forage|eau |agricole|irrigation|pastoral","Agriculture et eau"),("école|salle de classe|éducation|scolaire","Éducation"),
 ("véhicule|carburant|transport","Transport et logistique"),("bureau|papeterie","Fournitures de bureau"),
 ("construction|réhabilitation|travaux|bâtiment|piste|route|réalisation","Bâtiment et travaux publics")]
MOIS = {m:i+1 for i,m in enumerate("janvier février mars avril mai juin juillet août septembre octobre novembre décembre".split())}

def trouver(table, texte):
    t = texte.lower()
    for motifs, nom in table:
        if re.search(motifs, t): return nom
    return None

def type_marche(t):
    t = t.lower()
    if re.search(r"étude|contrôle|audit|consultant|prestations intellectuelles", t): return "Prestations intellectuelles"
    if re.search(r"travaux|construction|réhabilitation|réalisation|aménagement", t): return "Travaux"
    if re.search(r"acquisition|fourniture|achat", t): return "Fournitures"
    return "Services courants"

def date_limite(t):
    m = re.search(r"(\d{1,2})\s+(janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)\s+(20\d\d)", t, re.I)
    if m: return f"{m[3]}-{MOIS[m[2].lower()]:02d}-{int(m[1]):02d}"
    m = re.search(r"(\d{1,2})[/.](\d{1,2})[/.](20\d\d)", t)
    if m: return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}"
    return ""

def avis_du_pdf(contenu, n, url):
    with pdfplumber.open(io.BytesIO(contenu)) as pdf:
        texte = "\n".join((p.extract_text() or "") for p in pdf.pages)
    morceaux = re.split(r"(?=\n\s*(?:AVIS\s+D[’']APPEL|AVIS\s+DE\s+DEMANDE|AVIS\s+DE\s+SOLLICITATION|DEMANDE\s+DE\s+PRIX))", texte, flags=re.I)
    sortie = []
    for b in morceaux:
        b = re.sub(r"\s+", " ", b).strip()
        if len(b) < 200 or re.search(r"résultats?\s+provisoires?|synthèse\s+d[’']analyse|attribution", b[:300], re.I): continue
        if not re.search(r"région|regional|régional|commune|mairie|gouvernorat|province|conseil", b, re.I): continue
        ref = re.search(r"N°\s*([\w\-./]+(?:/[\w\-./]+)*)", b)
        aut = re.search(r"(?:au profit d[eu']|pour le compte d[eu']|lancée? par)\s*(?:la |le |l[’'])?([^.;]{5,90})", b, re.I)
        objet = re.search(r"(?:pour|relatif à|portant sur)\s+(?:l[’'a]|le |la |les )?\s*([^.]{15,160})", b, re.I)
        sortie.append({"titre": (objet[1] if objet else b[:140]).strip().capitalize(),
            "ref": ref[1] if ref else "", "aut": aut[1].strip() if aut else "Voir l’avis",
            "reg": trouver(REGIONS, b) or "Non précisée", "sec": trouver(SECTEURS, b) or "Autre",
            "typ": type_marche(b[:400]), "date": date_limite(b), "lien": url, "source": f"Quotidien n° {n}"})
    return sortie

def main():
    etat = json.load(open(ETAT)); dernier = etat["dernier"]
    try: appels = json.load(open(SORTIE))
    except Exception: appels = []
    vus = {(a["ref"], a["aut"]) for a in appels if a["ref"]}
    n, trous, faits = dernier + 1, 0, 0
    while trous < MAX_TROUS and faits < MAX_PAR_PASSAGE:
        contenu = url_ok = None
        for modele in MODELES:
            url = modele.format(n=n)
            try:
                r = requests.get(url, headers=ENTETES, timeout=60)
                if r.status_code == 200 and r.content[:4] == b"%PDF": contenu, url_ok = r.content, url; break
            except requests.RequestException as e: print("Erreur réseau", n, e)
        if contenu is None: trous += 1; print("Introuvable :", n); n += 1; continue
        trous = 0
        try:
            nouveaux = [a for a in avis_du_pdf(contenu, n, url_ok) if (a["ref"], a["aut"]) not in vus]
            for a in nouveaux:
                appels.append(a); vus.add((a["ref"], a["aut"]))
            print(f"Quotidien {n} : {len(nouveaux)} avis")
        except Exception as e: print("Lecture impossible", n, e)
        dernier, faits, n = n, faits + 1, n + 1
    limite = (datetime.date.today() - datetime.timedelta(days=60)).isoformat()
    appels = [a for a in appels if not a["date"] or a["date"] >= limite]
    json.dump(appels, open(SORTIE, "w"), ensure_ascii=False, indent=1)
    json.dump({"dernier": dernier}, open(ETAT, "w"))

if __name__ == "__main__": main()

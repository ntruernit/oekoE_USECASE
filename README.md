# OekoEnergie Usecase Demo

Automatisierte Verarbeitung von Auszahlungsbelegen mit zwei WatsonX Orchestrate Agenten und einem FastAPI Mock-Service, gehostet auf IBM Cloud Code Engine.

## Workflow

```
Nutzer lädt PDF hoch
        │
        ▼
[NT_InvoiceAgent_Main]
        │  liest PDF via "Chat with your Documents"
        │  sendet Dokumenttext an ▼
[NT_InvoiceExtractor]
        │  extrahiert Felder (Grundeigentümer, IBAN, Betrag, Fälligkeitsdatum, ...)
        │  gibt JSON zurück an ▲
[NT_InvoiceAgent_Main]
        │  zeigt extrahierte Felder dem Nutzer zur Bestätigung
        │
        ├─ Nutzer bestätigt → POST /odoo/entries (Eintrag anlegen)
        │
        ├─ Nutzer genehmigt → POST /odoo/entries/{id}/approve
        │                   → POST /payments/initiate
        │
        └─ Nutzer lehnt ab → POST /odoo/entries/{id}/reject
                           → Ablehnungsgrund wird erfasst
        ▼
  Mock Odoo ERP (IBM Cloud Code Engine)
```

Der Haupt-Agent übernimmt die gesamte Nutzerinteraktion und API-Aufrufe. Der Extraktor agiert als stiller Kollaborator ohne eigene Tools — er verarbeitet nur Text und gibt strukturiertes JSON zurück. Alle Schritte zur Erstellung, Genehmigung und Zahlung erfordern explizite Nutzerbestätigung.

## Komponenten

- **FastAPI-Service** (`api/main.py`) — läuft auf **IBM Cloud Code Engine**; stellt PDF-Extraktion, Mock-Odoo-ERP und Zahlungs-Gateway bereit. Zustand wird im Arbeitsspeicher gehalten.
- **NT_InvoiceAgent_Main** — läuft in **WatsonX Orchestrate**; liest PDFs über "Chat with your Documents", steuert den Gesamtablauf und interagiert mit dem Nutzer an jedem Bestätigungsschritt.
- **NT_InvoiceExtractor** — läuft in **WatsonX Orchestrate** als stiller Kollaborator; empfängt Dokumenttext und gibt ein strukturiertes JSON-Objekt mit allen Zahlungsfeldern zurück — ohne eigene Tools.
- **Dashboard** — Live-Übersicht aller Einträge und Aktivitäten unter `/dashboard`.

## Starten

```bash
pip install -r api/requirements.txt
uvicorn api.main:app --reload --port 8000
```

## Import in WatsonX Orchestrate

Vor dem Import die `servers`-URL in `tools/odoo.fastapi.json` auf die eigene Code Engine URL anpassen:

```json
"servers": [
  { "url": "https://<ihre-app>.us-south.codeengine.appdomain.cloud" }
]
```

Dann importieren:

```bash
orchestrate tools import -k openapi -f tools/odoo.fastapi.json
orchestrate agents import -f agents/NT_InvoiceExtractor.yaml
orchestrate agents import -f agents/NT_InvoiceAgent.yaml
```

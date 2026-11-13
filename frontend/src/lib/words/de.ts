// Generated from the de catalog by design-catalogs. Do not edit.

export const language = "de";
const numberFormat = new Intl.NumberFormat("de");
const pluralRules = new Intl.PluralRules("de");
// prettier-ignore
export type Words = {
    readonly "app.name": string;
    readonly "header.decks": string;
    readonly "header.settings": string;
    readonly "account.title": string;
    readonly "logout.button": string;
    readonly "legal.impressum": string;
    readonly "legal.datenschutz": string;
    readonly "legal.nutzungsbedingungen": string;
    readonly "decks.title": string;
    readonly "decks.columnState": string;
    readonly "decks.columnDeck": string;
    readonly "decks.columnChanged": string;
    readonly "decks.emptyTitle": string;
    readonly "decks.emptyExplanation": string;
    readonly "deck.decksLink": string;
    readonly "deck.back": string;
    readonly "deck.status": string;
    readonly "deck.stateReady": string;
    readonly "deck.stateBuilding": string;
    readonly "deck.stateFailed": string;
    readonly "deck.stateNeverBuilt": string;
    readonly "deck.built": (gaps: { readonly age: string }) => string;
    readonly "deck.buildsWith": (gaps: { readonly themes: string }) => string;
    readonly "deck.notBuiltExplanation": string;
    readonly "deck.buildingExplanation": string;
    readonly "deck.buildingLabel": string;
    readonly "deck.buildingFor": (gaps: { readonly age: string }) => string;
    readonly "deck.failedTitle": string;
    readonly "deck.attemptLabel": string;
    readonly "deck.failedWithoutMessage": string;
    readonly "deck.failedOutputMegabytes": (gaps: { readonly allowed: string; readonly written: string }) => string;
    readonly "deck.failedOutputEntries": (gaps: { readonly entries: string }) => string;
    readonly "deck.failedOutputShape": string;
    readonly "deck.failedDeckMegabytes": (gaps: { readonly entries: string; readonly megabytes: string }) => string;
    readonly "deck.failedDeckPaths": string;
    readonly "deck.failedLastTalkStands": string;
    readonly "deck.presenterView": string;
    readonly "deck.projectorView": string;
    readonly "deck.pdf": string;
    readonly "deck.unknownTitle": string;
    readonly "deck.unknownExplanation": string;
    readonly "error.notFoundTitle": string;
    readonly "error.notFoundExplanation": string;
    readonly "error.adminsTitle": string;
    readonly "error.adminsExplanation": string;
    readonly "error.decksLink": string;
    readonly "settings.title": string;
    readonly "settings.generalTab": string;
    readonly "settings.navLabel": string;
    readonly "settings.sourcesTab": string;
    readonly "settings.instanceName": string;
    readonly "settings.save": string;
    readonly "sources.title": string;
    readonly "sources.columnSource": string;
    readonly "sources.columnState": string;
    readonly "sources.columnFetched": string;
    readonly "sources.columnAccess": string;
    readonly "sources.fetchNow": string;
    readonly "sources.busyFetch": string;
    readonly "sources.requestFailed": string;
    readonly "sources.add": string;
    readonly "sources.emptyTitle": string;
    readonly "sources.emptyExplanation": string;
    readonly "sources.emptyNeed": readonly (string | { readonly marker: "code"; readonly text: string })[];
    readonly "source.back": string;
    readonly "source.folder": string;
    readonly "source.columnFetched": string;
    readonly "source.fetchNow": string;
    readonly "source.busyFetch": string;
    readonly "source.requestFailed": string;
    readonly "source.busyRenew": string;
    readonly "source.busyCount": string;
    readonly "source.busyRemove": string;
    readonly "source.copy": string;
    readonly "source.secret": string;
    readonly "source.webhookAddress": string;
    readonly "source.webhookSecret": string;
    readonly "source.webhookOnce": string;
    readonly "source.refusedSecret": string;
    readonly "source.accessHeading": string;
    readonly "source.webhookHeading": string;
    readonly "source.recentRuns": string;
    readonly "source.decks": string;
    readonly "source.reverse": string;
    readonly "source.pushInbox": string;
    readonly "source.later": string;
    readonly "source.renew": string;
    readonly "source.secretMask": string;
    readonly "source.accessRenewInfo": string;
    readonly "source.deployKeyInfo": string;
    readonly "source.publicKey": string;
    readonly "source.deployKeyRenew": string;
    readonly "source.renewedSecret": string;
    readonly "source.renewedDeployKey": string;
    readonly "source.renewedWebhook": string;
    readonly "source.runColumnState": string;
    readonly "source.runColumnFetched": string;
    readonly "source.runColumnDetail": string;
    readonly "source.removeHeading": string;
    readonly "source.removeHint": string;
    readonly "source.removeButton": string;
    readonly "source.removeConfirmTitle": (gaps: { readonly name: string }) => string;
    readonly "source.removeCancel": string;
    readonly "source.removeConfirm": string;
    readonly "source.removeRefused": string;
    readonly "source.stateReachable": string;
    readonly "source.stateError": string;
    readonly "source.stateRefused": string;
    readonly "source.stateFailed": string;
    readonly "source.stateNeverFetched": string;
    readonly "source.accessToken": string;
    readonly "source.accessDeployKey": string;
    readonly "source.accessLocal": string;
    readonly "source.refusedName": string;
    readonly "source.refusedFolder": string;
    readonly "source.refusedDuplicateName": string;
    readonly "source.refusedDuplicateUrl": string;
    readonly "source.refusedUnknownAccess": string;
    readonly "source.refusedNotChecked": string;
    readonly "source.checkReachable": (gaps: { readonly commit: string }) => string;
    readonly "source.refusedSecretNotAllowed": string;
    readonly "source.refusedOutsideMount": string;
    readonly "source.refusedDeployKey": string;
    readonly "source.fetchNothingNew": string;
    readonly "source.fetchChanged": string;
    readonly "source.fetchBuilt": (gaps: { readonly count: number }) => string;
    readonly "source.fetchFailed": (gaps: { readonly count: number }) => string;
    readonly "source.fetchRemoved": (gaps: { readonly count: number }) => string;
    readonly "source.fetchBusy": string;
    readonly "source.runFetched": string;
    readonly "source.runUnreachable": string;
    readonly "source.runSecret": string;
    readonly "source.runRefused": string;
    readonly "source.runFailed": string;
    readonly "sourceNew.back": string;
    readonly "sourceNew.title": string;
    readonly "sourceNew.name": string;
    readonly "sourceNew.url": string;
    readonly "sourceNew.folderOptional": string;
    readonly "sourceNew.secret": string;
    readonly "sourceNew.secretInfo": string;
    readonly "sourceNew.busyCheck": string;
    readonly "sourceNew.busyCreate": string;
    readonly "sourceNew.kindPending": string;
    readonly "sourceNew.kindFailed": string;
    readonly "sourceNew.kindRetry": string;
    readonly "sourceNew.webhookLater": string;
    readonly "sourceNew.deployKeyHeading": string;
    readonly "sourceNew.deployKeyHint": string;
    readonly "sourceNew.check": string;
    readonly "sourceNew.create": string;
    readonly "sourceNew.created": string;
    readonly "sourceNew.webhook": string;
    readonly "sourceNew.webhookAddress": string;
    readonly "sourceNew.webhookSecret": string;
    readonly "sourceNew.webhookOnce": string;
    readonly "sourceNew.copy": string;
    readonly "sourceNew.webhookPayload": string;
    readonly "sourceNew.createdDone": string;
    readonly "sourceNew.createdToast": string;
    readonly "source.removeConfirmDecks": (count: number) => string;
    readonly "source.removeConfirmRuns": (count: number) => string;
    readonly "source.removeConfirmUrl": string;
    readonly "source.removedName": (gaps: { readonly name: string }) => string;
    readonly "source.removedDecks": (count: number) => string;
    readonly "source.removedRuns": (count: number) => string;
    readonly "source.removedUrl": string;
};
// prettier-ignore
export const words: Words = {
    "app.name": "Presentator",
    "header.decks": "Decks",
    "header.settings": "Einstellungen",
    "account.title": "Konto",
    "logout.button": "Abmelden",
    "legal.impressum": "Impressum",
    "legal.datenschutz": "Datenschutz",
    "legal.nutzungsbedingungen": "Nutzungsbedingungen",
    "decks.title": "Decks",
    "decks.columnState": "Status",
    "decks.columnDeck": "Deck",
    "decks.columnChanged": "Geändert",
    "decks.emptyTitle": "Noch kein Deck",
    "decks.emptyExplanation": "Decks kommen nur aus Git. Lege per Push einen Ordner mit deck.toml und slides.md in eine deiner Quellen.",
    "deck.decksLink": "Decks",
    "deck.back": "← Decks",
    "deck.status": "Status",
    "deck.stateReady": "bereit",
    "deck.stateBuilding": "wird gebaut",
    "deck.stateFailed": "fehlgeschlagen",
    "deck.stateNeverBuilt": "nie gebaut",
    "deck.built": (gaps: { readonly age: string }) => "gebaut " + gaps.age,
    "deck.buildsWith": (gaps: { readonly themes: string }) => "Gebaut mit: " + gaps.themes,
    "deck.notBuiltExplanation": "Aus diesem Deck wurde noch kein Vortrag gebaut, deshalb gibt es keine Ansicht zum Öffnen.",
    "deck.buildingExplanation": "Beide Ansichten lassen sich öffnen, sobald der Build fertig ist. Lade die Seite neu.",
    "deck.buildingLabel": "Wird gebaut",
    "deck.buildingFor": (gaps: { readonly age: string }) => "seit " + gaps.age,
    "deck.failedTitle": "Fehler",
    "deck.attemptLabel": "Versuch",
    "deck.failedWithoutMessage": "Dieser Build hat keine Meldung hinterlassen. Vielleicht hat er zu lange gedauert oder wurde abgebrochen.",
    "deck.failedOutputMegabytes": (gaps: { readonly allowed: string; readonly written: string }) => "Der Vortrag, den dieser Build geschrieben hat, ist " + gaps.written + " MB groß; ein Build darf höchstens " + gaps.allowed + " MB hinterlassen.",
    "deck.failedOutputEntries": (gaps: { readonly entries: string }) => "Der Vortrag, den dieser Build geschrieben hat, enthält mehr als " + gaps.entries + " Dateien; ein Build darf weniger hinterlassen.",
    "deck.failedOutputShape": "Der Vortrag, den dieser Build geschrieben hat, kann nicht übernommen werden: Er enthält einen Pfad mit mehr als 32 Teilen (Ordner und Datei) oder 1024 Bytes, eine Datei, die dieser Schritt nicht schreiben darf, einen Link, eine Spezialdatei oder eine nicht lesbare Datei.",
    "deck.failedDeckMegabytes": (gaps: { readonly entries: string; readonly megabytes: string }) => "Dieses Deck ist zu groß zum Bauen: Ein Deck darf höchstens " + gaps.megabytes + " MB und höchstens " + gaps.entries + " Dateien und Ordner zusammen enthalten, den obersten Ordner node_modules nicht mitgezählt.",
    "deck.failedDeckPaths": "Dieses Deck kann nicht gebaut werden: Es enthält einen Pfad mit mehr als 32 Teilen (Ordner und Datei) oder 1024 Bytes oder einen Link, eine Spezialdatei oder eine nicht lesbare Datei.",
    "deck.failedLastTalkStands": "Der zuletzt gebaute Vortrag lässt sich weiterhin in beiden Ansichten öffnen.",
    "deck.presenterView": "Presenter-Ansicht",
    "deck.projectorView": "Projektor-Ansicht",
    "deck.pdf": "PDF",
    "deck.unknownTitle": "Kein solches Deck",
    "deck.unknownExplanation": "Unter dieser Adresse gibt es kein Deck.",
    "error.notFoundTitle": "Nicht gefunden",
    "error.notFoundExplanation": "Unter dieser Adresse gibt es nichts.",
    "error.adminsTitle": "Für Admins",
    "error.adminsExplanation": "Einstellungen sind für Admins. Dein Konto hat keine Admin-Rolle.",
    "error.decksLink": "Decks",
    "settings.title": "Allgemein",
    "settings.generalTab": "Allgemein",
    "settings.navLabel": "Einstellungen",
    "settings.sourcesTab": "Quellen",
    "settings.instanceName": "Name der Instanz",
    "settings.save": "Speichern",
    "sources.title": "Quellen",
    "sources.columnSource": "Quelle",
    "sources.columnState": "Status",
    "sources.columnFetched": "Abgerufen",
    "sources.columnAccess": "Zugriff",
    "sources.fetchNow": "Jetzt abrufen",
    "sources.busyFetch": "Wird abgerufen…",
    "sources.requestFailed": "Die Anfrage ist fehlgeschlagen. Versuch es noch einmal.",
    "sources.add": "Quelle hinzufügen",
    "sources.emptyTitle": "Noch keine Quelle",
    "sources.emptyExplanation": "Eine Quelle ist ein Git-Repository, das der Server alle paar Minuten liest – GitHub, GitLab, firmenintern oder Bare.",
    "sources.emptyNeed": ["Du brauchst die Git-URL und ein Secret mit reinem Lesezugriff: ", { marker: "code", text: "SSH-Deploy-Key" }, " oder ", { marker: "code", text: "HTTPS-Token" }] as readonly (string | { readonly marker: "code"; readonly text: string })[],
    "source.back": "← Quellen",
    "source.folder": "Ordner",
    "source.columnFetched": "Abgerufen",
    "source.fetchNow": "Jetzt abrufen",
    "source.busyFetch": "Wird abgerufen…",
    "source.requestFailed": "Die Anfrage ist fehlgeschlagen. Versuch es noch einmal.",
    "source.busyRenew": "Wird erneuert…",
    "source.busyCount": "Wird gezählt…",
    "source.busyRemove": "Wird entfernt…",
    "source.copy": "Kopieren",
    "source.secret": "Secret",
    "source.webhookAddress": "Adresse",
    "source.webhookSecret": "Secret",
    "source.webhookOnce": "nur jetzt sichtbar",
    "source.refusedSecret": "Gib zuerst das neue Secret ein.",
    "source.accessHeading": "Zugriff auf Git",
    "source.webhookHeading": "Webhook",
    "source.recentRuns": "Letzte Läufe",
    "source.decks": "Decks aus dieser Quelle",
    "source.reverse": "Umgekehrte Richtung",
    "source.pushInbox": "Push-Eingang – der Heimserver bietet ein eigenes Git an, wenn er dein Firmen-Git nicht erreicht",
    "source.later": "später",
    "source.renew": "Secret erneuern",
    "source.secretMask": "••••••••••••••••••",
    "source.accessRenewInfo": "Verschlüsselt gespeichert und seit der Eingabe nie wieder angezeigt. Beim Erneuern wird es durch ein neues ersetzt.",
    "source.deployKeyInfo": "Trage diesen öffentlichen Schlüssel beim Git-Host mit Lesezugriff ein. Beim Erneuern ersetzt er den alten sofort in dieser Quelle.",
    "source.publicKey": "Öffentlicher Schlüssel",
    "source.deployKeyRenew": "Erneuern",
    "source.renewedSecret": "Secret erneuert.",
    "source.renewedDeployKey": "Deploy-Key erneuert. Trage den neuen öffentlichen Schlüssel beim Git-Host ein.",
    "source.renewedWebhook": "Webhook-Secret erneuert. Trage es beim Git-Host ein.",
    "source.runColumnState": "Status",
    "source.runColumnFetched": "Abgerufen",
    "source.runColumnDetail": "Details",
    "source.removeHeading": "Quelle entfernen",
    "source.removeHint": "Löscht den Mirror, seine Decks und deren gebaute Vorträge; die URL lässt sich wieder hinzufügen.",
    "source.removeButton": "Quelle entfernen…",
    "source.removeConfirmTitle": (gaps: { readonly name: string }) => gaps.name + " entfernen?",
    "source.removeCancel": "Abbrechen",
    "source.removeConfirm": "Entfernen",
    "source.removeRefused": "Der Mirror oder ein Build-Ordner konnte nicht entfernt werden, deshalb wurde nichts gelöscht.",
    "source.stateReachable": "erreichbar",
    "source.stateError": "Fehler",
    "source.stateRefused": "Abgelehnt",
    "source.stateFailed": "Fehlgeschlagen",
    "source.stateNeverFetched": "nie abgerufen",
    "source.accessToken": "Token",
    "source.accessDeployKey": "Deploy-Key",
    "source.accessLocal": "Auf diesem Rechner",
    "source.refusedName": "Ein Name besteht aus Kleinbuchstaben, Ziffern und Bindestrichen, beginnt mit einem Buchstaben oder einer Ziffer und ist höchstens 64 Zeichen lang.",
    "source.refusedFolder": "Ein Ordner ist ein Pfad im Repository, etwa examples oder talks/2026, ohne führenden Schrägstrich und ohne .. darin.",
    "source.refusedDuplicateName": "Eine Quelle mit diesem Namen gibt es schon.",
    "source.refusedDuplicateUrl": "Eine Quelle mit dieser Git-URL gibt es schon.",
    "source.refusedUnknownAccess": "Nutze https://host/pfad, user@host:pfad, ssh://user@host/pfad oder einen Ordner auf diesem Rechner, ohne Benutzername und Passwort darin.",
    "source.refusedNotChecked": "Prüfe die Verbindung noch einmal – die URL oder das Secret hat sich seit der letzten Prüfung geändert.",
    "source.checkReachable": (gaps: { readonly commit: string }) => "Repository gelesen, Head " + gaps.commit + ".",
    "source.refusedSecretNotAllowed": "Ein Schlüssel oder ein Ordner auf diesem Rechner braucht kein Token – lass das Feld leer.",
    "source.refusedOutsideMount": "Die Adresse muss einen Pfad unter /data/local-sources nennen, wo das Host-Verzeichnis eingebunden ist.",
    "source.refusedDeployKey": "Dieser Deploy-Key gehört dir nicht mehr – hier ist ein neuer zum Eintragen.",
    "source.fetchNothingNew": "Abgerufen, nichts Neues.",
    "source.fetchChanged": "Abgerufen.",
    "source.fetchBuilt": (gaps: { readonly count: number }) => "Decks gebaut: " + numberFormat.format(gaps.count),
    "source.fetchFailed": (gaps: { readonly count: number }) => "Builds fehlgeschlagen: " + numberFormat.format(gaps.count),
    "source.fetchRemoved": (gaps: { readonly count: number }) => "Decks entfernt: " + numberFormat.format(gaps.count),
    "source.fetchBusy": "Mit einem anderen Auftrag beschäftigt, nichts abgerufen. Versuch es gleich noch einmal.",
    "source.runFetched": "abgerufen",
    "source.runUnreachable": "Nicht erreichbar: Der Host antwortet nicht, oder das Repository ist privat und braucht ein Token.",
    "source.runSecret": "Secret nicht gespeichert",
    "source.runRefused": "Anmeldung abgelehnt",
    "source.runFailed": "Abruf fehlgeschlagen",
    "sourceNew.back": "← Quellen",
    "sourceNew.title": "Quelle hinzufügen",
    "sourceNew.name": "Name",
    "sourceNew.url": "Git-URL",
    "sourceNew.folderOptional": "Ordner (optional)",
    "sourceNew.secret": "Secret",
    "sourceNew.secretInfo": "Nur ein privates Repository braucht ein Token. Es wird verschlüsselt gespeichert, nach dem Anlegen nie wieder angezeigt und braucht nur Lesezugriff – der Server schreibt nie zurück nach Git.",
    "sourceNew.busyCheck": "Wird geprüft…",
    "sourceNew.busyCreate": "Wird angelegt und gebaut…",
    "sourceNew.kindPending": "Adresse wird gelesen.",
    "sourceNew.kindFailed": "Die Adresse konnte nicht gelesen werden. Versuch es noch einmal.",
    "sourceNew.kindRetry": "Erneut lesen",
    "sourceNew.webhookLater": "Die Webhook-Adresse und ihr Secret entstehen erst, wenn du die Quelle hinzufügst.",
    "sourceNew.deployKeyHeading": "Auf der Gegenseite eintragen",
    "sourceNew.deployKeyHint": "Der öffentliche Schlüssel des Servers – als Deploy-Key mit Lesezugriff",
    "sourceNew.check": "Verbindung prüfen",
    "sourceNew.create": "Anlegen",
    "sourceNew.created": "angelegt",
    "sourceNew.webhook": "Webhook – trage beide in dein GitLab ein",
    "sourceNew.webhookAddress": "Adresse",
    "sourceNew.webhookSecret": "Secret",
    "sourceNew.webhookOnce": "nur jetzt sichtbar",
    "sourceNew.copy": "Kopieren",
    "sourceNew.webhookPayload": "Der Server liest die Nutzdaten des Hosts nicht – jeder gültige Aufruf heißt nur „Jetzt abrufen“.",
    "sourceNew.createdDone": "Fertig",
    "sourceNew.createdToast": "Quelle angelegt",
    "source.removeConfirmDecks": (count: number) => ({
        zero: numberFormat.format(count) + " Decks und ihre Vorträge gehen mit.",
        one: numberFormat.format(count) + " Deck und sein gebauter Vortrag gehen mit.",
        two: numberFormat.format(count) + " Decks und ihre Vorträge gehen mit.",
        few: numberFormat.format(count) + " Decks und ihre Vorträge gehen mit.",
        many: numberFormat.format(count) + " Decks und ihre Vorträge gehen mit.",
        other: numberFormat.format(count) + " Decks und ihre Vorträge gehen mit.",
    })[pluralRules.select(count)],
    "source.removeConfirmRuns": (count: number) => ({
        zero: numberFormat.format(count) + " Läufe gehen mit.",
        one: numberFormat.format(count) + " Lauf geht mit.",
        two: numberFormat.format(count) + " Läufe gehen mit.",
        few: numberFormat.format(count) + " Läufe gehen mit.",
        many: numberFormat.format(count) + " Läufe gehen mit.",
        other: numberFormat.format(count) + " Läufe gehen mit.",
    })[pluralRules.select(count)],
    "source.removeConfirmUrl": "Die URL lässt sich wieder hinzufügen.",
    "source.removedName": (gaps: { readonly name: string }) => gaps.name + " ist weg.",
    "source.removedDecks": (count: number) => ({
        zero: numberFormat.format(count) + " Decks gingen mit.",
        one: numberFormat.format(count) + " Deck ging mit.",
        two: numberFormat.format(count) + " Decks gingen mit.",
        few: numberFormat.format(count) + " Decks gingen mit.",
        many: numberFormat.format(count) + " Decks gingen mit.",
        other: numberFormat.format(count) + " Decks gingen mit.",
    })[pluralRules.select(count)],
    "source.removedRuns": (count: number) => ({
        zero: numberFormat.format(count) + " Läufe gingen mit.",
        one: numberFormat.format(count) + " Lauf ging mit.",
        two: numberFormat.format(count) + " Läufe gingen mit.",
        few: numberFormat.format(count) + " Läufe gingen mit.",
        many: numberFormat.format(count) + " Läufe gingen mit.",
        other: numberFormat.format(count) + " Läufe gingen mit.",
    })[pluralRules.select(count)],
    "source.removedUrl": "Die URL lässt sich wieder hinzufügen.",
};
export type Key = keyof Words;

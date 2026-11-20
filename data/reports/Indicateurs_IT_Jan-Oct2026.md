# Indicateurs des demandes IT internes — janvier à octobre 2026

Périmètre : `wd.it.request`, demandes créées entre le 01/01/2026 et le
31/10/2026, **annulations exclues** (R16). Source : import de
`Demandes_IT_Historique` (420 lignes, 5 doublons retirés en amont).

## Volume et états

| Indicateur | Valeur |
|---|---|
| Volume total | **384 demandes** |
| Clôturées | 330 (86 %) |
| Résolues (non clôturées) | 51 |
| En attente utilisateur | 2 |
| En cours | 1 |
| Backlog en fin d'octobre | **3 demandes ouvertes** |
| Demandes réouvertes | 19 (compteur R15 actif) |

## SLA (R12/R13)

| Indicateur | Valeur |
|---|---|
| % SLA 1re réponse | **56,1 %** (213/380 mesurées) |
| % SLA résolution | **47,5 %** (181/381 mesurées) |
| Délai net moyen de résolution | **28,2 h** |
| Délai net médian | **20,6 h** |

## Détail par priorité

| Priorité | Volume | % SLA réponse | % SLA résolution |
|---|---|---|---|
| P1 (bloquant multi) | 9 | 44 % (4/9) | 67 % (6/9) |
| P2 (bloquant simple) | 89 | 56 % (50/89) | 49 % (44/89) |
| P3 (gêne) | 183 | 58 % (105/180) | 39 % (70/181) |
| P4 (planifiable) | 103 | 53 % (54/102) | 60 % (61/102) |

## Détail par technicien

| Technicien | Volume | % SLA résolution | Demandes avec délai net |
|---|---|---|---|
| Messan AGBODJAN | 86 | 49 % (42/85) | 84 |
| Kodjo MENSAH | 80 | 48 % (38/80) | 79 |
| Enyonam TCHALLA | 77 | 50 % (38/76) | 75 |
| Elom BRUCE | 77 | 51 % (39/76) | 76 |
| Abla DJOSSOU | 64 | 38 % (24/64) | 64 |

## Répartition par canal

| Canal | Volume | Part |
|---|---|---|
| WhatsApp | 199 | 52 % |
| Email | 78 | 20 % |
| Autre (oral, guichet) | 74 | 19 % |
| Téléphone | 33 | 9 % |

## Commentaire (ce que ces chiffres disent du service actuel)

Le volume est réel (≈ 38 demandes/mois) mais **plus de la moitié des
demandes dépassent le SLA de réponse et moins d'une sur deux atteint le
SLA de résolution** — le service vivait sans file structurée ni mesure,
ce qui explique l'absence de tout suivi. Les P1 ne sont pas mieux traités
que les P4 : sans priorisation effective, un blocage multi-personnes
attend autant qu'une demande planifiable. Les 19 réouvertures (~5 %)
montrent un taux de résolution au premier coup honorable, mais les
délais médians de 20 h masquent probablement de longues attentes
utilisateur aujourd'hui neutralisées par R13. Enfin, 52 % des demandes
arrivaient par WhatsApp : le module supprime ce canal non traçable et
fournit enfin des indicateurs fiables par technicien et par priorité.

# NET Thermal Simulator

Application autonome qui simule un device NET FreakyDomos pour valider le
thermostat maître sur quatre zones thermiques.

Le device expose sur une connexion TCP NET :

- quatre capteurs `TEMP` ;
- quatre sorties `AIR POS VALVE` commandables par le master ;
- une sortie `HEATER` commandable sur les niveaux 1 à 5.

L'application ne réutilise pas le simulateur FreakyDomos existant. Elle
réimplémente uniquement le contrat de trame NET nécessaire à l'interopérabilité.

## Démarrage Docker

Copier `.env.example` vers `.env`, renseigner le jeton du master et les
paramètres InfluxDB, puis lancer :

```sh
docker compose --env-file .env up --build
```

L'interface est disponible sur <http://localhost:8090>.

Le conteneur se connecte au master sur TCP `8082` et commence par envoyer :

```text
AUTH <MASTER_TOKEN>\n
```

Le master doit être accessible depuis le réseau Docker. Sous Docker Desktop,
un master installé sur l'hôte est généralement joignable avec
`host.docker.internal`.

## Configuration

Les variables principales sont :

| Variable | Rôle |
|---|---|
| `NET_MASTER_HOST` | adresse IP ou nom du master |
| `NET_MASTER_PORT` | port TCP NET, 8082 par défaut |
| `NET_MASTER_TOKEN` | jeton d'authentification NET |
| `NET_DEVICE_ID` | identité NET, 1 à 5 caractères |
| `INFLUXDB_URL` | URL de l'instance InfluxDB externe |
| `INFLUXDB_TOKEN` | jeton InfluxDB |
| `INFLUXDB_ORG` | organisation InfluxDB |
| `INFLUXDB_BUCKET` | bucket InfluxDB |
| `WEATHER_CSV` | chemin optionnel vers les données météo 2024 |
| `SIMULATION_INITIAL_TEMPERATURE` | température initiale, 20 °C par défaut |
| `SIMULATION_STEP_SECONDS` | pas de calcul, 10 secondes par défaut |

Le CSV météo doit contenir au minimum deux colonnes :

```csv
timestamp,temp_c
2024-01-01T00:00:00+01:00,4.2
```

Sans fichier CSV, l'interface fonctionne avec une température extérieure
manuelle et signale qu'aucune série Météo-France n'est chargée. Le simulateur
ne prétend pas que sa courbe de démonstration est une donnée historique réelle.

Un fichier prêt à l'emploi est fourni dans `data/` pour Luz-Saint-Sauveur. Il
est chargé dans le conteneur avec un montage de volume et la configuration
suivante :

```yaml
volumes:
  - ./data:/data:ro
```

```env
WEATHER_CSV=/data/weather_2024_luz_saint_sauveur.csv
```

## Fonctionnement thermique

Le calcul est exécuté toutes les 10 secondes en temps réel. Le scénario continue
jusqu'à un arrêt manuel depuis l'interface. Le modèle utilise une capacité
thermique et un coefficient de pertes par pièce, avec un échange simplifié
entre la chambre étage et la chambre RDC/SDB.

Une source auxiliaire de 9 kW est disponible dans le salon. Elle est activée
ou désactivée par une case à cocher dans l'interface, sans rampe. Sa puissance
est incluse dans la puissance injectée du salon et dans l'historique des
graphes.

Les points de simulation sont conservés côté serveur et disponibles via
`GET /api/history`. Les graphes température, vannes et puissance conservent
les points reçus, proposent un zoom à la molette, un déplacement horizontal
par glisser-déposer et des repères date/heure sur l'axe temporel.

Les paramètres physiques sont volontairement simples et calibrables dans
`thermal.py`. Les ouvertures des vannes servent à répartir la puissance
disponible : elles ne sont pas plafonnées individuellement. Le HEATER fournit
3, 5.25, 7.5, 9.75 ou 12 kW selon son niveau ; avec deux vannes ouvertes à
100 % au niveau 5, chaque pièce reçoit donc 6 kW.

## InfluxDB

Les mesures sont écrites dans l'instance externe lorsqu'elle est configurée :

- `thermal_room` : température, puissance reçue et ouverture de vanne ;
- `thermal_environment` : température extérieure ;
- `thermal_master` : niveau HEATER et puissance distribuée.

Une indisponibilité InfluxDB ne stoppe pas la simulation ; l'erreur est
visible dans l'interface et les nouvelles mesures seront réessayées.

## Déploiement TrueNAS

Depuis une tâche Shell ou un terminal TrueNAS, récupérer la release puis
préparer la configuration :

```sh
git clone --branch v0.1.0 https://github.com/freakstreet/FreakyDomos-Thermal_Simulator.git
cd FreakyDomos-Thermal_Simulator
cp .env.example .env
vi .env
docker compose up -d --build
```

Dans `.env`, renseigner l'adresse IP du master NET et les paramètres de
l'instance InfluxDB externe. Le port web publié est `8090` par défaut. La
page est ensuite accessible sur `http://ADRESSE_TRUENAS:8090/`.

Le dossier `data/` est monté en lecture seule dans le conteneur afin de
conserver les données météo fournies avec la release.

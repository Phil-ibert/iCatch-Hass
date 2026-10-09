# iCatch DVR pour Home Assistant

Affiche dans Home Assistant les caméras d'un enregistreur **iCatch** (vendu notamment sous la marque **iWatch** avec le logiciel *iWatchDVR*) :

- un **aperçu basse définition en direct** dans les tableaux de bord, léger pour le DVR et le réseau ;
- le **flux haute définition** quand on ouvre une caméra.

Sur l'iWatch testé, ni ONVIF ni chemin RTSP fonctionnel (le port 554 est ouvert, mais les URL usuelles échouent) : la vidéo passe par un protocole HTTP propriétaire. Le dépôt contient les deux pièces nécessaires :

| Pièce | Rôle |
|---|---|
| **Add-on `iCatch DVR`** (`icatch_dvr/`) | Se connecte au DVR, traduit son protocole et republie chaque caméra en RTSP via un go2rtc intégré, à la demande (aucune connexion au DVR tant que personne ne regarde). |
| **Intégration `iCatch DVR`** (`custom_components/icatch_dvr/`, via HACS) | Est proposée automatiquement par l'add-on, vous laisse choisir les caméras et crée pour chacune une entité **HD** et une entité **Aperçu**. |

```
DVR iCatch ──HTTP propriétaire──▶ add-on (pont + go2rtc) ──RTSP──▶ go2rtc de Home Assistant ──WebRTC──▶ navigateur / appli
```

## Prérequis

- Home Assistant OS ou Supervised (pour les add-ons), version 2024.12 ou plus récente.
- [HACS](https://hacs.xyz) pour installer l'intégration.
- L'adresse IP du DVR, le **port de son interface web** (1027 sur l'iWatch testé) et un compte utilisateur.

## Installation

### 1. L'add-on

1. **Paramètres → Modules complémentaires** (« Applications » dans les versions récentes) **→ Boutique → ⋮ → Dépôts**, ajoutez :
   `https://github.com/Phil-ibert/iCatch-Hass`
2. Installez **iCatch DVR**. La construction télécharge go2rtc et prend quelques minutes.
3. Onglet **Configuration** : renseignez au minimum le **mot de passe**, vérifiez l'adresse, le port et la liste des **caméras**.
4. **Démarrer**, puis ouvrez le **Journal**. Il liste les entrées où le DVR voit une image :

   ```
   caméra 1: h264 640x368 @12 i/s, 450 kbit/s - branchée
   caméra 6: h264 640x368 @1 i/s, 60 kbit/s - pas de signal (VIDEO LOSS ?)
   ```

### 2. L'intégration

1. **HACS → ⋮ → Dépôts personnalisés** : URL `https://github.com/Phil-ibert/iCatch-Hass`, type **Intégration**.
2. Téléchargez **iCatch DVR**, puis redémarrez Home Assistant.
3. Redémarrez l'add-on : il se signale à Home Assistant, qui affiche **« Nouvel appareil découvert : iCatch DVR »** dans **Paramètres → Appareils et services**. Cliquez sur **Ajouter**. L'adresse et les identifiants de connexion sont transmis automatiquement.
4. Cochez les caméras à ajouter.

Saisie manuelle (si la découverte n'apparaît pas) : définissez l'option `api_password` de l'add-on, puis **Ajouter une intégration → iCatch DVR** avec l'utilisateur `admin` et ce mot de passe. L'hôte est le nom affiché en tête du journal de l'add-on (`xxxxxxxx-icatch-dvr`).

Chaque caméra devient un appareil **Caméra DVR N** avec deux entités :

| Entité | Flux | Usage |
|---|---|---|
| `camera.camera_dvr_N` | HD (2560×1920 en H.265 sur l'iWatch testé) | vue détaillée |
| `camera.camera_dvr_N_apercu` | basse définition (640×368 en H.264) | mosaïque, miniatures |

Ces identifiants sont ceux d'un Home Assistant en français ; en anglais, ce sont `camera.dvr_camera_N` et `camera.dvr_camera_N_preview`. Les miniatures fixes (vignettes, notifications) viennent toujours du flux d'aperçu.

### 3. Le tableau de bord

[`examples/dashboard.yaml`](examples/dashboard.yaml) contient une mosaïque prête à coller dans une carte **Manuel** (Modifier le tableau de bord → Ajouter une carte → Manuel). Chaque carte affiche l'aperçu en direct, et un clic ouvre la HD :

```yaml
type: picture-entity
entity: camera.camera_dvr_1_apercu   # aperçu en direct dans la mosaïque
camera_view: live
name: Caméra 1
show_state: false
tap_action:
  action: more-info
  entity: camera.camera_dvr_1        # HD à l'ouverture
```

La carte doit pointer sur l'entité *aperçu*. Quand `entity` est une caméra, `picture-entity` ignore `camera_image` et diffuse l'entité elle-même ; on choisit donc la HD avec `tap_action`.

## Options de l'add-on

| Option | Défaut | Description |
|---|---|---|
| `dvr_host` | `192.168.1.108` | Adresse du DVR. |
| `dvr_port` | `1027` | Port de l'**interface web** du DVR (pas le 554). |
| `username` / `password` | `admin` / — | Compte du DVR. |
| `cameras` | `1` à `5` | Entrées exposées (1 à 16). |
| `hd_video` | `copy` | `copy` : HD d'origine, sans réencodage. `h264_1080p` : réencodage processeur en H.264 réduit à 1080 lignes, lisible partout. `h264` : réencodage en pleine résolution (le plus gourmand). |
| `api_password` | vide | Vide : mot de passe aléatoire généré et transmis à Home Assistant. Rempli : utilisateur `admin` avec ce mot de passe, pour ouvrir l'interface web go2rtc ou configurer l'intégration à la main. |
| `log_level` | `info` | |

### Ajouter une caméra

1. Ajoutez son numéro dans l'option `cameras` de l'add-on, puis redémarrez l'add-on.
2. **Appareils et services → iCatch DVR → Configurer**, cochez la nouvelle caméra.

Décocher une caméra dans l'intégration supprime ses entités et son appareil.

## Dépannage

- **La HD reste noire alors que l'aperçu marche.** Le flux HD du DVR est en H.265, que certains navigateurs ne savent pas afficher en WebRTC. Passez `hd_video` à `h264_1080p`. Le réencodage ne tourne que pendant qu'on regarde la HD, mais il décode du 5 Mpx : sur un petit ARM (Raspberry Pi), il peut ne pas suivre le temps réel.
- **« Le DVR refuse l'identifiant ou le mot de passe »** dans le journal : corrigez `username` / `password`.
- **« DVR injoignable »** : vérifiez `dvr_host` et `dvr_port`. Le port est celui de la page web du DVR.
- **Tester les flux sans l'intégration** : définissez `api_password`, attribuez un port à `1986/tcp` dans l'onglet *Réseau* de l'add-on, puis ouvrez `http://<ip-de-home-assistant>:1986` (utilisateur `admin`).
- **Logs détaillés** : `log_level: debug`.
- **Connexions au DVR** : chaque flux regardé ouvre sa propre connexion au DVR (aperçu et HD séparés). Si des flux décrochent quand beaucoup de caméras sont affichées, la limite de sessions simultanées du DVR est probablement atteinte.

## Sécurité

- L'API et le serveur RTSP de go2rtc exigent toujours un identifiant et un mot de passe : générés au premier démarrage (conservés dans le stockage privé de l'add-on) et transmis à Home Assistant par la découverte du Supervisor, ou définis par `api_password`.
- Aucun port n'est publié par défaut : l'add-on n'est joignable que depuis le réseau interne de Home Assistant.
- go2rtc reçoit sa configuration en ligne de commande, sans fichier : son API ne peut pas la réécrire ni la rendre persistante. `allow_paths` limite les sources `exec`/`echo` au seul pont iCatch, même pour des flux ajoutés via l'API.
- Le mot de passe du DVR passe par une variable d'environnement : il n'apparaît ni dans la configuration go2rtc ni dans les journaux.

## Comment ça marche

Le logiciel Windows *iWatchDVR* n'est qu'un lanceur : il découvre le DVR (WS-Discovery), dialogue en HTTP (Basic auth) et passe par le P2P ThroughTek pour l'accès distant. La vidéo, elle, arrive par :

```
GET /cgi-bin/net_video.cgi?hq=<0|1>&iframe=<masque>&pframe=<masque>&audio=0&complete=0&beg=-1&end=-1&ivs=0
```

- `hq=0` : sous-flux basse définition, `hq=1` : flux principal.
- `iframe` / `pframe` : masque de bits des caméras (bit 0 = caméra 1).
- Réponse `multipart/x-mixed-replace` (délimiteur `--myboundary`). Chaque partie est un message binaire : en-tête de 0x120 octets (magic `0x00001234`, heure UNIX, décalage horaire, longueur, nombre de blocs), puis des blocs avec un en-tête de 0x2C octets (type, canal, largeur, hauteur, cadence, compteur µs, taille), qui contiennent du H.264 (types 0/1) ou du H.265 (types 11/12) Annex-B.

Le détail est documenté dans [`icatch_dvr/app/icatch_protocol.py`](icatch_dvr/app/icatch_protocol.py). Merci au ticket [go2rtc #676](https://github.com/AlexxIT/go2rtc/issues/676) et au projet [icatch-dvr-demux](https://github.com/AlexCherrypi/icatch-dvr-demux), qui avaient décrit ce format.

## Développement

```bash
python3 -m unittest discover -s tests -v
```

Les tests n'ont besoin que de Python 3.11+ et ffmpeg. Ils font tourner un faux DVR qui rejoue un flux synthétique. Pour les lancer sur de vraies captures (`curl -o` de `net_video.cgi`), définissez `ICATCH_CAPTURE_SD` et `ICATCH_CAPTURE_HD`. Ne versionnez pas ces captures : elles contiennent vos images de vidéosurveillance (voir `.gitignore`).

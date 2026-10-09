# iCatch DVR

Passerelle entre un enregistreur **iCatch / iWatch** et Home Assistant. Pour chaque caméra listée dans l'option `cameras`, l'add-on publie deux flux RTSP, démarrés seulement quand quelqu'un regarde :

- `camN_sd` : aperçu basse définition (H.264) ;
- `camN_hd` : haute définition (H.265 sur les DVR récents, ou réencodé en H.264 selon `hd_video`).

Les entités caméra sont créées par l'intégration **iCatch DVR** du même dépôt, à installer via HACS. Une fois l'intégration installée, l'add-on se signale à chaque démarrage : Home Assistant affiche « Nouvel appareil découvert », avec l'adresse et les identifiants déjà remplis. Voir le [README](https://github.com/Phil-ibert/iCatch-Hass) pour la mise en place complète et la carte de tableau de bord.

## Configuration

- **Adresse du DVR / port web** : le port est celui de la page web du DVR (1027 sur l'iWatch), pas le 554.
- **Identifiant / mot de passe** : un compte du DVR.
- **Caméras** : numéros des entrées à exposer. Au démarrage, le journal indique les entrées où le DVR voit une image.
- **Vidéo HD** : `copy` (HD d'origine), `h264_1080p` (réencodage processeur réduit à 1080 lignes), `h264` (réencodage en pleine résolution). Si la HD reste noire dans votre navigateur, passez à `h264_1080p`.
- **Mot de passe go2rtc** : facultatif. Vide, un mot de passe aléatoire est généré et transmis à Home Assistant. Rempli, l'utilisateur est `admin` : utile pour l'interface web go2rtc ou une configuration manuelle de l'intégration.

## Réseau

Aucun port n'est publié par défaut : l'intégration joint l'add-on par le réseau interne de Home Assistant. L'API et le RTSP exigent toujours un mot de passe. Pour tester les flux dans l'interface web de go2rtc, définissez le mot de passe go2rtc, attribuez un port à `1986/tcp`, puis ouvrez `http://<ip-de-home-assistant>:<port>` (utilisateur `admin`).

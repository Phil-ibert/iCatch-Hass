# Changelog

## 1.0.0

- Première version : pont vers le protocole `net_video.cgi` des DVR iCatch, flux aperçu (H.264) et HD (H.265, ou réencodé en H.264) par caméra via go2rtc 1.9.14, à la demande.
- Sondage des caméras au démarrage.
- Découverte Supervisor : l'intégration iCatch DVR est proposée automatiquement, avec les identifiants de connexion.
- go2rtc verrouillé : API et RTSP authentifiés, configuration sans fichier, `allow_paths` pour `exec` et `echo`.

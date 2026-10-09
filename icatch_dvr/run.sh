#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail

# DVR settings go to the environment: go2rtc passes it to the exec sources,
# so the password never appears on a command line or in go2rtc's logs.
ICATCH_HOST="$(bashio::config 'dvr_host')"
ICATCH_PORT="$(bashio::config 'dvr_port')"
ICATCH_USERNAME="$(bashio::config 'username')"
ICATCH_PASSWORD="$(bashio::config 'password')"
ICATCH_LOG_LEVEL="$(bashio::config 'log_level')"
export ICATCH_HOST ICATCH_PORT ICATCH_USERNAME ICATCH_PASSWORD ICATCH_LOG_LEVEL

bashio::log.info "DVR : ${ICATCH_HOST}:${ICATCH_PORT} (utilisateur ${ICATCH_USERNAME})"
bashio::log.info "Nom d'hôte de cet add-on sur le réseau interne : $(hostname)"

bashio::log.info "Caméras vues par le DVR (flux basse définition, 4 s d'écoute) :"
set +e
python3 /opt/icatch/icatch_probe.py --seconds 4
probe=$?
set -e
if [ "${probe}" -eq 3 ]; then
    bashio::log.error "Le DVR refuse l'identifiant ou le mot de passe : corrigez-les dans la configuration."
elif [ "${probe}" -ne 0 ]; then
    bashio::log.warning "DVR injoignable pour l'instant : vérifiez l'adresse et le port web (go2rtc démarre quand même)."
fi

python3 /opt/icatch/gen_go2rtc.py /data/options.json /data/credentials.json \
    /tmp/go2rtc.json /tmp/discovery.json

# Hand the connection details (including the go2rtc credentials) to Home
# Assistant: the iCatch DVR integration is offered or updated automatically.
if bashio::discovery "icatch_dvr" "$(cat /tmp/discovery.json)" >/dev/null; then
    bashio::log.info "Home Assistant informé : ajoutez ou confirmez l'intégration iCatch DVR."
else
    bashio::log.warning "Découverte Home Assistant impossible : ajoutez l'intégration à la main."
fi
rm -f /tmp/discovery.json

bashio::log.info "Démarrage de go2rtc (API :1986, RTSP :8586, authentification active)"
# Inline config: no config file, so the go2rtc API cannot rewrite it.
config="$(cat /tmp/go2rtc.json)"
rm -f /tmp/go2rtc.json
exec /usr/local/bin/go2rtc -config "${config}"
